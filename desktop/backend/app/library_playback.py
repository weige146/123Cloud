"""影库播放编排：海报墙点播 → 把当季文件秒传进网盘播放缓存目录 → 本地播放器拉 m3u，
每一集开播时由本机服务 302 到 123 直链（直链按需解析 + 55 秒缓存，不怕过期）。

与 123pan-strm-docker 的 /play 思路同源：播放列表永远指向本机服务，而不是 123 直链本身。

播放进度：mpv / IINA 用 --input-ipc-server 回传（集内秒数、自动判看完）；
VLC 等无回传播放器退化为「知道从哪集开播 + 手动标记已看」。
看完（回传 ≥92% / 切到下一集 / 手动标记）→ 自动把网盘里那一集移入 123 回收站。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import movie_library_db
from .movie_library import etag_hex, order_play_items, season_episode_label

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 24 * 3600
MAX_SESSIONS = 30
LINK_CACHE_TTL = 55.0
# 播放到这个比例即视为看完（回传判定）
WATCHED_PERCENT = 92.0
WATCHED_RATIO = 0.92
# 断点续播的最小起点（低于 30 秒从头播）
RESUME_MIN_SECONDS = 30.0
# 播放转存默认落在网盘原有的「秒传」目录（维护者 2026-09-18 指定：不新建文件夹），
# 影库设置里仍可改成别的目录
DEFAULT_PLAY_CACHE_PATH = "秒传"
LEGACY_PLAY_CACHE_PATH = "影库播放"

_PLAYER_LABELS = {"iina": "IINA", "mpv": "mpv", "vlc": "VLC", "infuse": "Infuse", "other": "自定义播放器"}

# 各平台自动检测顺序：IINA（macOS 上最像 Emby）→ mpv（IPC 最可靠）→ VLC
_DARWIN_PLAYER_PATHS = (
    ("iina", "/Applications/IINA.app/Contents/MacOS/iina-cli"),
    ("iina", "/usr/local/bin/iina-cli"),
    ("iina", "/opt/homebrew/bin/iina-cli"),
    ("mpv", "/opt/homebrew/bin/mpv"),
    ("mpv", "/usr/local/bin/mpv"),
    ("vlc", "/Applications/VLC.app/Contents/MacOS/VLC"),
)
_UNIX_PLAYER_NAMES = (("mpv", "mpv"), ("vlc", "vlc"), ("iina", "iina-cli"))
_WINDOWS_PLAYER_NAMES = (("mpv", "mpv.exe"), ("vlc", "vlc.exe"))


def _player_kind_from_name(path: str) -> str:
    base = os.path.basename(str(path or "")).lower()
    if "iina" in base:
        return "iina"
    if "mpv" in base:
        return "mpv"
    if "vlc" in base:
        return "vlc"
    return "other"


async def web_download_url(author_token: str, login_uuid: str = "", *,
                           file_id: int, etag: str, s3_key_flag: str = "",
                           size: int = 0, file_name: str = "") -> str:
    """网页版取直链（POST yun.123pan.com/b/api/file/download_info）。

    与 OpenAPI download_info 不同：吃 authorToken（网页登录态，油猴「推送登录会话到客户端」存档的），
    且要带 etag/s3KeyFlag 等文件信息——OpenAPI 直链失败（频控/权限等）时的备胎。
    123 的 DownloadInfo 对播放器拉流更友好（同 123pan-strm-docker 的做法）。"""
    import httpx
    headers = {
        "platform": "web",
        "app-version": "46",
        "accept-language": "zh-CN",
        "authorization": f"Bearer {str(author_token or '').strip()}",
    }
    if str(login_uuid or "").strip():
        headers["loginuuid"] = str(login_uuid).strip()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://yun.123pan.com/b/api/file/download_info",
            json={
                "driveId": 0,
                "etag": str(etag or "").lower(),
                "fileId": int(file_id or 0),
                "s3keyFlag": str(s3_key_flag or ""),
                "type": 0,
                "fileName": str(file_name or ""),
                "size": int(size or 0),
            },
            headers=headers,
        )
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        code = payload.get("code")
        url = ""
        data = payload.get("data")
        if isinstance(data, dict):
            url = str(data.get("DownloadUrl") or data.get("downloadUrl") or "")
        if resp.status_code == 200 and code in (0, "0") and url:
            return url
        raise ValueError(f"123 网页接口取直链失败（code={code}）：{payload.get('message') or resp.status_code}")


def detect_player(custom_path: str = "") -> Dict[str, str]:
    """找本地播放器：配置了路径优先，否则按平台常见位置自动检测。
    custom_path 三种形态：可执行文件路径 / macOS .app 应用包（如 Infuse）/ 「system」= 交给系统默认程序打开。
    返回 {"kind": "iina"|"mpv"|"vlc"|"app"|"system"|"other"|"", "path": 路径, "label": 显示名}。"""
    custom = str(custom_path or "").strip()
    if custom:
        if custom.lower() == "system":
            system = platform.system()
            if system == "Windows":
                return {"kind": "system", "path": "cmd", "label": "系统默认播放器"}
            if system == "Darwin":
                return {"kind": "system", "path": "/usr/bin/open", "label": "系统默认播放器"}
            return {"kind": "system", "path": shutil.which("xdg-open") or "xdg-open", "label": "系统默认播放器"}
        if os.path.isdir(custom):
            # macOS 直接选 /Applications 里的 .app（文件选择框里它们是目录）
            if custom.endswith(".app") and platform.system() == "Darwin":
                stem = os.path.basename(custom[:-4])
                # IINA 不接收 open -a 方式给的网络流（会报「找不到流」）：找到内置 iina-cli 就用它，
                # 既修播放又恢复进度回传
                if stem.lower() == "iina":
                    cli = os.path.join(custom, "Contents", "MacOS", "iina-cli")
                    if os.path.isfile(cli):
                        return {"kind": "iina", "path": cli, "label": "IINA"}
                if stem.lower() == "infuse":
                    # Infuse 不接受播放列表文件/命令行 URL（实测都直接拒绝），
                    # 只认它自家的跳转协议单条流地址：走单集模式（见 start_play）
                    return {"kind": "infuse", "path": custom, "label": "Infuse"}
                if os.path.isfile(os.path.join(custom, "Contents", "MacOS", stem)):
                    return {"kind": "app", "path": custom, "label": stem}
            return {"kind": "", "path": "", "label": ""}
        if os.path.isfile(custom) or shutil.which(custom):
            kind = _player_kind_from_name(custom)
            if kind == "other":
                # 不认识的播放器（PotPlayer 等）：显示名用程序文件名
                label = os.path.splitext(os.path.basename(custom))[0] or "自定义播放器"
            else:
                label = _PLAYER_LABELS[kind]
            return {"kind": kind, "path": custom, "label": label}
        return {"kind": "", "path": "", "label": ""}
    system = platform.system()
    if system == "Darwin":
        for kind, path in _DARWIN_PLAYER_PATHS:
            if os.path.isfile(path):
                return {"kind": kind, "path": path, "label": _PLAYER_LABELS[kind]}
        return {"kind": "", "path": "", "label": ""}
    names = _WINDOWS_PLAYER_NAMES if system == "Windows" else _UNIX_PLAYER_NAMES
    for kind, name in names:
        found = shutil.which(name)
        if found:
            return {"kind": kind, "path": found, "label": _PLAYER_LABELS[kind]}
    return {"kind": "", "path": "", "label": ""}


def ipc_socket_path(session_id: str) -> str:
    """mpv IPC 通道路径：unix socket / Windows 命名管道。"""
    if platform.system() == "Windows":
        return "\\\\.\\pipe\\c123play-" + session_id
    return os.path.join(tempfile.gettempdir(), f"c123-play-{session_id}.sock")


def build_player_command(player: Dict[str, str], playlist_url: str, sock_path: str = "",
                         start_position_sec: float = 0.0) -> List[str]:
    """组装播放器启动命令。mpv/IINA 带 IPC（进度回传），带断点续播秒数；
    app（.app 应用包如 Infuse）/ system（系统默认）走 open，没有进度回传。"""
    kind, path = player.get("kind") or "other", player.get("path") or ""
    resume = int(start_position_sec) if start_position_sec and start_position_sec >= RESUME_MIN_SECONDS else 0
    if kind == "mpv":
        # --ytdl=no：我们的地址是普通 http 直链/播放列表，不需要 youtube-dl 解析；
        # IINA 内置的 youtube-dl 在部分机器上挂死，会把 mpv 的 loadfile 卡在 hook 阶段（实测）
        command = [path, "--no-terminal", "--ytdl=no", f"--input-ipc-server={sock_path}"]
        if resume:
            command.append(f"--start={resume}")
    elif kind == "iina":
        # iina-cli 用 --mpv-<option> 透传 mpv 参数（IINA 官方 CLI 约定）
        command = [path, "--no-stdin", "--mpv-ytdl=no", f"--mpv-input-ipc-server={sock_path}"]
        if resume:
            command.append(f"--mpv-start={resume}")
    elif kind == "vlc":
        command = [path]
        if resume:
            command.append(f"--start-time={resume}")
    elif kind == "app":
        command = ["open", "-a", path]
        if resume:
            command.extend(["--args", f"--start={resume}"])
    elif kind == "system":
        command = [path]
        if platform.system() == "Windows":
            command = ["cmd", "/c", "start", "", playlist_url]
            return command
    else:
        command = [path]
    command.append(playlist_url)
    return command


# ---- mpv IPC 通道（unix socket / Windows 命名管道的统一读写封装） ----

class _UnixSocketChannel:
    def __init__(self, path: str, timeout: float):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(path)
        self._buf = b""

    def send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def readline(self) -> Optional[bytes]:
        """读到一行；连接被对端关闭（播放器退出）返回 None。"""
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                return b""
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class _NamedPipeChannel:
    """Windows 命名管道按文件打开读写（mpv --input-ipc-server=\\\\.\\pipe\\name）。"""

    def __init__(self, path: str, timeout: float):
        del timeout
        self._file = open(path, "r+b", buffering=0)
        self._buf = b""

    def send(self, data: bytes) -> None:
        self._file.write(data)

    def readline(self) -> Optional[bytes]:
        while b"\n" not in self._buf:
            chunk = self._file.read(4096)
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    return None if number is None else int(number)


# 请求 ID 约定：1=当前播放条目序号 2=条目内秒数 3=总时长 4=百分比
_IPC_REQUESTS = (
    (1, "playlist-playing-pos"),
    (2, "time-pos"),
    (3, "duration"),
    (4, "percent-pos"),
)


class MpvIpcMonitor(threading.Thread):
    """mpv/IINA 进度监听（独立线程、阻塞读写）。

    每个轮询周期发 4 个 get_property，读齐回应后交给 _apply_poll（纯逻辑，
    测试可直接驱动不碰 socket）。判定看完的两条路径：
    ① 播放百分比 ≥ 92%；② 当前条目序号往前走（切下一集）⇒ 上一集视为看完。
    socket 断开（播放器退出）→ 结束监听。
    """

    def __init__(self, sock_path: str, items: List[Dict[str, Any]], *,
                 on_update: Callable[[Dict[str, Any], float, float], None],
                 on_watched: Callable[[Dict[str, Any]], None],
                 on_exit: Callable[[], None],
                 poll_interval: float = 2.0, connect_timeout: float = 20.0):
        super().__init__(daemon=True, name="library-play-ipc")
        self._sock_path = str(sock_path or "")
        self._items = items or []
        self._on_update = on_update
        self._on_watched = on_watched
        self._on_exit = on_exit
        self._poll_interval = max(0.5, float(poll_interval))
        self._connect_timeout = float(connect_timeout)
        self._index: Optional[int] = None
        self._position = 0.0
        self._duration = 0.0

    # ---- 纯逻辑（测试入口） ----
    def _apply_poll(self, values: Dict[int, Any]) -> None:
        index = _as_int(values.get(1))
        if index is None or not 0 <= index < len(self._items):
            return
        position = _as_float(values.get(2)) or 0.0
        duration = _as_float(values.get(3)) or 0.0
        percent = _as_float(values.get(4))
        prev = self._index
        if prev is not None and prev != index and 0 <= prev < len(self._items):
            self._fire_watched(prev)
        self._index = index
        self._position, self._duration = position, duration
        if self._on_update:
            try:
                self._on_update(self._items[index], position, duration)
            except Exception as error:
                logger.warning("影库播放：记录进度失败 %s", error)
        watched_now = (
            (percent is not None and percent >= WATCHED_PERCENT)
            or (duration > 0 and position / duration >= WATCHED_RATIO)
        )
        if watched_now:
            self._fire_watched(index)

    def _fire_watched(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        if item.get("_watchedFired"):
            return
        item["_watchedFired"] = True
        if self._on_watched:
            try:
                self._on_watched(item)
            except Exception as error:
                logger.warning("影库播放：标记已看回调失败 %s", error)

    # ---- 线程主体 ----
    def run(self) -> None:
        channel = self._connect()
        if channel is None:
            self._finish()
            return
        try:
            while True:
                responses = self._poll_once(channel)
                if responses is None:
                    break
                if responses:
                    self._apply_poll(responses)
                time.sleep(self._poll_interval)
        finally:
            channel.close()
            self._finish()

    def _connect(self):
        deadline = time.time() + self._connect_timeout
        while time.time() < deadline:
            try:
                if self._sock_path.startswith("\\\\.\\pipe\\"):
                    return _NamedPipeChannel(self._sock_path, 3.0)
                return _UnixSocketChannel(self._sock_path, max(3.0, self._poll_interval * 2))
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                time.sleep(1.0)  # 播放器还在启动，等 IPC 通道建好
        logger.info("影库播放：等了 %.0f 秒也没连上播放器进度通道，本次只记录开播集数", self._connect_timeout)
        return None

    def _poll_once(self, channel) -> Optional[Dict[int, Any]]:
        """发一轮查询、收齐回应；对端关闭返回 None（结束监听）。"""
        try:
            payload = "".join(
                json.dumps({"command": ["get_property", prop], "request_id": rid}) + "\n"
                for rid, prop in _IPC_REQUESTS)
            channel.send(payload.encode("utf-8"))
        except Exception:
            return None
        values: Dict[int, Any] = {}
        while len(values) < len(_IPC_REQUESTS):
            line = channel.readline()
            if line is None:
                return None
            if not line:
                return values or None
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                continue
            request_id = message.get("request_id")
            if isinstance(request_id, int):
                values[request_id] = message.get("data") if message.get("error") == "success" else None
        return values

    def _finish(self) -> None:
        if self._on_exit:
            try:
                self._on_exit()
            except Exception:
                pass


class PlaybackService:
    """播放编排：转存 → 会话/播放列表 → 启动播放器 → 进度回传 → 看完移回收站。"""

    def __init__(self, client_provider: Optional[Callable[[], Any]] = None,
                 config_provider: Optional[Callable[[], Dict[str, Any]]] = None,
                 db: Any = movie_library_db, spawn=subprocess.Popen,
                 web_session_provider: Optional[Callable[[], Any]] = None,
                 data_dir: str = ""):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._data_dir = str(data_dir or os.environ.get("DATA_DIR") or "")
        self._client_provider = client_provider
        self._config_provider = config_provider
        self._db = db
        self._spawn = spawn
        # 网页登录态存档（油猴「推送登录会话到客户端」），OpenAPI 直链失败时的备胎凭据
        self._web_session_provider = web_session_provider
        # Python 3.9 的 Lock 在构造时会绑事件循环，必须懒建（首次 start_play 时）
        self._lock: Optional[asyncio.Lock] = None
        self._link_cache: Dict[int, tuple] = {}

    # ---- 配置 / 客户端 ----
    def _get_config(self) -> Dict[str, Any]:
        try:
            if self._config_provider:
                return dict(self._config_provider() or {})
        except Exception as error:
            logger.warning("影库播放：读取配置失败 %s", error)
        return {}

    async def _get_client(self) -> Any:
        if self._client_provider is None:
            raise ValueError("123 网盘未授权，请先在客户端设置里完成 123 网盘授权")
        return await self._client_provider()

    # ---- 起播 ----
    async def start_play(self, dir_name: str, season: Optional[int] = None, episode: Optional[int] = None,
                         file_path: str = "", resume: bool = False, base_url: str = "") -> Dict[str, Any]:
        dir_name = str(dir_name or "").strip()
        if not dir_name:
            raise ValueError("缺少作品目录")
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            info = await asyncio.to_thread(self._db.list_files, dir_name)
            if info is None:
                raise ValueError("作品不存在（可能已被删除，请重新搜索）")
            title = str(info.get("title") or "")
            year = info.get("year")
            ordered = order_play_items(info.get("files") or [])
            is_series = bool(ordered.get("isSeries"))
            if is_series:
                seasons: Dict[int, List[Dict[str, Any]]] = ordered["seasons"]
                if not seasons:
                    raise ValueError("这个作品没有可播放的视频文件")
                season_no = int(season or 0) or min(seasons)
                if season_no not in seasons:
                    available = "、".join(f"第 {s} 季" for s in sorted(seasons))
                    raise ValueError(f"影库里没有第 {season_no} 季（现有：{available}）")
                entries = list(seasons[season_no])
            else:
                standalone = ordered.get("standalone")
                if not standalone:
                    raise ValueError("这个作品没有可播放的视频文件")
                season_no = 0
                entries = [{"season": 0, "episode": 0, "file": standalone, "alternates": []}]

            # 指定文件（电影选版本/集列表选具体文件）：定位后顶成起点；剧集跨季找
            start_hint = -1
            file_path = str(file_path or "").strip()
            if file_path and not is_series:
                all_videos = [f for f in (info.get("files") or []) if isinstance(f, dict) and f.get("isVideo")]
                hit = next((f for f in all_videos
                            if f.get("path") == file_path or f.get("fileName") == file_path), None)
                if hit is None:
                    raise ValueError("找不到要播放的文件")
                entries = [{"season": 0, "episode": 0, "file": hit, "alternates": []}]
                start_hint = 0
            elif file_path:
                for season_key, season_entries in seasons.items():
                    for i, entry in enumerate(season_entries):
                        hit = next((c for c in [entry["file"], *(entry.get("alternates") or [])]
                                    if c.get("path") == file_path or c.get("fileName") == file_path), None)
                        if hit is None:
                            continue
                        if season_key != season_no:
                            season_no = season_key
                            entries = list(season_entries)
                        promoted = dict(entry)
                        promoted["file"] = hit
                        entries[i] = promoted
                        start_hint = i
                        break
                    if start_hint >= 0:
                        break
                if start_hint < 0:
                    raise ValueError("找不到要播放的文件")
            elif episode:
                target = int(episode)
                start_hint = next((i for i, e in enumerate(entries) if int(e.get("episode") or 0) >= target), 0)

            # 续播：海报墙默认播放时跳过已看前缀，从第一个没看完的集开始
            records = await asyncio.to_thread(self._db.list_playback, dir_name)
            by_path = {str(r.get("filePath") or ""): r for r in records}
            start_index = start_hint if start_hint >= 0 else 0
            start_position = 0.0
            if start_hint < 0 and resume:
                for i, entry in enumerate(entries):
                    record = by_path.get(str(entry["file"].get("path") or ""))
                    if record is None or not record.get("watched"):
                        start_index = i
                        position = float(record.get("positionSec") or 0) if record else 0.0
                        duration = float(record.get("durationSec") or 0) if record else 0.0
                        if position >= RESUME_MIN_SECONDS and (not duration or position / duration < WATCHED_RATIO):
                            start_position = position
                        break

            items_src = entries[start_index:]
            if not items_src:
                raise ValueError("没有可播放的剧集")

            # 播放器先确认，再动网盘：没装播放器立即报错，不让用户等一场空转存
            cfg = self._get_config()
            player = detect_player(cfg.get("playerPath"))
            if not player.get("path"):
                raise ValueError(
                    "没找到本地播放器：请安装 IINA / mpv / VLC，或在影库设置 → 播放里手动选择播放器（也可选系统默认）"
                )
            # Infuse 不接受播放列表/命令行地址（实测直接拒绝）：降级单集模式，只转存并播起播那一集
            single_only = player["kind"] == "infuse"
            if single_only:
                items_src = items_src[:1]

            client = await self._get_client()
            cache_root = str(cfg.get("playCachePath") or "").strip() or DEFAULT_PLAY_CACHE_PATH
            work_folder = f"{title} ({year})" if year else title
            folder_parts = [cache_root, work_folder]
            if is_series:
                folder_parts.append(f"Season {season_no:02d}")
            parent_id = await client.ensure_path("0", folder_parts)
            existing = {
                str(f.get("name") or ""): f
                for f in (await client.list_files(parent_id))
                if int(f.get("type") or 0) != 1
            }

            interval = max(0, int(cfg.get("transferIntervalMs") or 200)) / 1000.0
            playlist_items: List[Dict[str, Any]] = []
            transferred = reused = 0
            missed: List[str] = []
            for entry in items_src:
                f = entry["file"]
                name = str(f.get("fileName") or "")
                size = int(f.get("size") or 0)
                hit = existing.get(name)
                cloud_id = 0
                if hit is not None and int(hit.get("size") or 0) == size:
                    cloud_id = int(hit.get("fileId") or hit.get("id") or 0)
                    reused += 1
                elif hit is not None:
                    missed.append(f"{name}（网盘里同名文件体积对不上，已跳过）")
                    continue
                else:
                    hex_md5 = etag_hex(f.get("etag"))
                    if not hex_md5 or not size:
                        missed.append(f"{name}（缺少秒传信息，无法转存）")
                        continue
                    try:
                        cloud_id = int(await client.md5_reuse(parent_id, name, hex_md5, size) or 0)
                    except Exception as error:
                        if int(getattr(error, "code", 0) or 0) in (401, 403):
                            raise ValueError(f"123 网盘授权已失效，请重新授权：{error}")
                        missed.append(f"{name}（转存失败：{error}）")
                        continue
                    if not cloud_id:
                        missed.append(f"{name}（秒传未命中，123 里已经没有这个文件）")
                        continue
                    transferred += 1
                    if interval:
                        await asyncio.sleep(interval)
                file_path_key = str(f.get("path") or name)
                playlist_items.append({
                    "index": len(playlist_items),
                    "path": file_path_key,
                    "fileName": name,
                    "season": int(entry.get("season") or 0),
                    "episode": int(entry.get("episode") or 0),
                    "size": size,
                    "etag": str(f.get("etag") or ""),
                    "s3KeyFlag": str(f.get("s3KeyFlag") or ""),
                    "cloudFileId": cloud_id,
                })
            # 播放记录只落真正交给播放器的第一集：整季每集都写会让「续看」误跳到最新一集，
            # 没播到的集继续没记录，看完/进度仍由 IPC 回传或手动标记逐集落库
            if playlist_items:
                first_item = playlist_items[0]
                try:
                    self._db.upsert_playback(dir_name, first_item["path"],
                                             first_item["season"], first_item["episode"],
                                             cloud_file_id=first_item["cloudFileId"])
                except Exception as error:
                    logger.warning("影库播放：写播放记录失败 %s", error)
            if not playlist_items:
                detail = "；".join(missed[:3]) + ("…" if len(missed) > 3 else "")
                raise ValueError(f"没有文件能转存进网盘：{detail}")

            # 同一部剧再点播放：先关掉上一次的播放器窗口（新播放顶掉旧播放，不堆积窗口）
            for old_session in list(self.sessions.values()):
                if old_session.get("dir") == dir_name and old_session.get("alive"):
                    self._stop_session(old_session)
            self._prune_sessions()
            session_id = secrets.token_urlsafe(16)
            sock_path = ipc_socket_path(session_id) if player["kind"] in ("mpv", "iina") else ""
            base = str(base_url or "").rstrip("/") or f"http://127.0.0.1:{os.environ.get('CLOUD123_PORT') or 8000}"
            # m3u 落成本地文件交给播放器：IINA/Infuse 对「命令行直接给 http 地址」的打开路径不可靠
            # （实测 IINA 收到 URL 后从不发起请求），本地文件走它们最成熟的文档打开路径；
            # 文件里的条目仍是本机服务地址，播放器内核逐项拉取（302 取直链不受影响）
            entry_url = f"{base}/api/library/play/{session_id}/0"
            playlist_note = ""
            if single_only:
                # Infuse 官方跳转协议：infuse://x-callback-url/play?url=<编码后的流地址>
                from urllib.parse import quote
                command = ["open", "infuse://x-callback-url/play?url=" + quote(entry_url, safe="")]
                playlist_url = ""
                playlist_note = "Infuse 暂不支持连播列表，本集播完请回影库继续下一集"
            else:
                playlist_url = self._write_playlist_file(session_id, title, [
                    (season_episode_label(i["season"], i["episode"]) or i["fileName"], f"{base}/api/library/play/{session_id}/{i['index']}")
                    for i in playlist_items
                ])
                command = build_player_command(player, playlist_url, sock_path, start_position)
            popen_kwargs: Dict[str, Any] = {}
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            try:
                process = self._spawn(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kwargs)
            except Exception as error:
                raise ValueError(f"启动播放器失败（{player['label']}）：{error}")

            first = playlist_items[0]
            start_label = season_episode_label(first["season"], first["episode"]) or first["fileName"]
            session = {
                "sessionId": session_id,
                "dir": dir_name,
                "title": title,
                "season": season_no,
                "isSeries": is_series,
                "items": playlist_items,
                "baseUrl": base,
                "createdAt": time.time(),
                "alive": True,
                "currentIndex": 0,
                "positionSec": start_position,
                "playerKind": player["kind"],
                "playerLabel": player["label"],
                "sockPath": sock_path,
                "playlistFile": playlist_url,
                "_popen": process,
            }
            self.sessions[session_id] = session
            if sock_path:
                self._start_monitor(session)

            logger.info(
                "影库播放：《%s》从 %s 开始交给 %s（本次转存 %d 个、网盘已有 %d 个%s）；%s：%s",
                title, start_label, player["label"], transferred, reused,
                "，秒传未命中 " + str(len(missed)) + " 个" if missed else "",
                "单集地址" if single_only else "播放列表文件",
                playlist_url or entry_url,
            )
            return {
                "sessionId": session_id,
                "player": player["kind"],
                "playerLabel": player["label"],
                "title": title,
                "season": season_no,
                "startLabel": start_label,
                "itemCount": len(playlist_items),
                "transferred": transferred,
                "reused": reused,
                "missed": missed[:20],
                "resumeSeconds": round(start_position),
                "note": playlist_note,
            }

    def _stop_session(self, session: Dict[str, Any]) -> None:
        """结束一次播放：优雅退出播放器（IPC quit / 终止进程）并清掉它的播放列表文件。"""
        session["alive"] = False
        sock_path = str(session.get("sockPath") or "")
        if sock_path:
            # IINA 场景 spawn 的是 iina-cli（它立即退出、真正播放的是 IINA 主程序），
            # terminate 杀不到，走 IPC 让 mpv 核心优雅退出 → 窗口关闭
            threading.Thread(target=self._ipc_quit, args=(sock_path,), daemon=True).start()
        popen = session.get("_popen")
        if popen is not None:
            try:
                popen.terminate()
            except Exception:
                pass
        playlist_file = str(session.get("playlistFile") or "")
        if playlist_file:
            try:
                os.remove(playlist_file)
            except OSError:
                pass

    @staticmethod
    def _ipc_quit(sock_path: str) -> None:
        try:
            if sock_path.startswith("\\\\.\\pipe\\"):
                channel = _NamedPipeChannel(sock_path, 2.0)
            else:
                channel = _UnixSocketChannel(sock_path, 2.0)
            try:
                channel.send(b'{"command": ["quit"]}\n')
                time.sleep(0.5)
            finally:
                channel.close()
        except Exception:
            pass  # 播放器可能已经退了

    def _start_monitor(self, session: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            session["alive"] = False
            return
        items = session["items"]

        def on_update(item: Dict[str, Any], position: float, duration: float) -> None:
            session["currentIndex"] = item["index"]
            session["positionSec"] = position
            try:
                self._db.upsert_playback(session["dir"], item["path"], item["season"], item["episode"],
                                         position_sec=round(position, 1), duration_sec=round(duration, 1))
            except Exception as error:
                logger.warning("影库播放：写播放进度失败 %s", error)

        def on_watched(item: Dict[str, Any]) -> None:
            asyncio.run_coroutine_threadsafe(self._handle_watched(session, item), loop)

        def on_exit() -> None:
            session["alive"] = False

        monitor = MpvIpcMonitor(session["sockPath"], items, on_update=on_update,
                                on_watched=on_watched, on_exit=on_exit)
        session["_monitor"] = monitor
        monitor.start()

    async def _handle_watched(self, session: Dict[str, Any], item: Dict[str, Any]) -> None:
        try:
            self._db.upsert_playback(session["dir"], item["path"], item["season"], item["episode"],
                                     watched=True, position_sec=0)
        except Exception as error:
            logger.warning("影库播放：写已看记录失败 %s", error)
        await self._trash_item_file(session, item)

    async def _trash_item_file(self, session: Dict[str, Any], item: Dict[str, Any]) -> None:
        """看完把网盘里的这一集移入回收站（可在影库设置关闭）。只动自己转存进缓存目录的文件。"""
        cloud_id = int(item.get("cloudFileId") or 0)
        if not cloud_id:
            return
        if not self._get_config().get("autoTrash", True):
            return
        try:
            client = await self._get_client()
            await client.trash_files([cloud_id])
            item["cloudFileId"] = 0
            self._db.upsert_playback(session["dir"], item["path"], cloud_file_id=0)
            logger.info("影库播放：《%s》%s 看完，已把网盘里的文件移入回收站",
                        session.get("title") or "", season_episode_label(item["season"], item["episode"]) or item["fileName"])
        except Exception as error:
            logger.warning("影库播放：移入回收站失败（%s）：%s", item.get("fileName"), error)

    # ---- 播放列表 / 直链 ----
    def _live_session(self, session_id: str, allow_dead: bool = False) -> Dict[str, Any]:
        session = self.sessions.get(str(session_id or ""))
        if session is None:
            raise ValueError("播放会话不存在或已过期，请回到影库重新点播")
        if time.time() - float(session.get("createdAt") or 0) > SESSION_TTL_SECONDS:
            raise ValueError("播放会话已过期（超过 24 小时），请回到影库重新点播")
        if not allow_dead and not session.get("alive", True):
            raise ValueError("这次播放已经结束，请回到影库重新点播")
        return session

    def _write_playlist_file(self, session_id: str, title: str, entries: List[tuple]) -> str:
        """把播放列表写成本地 .m3u 文件（给播放器「文档打开」用），返回文件路径。"""
        base_dir = os.path.join(self._data_dir or tempfile.gettempdir(), "playlists")
        os.makedirs(base_dir, exist_ok=True)
        fpath = os.path.join(base_dir, f"{session_id}.m3u")
        lines = ["#EXTM3U"]
        for label, url in entries:
            lines.append(f"#EXTINF:-1,{title} {label}".replace("//", "/"))
            lines.append(url)
        # utf-8-sig（带 BOM）：Windows 播放器（PotPlayer/VLC）对含中文标题的 m3u 认 BOM 才稳
        with open(fpath, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(lines) + "\n")
        return fpath

    def playlist_m3u(self, session_id: str) -> Optional[str]:
        try:
            session = self._live_session(session_id, allow_dead=True)
        except ValueError:
            return None
        lines = ["#EXTM3U"]
        for item in session["items"]:
            label = season_episode_label(item["season"], item["episode"])
            label = f"{session['title']} {label} · {item['fileName']}" if label else item["fileName"]
            lines.append(f"#EXTINF:-1,{label}")
            lines.append(f"{session['baseUrl']}/api/library/play/{session['sessionId']}/{item['index']}")
        return "\n".join(lines) + "\n"

    async def resolve_item(self, session_id: str, index: int) -> str:
        """按需取 123 直链：先走 OpenAPI download_info，失败降级网页版接口（authorToken）。
        播放器只看到 302，哪条路成功对它透明。"""
        session = self._live_session(session_id, allow_dead=True)
        item = next((i for i in session["items"] if int(i["index"]) == int(index)), None)
        if item is None:
            raise ValueError("播放条目不存在")
        cloud_id = int(item.get("cloudFileId") or 0)
        if not cloud_id:
            raise ValueError("这一集不在网盘里（可能已随看完移入回收站），请回到影库重新点播")
        cached = self._link_cache.get(cloud_id)
        if cached and time.time() - cached[0] < LINK_CACHE_TTL:
            return cached[1]
        openapi_error = web_error = None
        url = ""
        try:
            client = await self._get_client()
            url = str(await client.download_info(cloud_id))
        except Exception as error:
            openapi_error = error
            logger.warning("影库播放：OpenAPI 取直链失败（%s 第 %s 项，fileId=%s）：%s；改试网页版接口",
                           session.get("title") or "", index, cloud_id, error)
        if not url:
            url = await self._web_fallback_url(item)
            if not url:
                web_error = "网页登录态不可用或网页接口也未取到直链"
        if not url:
            raise ValueError(f"取不到 123 直链：OpenAPI（{openapi_error}）；网页接口（{web_error}）")
        logger.info("影库播放：%s 第 %s 项直链就绪（%s）",
                    session.get("title") or "", index,
                    "OpenAPI" if not openapi_error else "网页接口")
        self._link_cache[cloud_id] = (time.time(), url)
        return url

    async def _web_fallback_url(self, item: Dict[str, Any]) -> str:
        """网页版接口兜底：需要油猴「推送登录会话到客户端」存档的网页登录态。"""
        session_payload: Any = None
        if self._web_session_provider:
            try:
                session_payload = self._web_session_provider()
            except Exception as error:
                logger.warning("影库播放：读取网页登录态存档失败 %s", error)
        if not isinstance(session_payload, dict) or not str(session_payload.get("authorToken") or "").strip():
            return ""
        try:
            return await web_download_url(
                str(session_payload.get("authorToken")),
                str(session_payload.get("loginUuid") or ""),
                file_id=int(item.get("cloudFileId") or 0),
                etag=str(item.get("etag") or ""),
                s3_key_flag=str(item.get("s3KeyFlag") or ""),
                size=int(item.get("size") or 0),
                file_name=str(item.get("fileName") or ""),
            )
        except Exception as error:
            logger.warning("影库播放：网页接口取直链失败（%s）：%s", item.get("fileName"), error)
            return ""

    def _prune_sessions(self) -> None:
        now = time.time()
        for sid in [s for s, v in self.sessions.items()
                    if now - float(v.get("createdAt") or 0) > SESSION_TTL_SECONDS]:
            session = self.sessions.pop(sid, None)
            if session and session.get("playlistFile"):
                try:
                    os.remove(session["playlistFile"])
                except OSError:
                    pass
        if len(self.sessions) < MAX_SESSIONS:
            return
        ordered = sorted(self.sessions.items(), key=lambda kv: float(kv[1].get("createdAt") or 0))
        for sid, _ in ordered[:len(self.sessions) - MAX_SESSIONS + 1]:
            self.sessions.pop(sid, None)

    # ---- 进度查询 / 手动标记 ----
    def active_for(self, dir_name: str) -> Optional[Dict[str, Any]]:
        for session in self.sessions.values():
            if session.get("dir") != dir_name or not session.get("alive"):
                continue
            item = next((i for i in session["items"] if int(i.get("index") or 0) == int(session.get("currentIndex") or 0)), None)
            return {
                "sessionId": session["sessionId"],
                "playerLabel": session.get("playerLabel") or "",
                "fileName": item["fileName"] if item else "",
                "season": int(item["season"]) if item else int(session.get("season") or 0),
                "episode": int(item["episode"]) if item else 0,
                "positionSec": round(float(session.get("positionSec") or 0)),
            }
        return None

    async def progress_payload(self, dir_name: str) -> Dict[str, Any]:
        records = await asyncio.to_thread(self._db.list_playback, dir_name)
        return {"records": records, "active": self.active_for(dir_name)}

    async def mark_watched(self, dir_name: str, file_path: str, watched: bool) -> None:
        dir_name = str(dir_name or "").strip()
        file_path = str(file_path or "").strip()
        if not dir_name or not file_path:
            raise ValueError("缺少作品目录或文件路径")
        from .movie_library import parse_season_episode
        season_no, episode_no = parse_season_episode(file_path)
        if episode_no is None:
            season_no = episode_no = 0
        record = await asyncio.to_thread(self._db.get_playback, dir_name, file_path)
        if record is None:
            self._db.upsert_playback(dir_name, file_path, season_no, episode_no, watched=watched)
            return
        if watched and not record.get("watched"):
            self._db.upsert_playback(dir_name, file_path,
                                     int(record.get("season") or season_no),
                                     int(record.get("episode") or episode_no),
                                     watched=True, position_sec=0)
            session = {"dir": dir_name, "title": ""}
            item = {"path": file_path, "season": int(record.get("season") or 0),
                    "episode": int(record.get("episode") or 0),
                    "fileName": file_path.rsplit("/", 1)[-1],
                    "cloudFileId": int(record.get("cloudFileId") or 0)}
            await self._trash_item_file(session, item)
        else:
            self._db.upsert_playback(dir_name, file_path, watched=watched)

    async def mark_watched_until(self, dir_name: str, season: int, episode: int) -> Dict[str, Any]:
        """「看到第 N 集」：该季 1..N 标已看、N 之后标未看；新标已看且已转存的文件照常移回收站。
        以作品全集列表为准（没播放记录的集也会补上记录）。返回各动作条数供提示。"""
        dir_name = str(dir_name or "").strip()
        season, episode = int(season or 0), int(episode or 0)
        if not dir_name or season <= 0 or episode <= 0:
            raise ValueError("请指定作品与要标记到的集数")
        info = await asyncio.to_thread(self._db.list_files, dir_name)
        if info is None:
            raise ValueError("作品不存在（可能已被删除，请重新搜索）")
        ordered = order_play_items(info.get("files") or [])
        entries = (ordered.get("seasons") or {}).get(season) or []
        if not entries:
            raise ValueError(f"这个作品没有第 {season} 季")
        marked_watched = marked_unwatched = 0
        keep_paths: List[str] = []
        for entry in entries:
            f = entry.get("file") or {}
            path = str(f.get("path") or "")
            if not path:
                continue
            if int(entry.get("episode") or 0) <= episode:
                keep_paths.append(path)
            else:
                record = await asyncio.to_thread(self._db.get_playback, dir_name, path)
                if record is not None and record.get("watched"):
                    self._db.upsert_playback(dir_name, path, season, int(entry.get("episode") or 0), watched=False)
                    marked_unwatched += 1
        cloud_ids = await asyncio.to_thread(self._db.watched_cloud_ids, dir_name, keep_paths)
        for entry in entries:
            f = entry.get("file") or {}
            path = str(f.get("path") or "")
            ep = int(entry.get("episode") or 0)
            if path and ep <= episode:
                record = await asyncio.to_thread(self._db.get_playback, dir_name, path)
                if record is None or not record.get("watched"):
                    self._db.upsert_playback(dir_name, path, season, ep,
                                             cloud_file_id=int(cloud_ids.get(path) or 0),
                                             watched=True, position_sec=0)
                    marked_watched += 1
        # 新标已看的集：把转存进网盘的文件移入回收站（开关关了就不动）
        trashed = 0
        if self._get_config().get("autoTrash", True) and cloud_ids:
            try:
                client = await self._get_client()
                await client.trash_files(list(cloud_ids.values()))
                trashed = len(cloud_ids)
                for path in cloud_ids:
                    self._db.upsert_playback(dir_name, path, cloud_file_id=0)
            except Exception as error:
                logger.warning("影库播放：批量标记已看后移回收站失败：%s", error)
        logger.info(
            "影库播放：《%s》手动标记看到第 %d 季第 %d 集（新标已看 %d、取消已看 %d、移回收站 %d 个文件）",
            str(info.get("title") or ""), season, episode, marked_watched, marked_unwatched, trashed,
        )
        return {"markedWatched": marked_watched, "markedUnwatched": marked_unwatched, "trashed": trashed}
