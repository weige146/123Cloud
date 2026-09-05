"""115→123 搬运管线（参照 tdr-123help 的主线重组）。

一次任务的执行被拆成六个阶段，每个阶段都会在任务日志里用大白话
说明"做了什么、进行到哪"，保证不懂技术的人也能看懂：

  1 解析   把 115 分享/本地目录展开成文件清单
  2 规划   每个目标 123 目录只列一次，先挑出已经存在、无需搬运的文件
  3 秒传   SHA1/MD5 秒传 + 断点恢复，能秒传的文件不用下载
  4 离线   秒传不了的取 115 直链，批量提交 123 离线下载
  5 等待   一个循环统一照看所有离线任务，以"列目录+大小比对"为完成依据
  6 收尾   汇总结果；按配置删除 115 源文件、清理空目录

与 tdr-123help 的差异（有意为之）：
- 撞 123 离线并发限制用有界指数退避（5/15/30/60 秒），不做 100 秒死等
- 秒传命中的 etag 记在本地 transfer_hash 表，不上报任何第三方服务器
- 保留本项目已有的多账号池、冷却换号、暂停窗口、断点恢复能力

本模块不 import TransferService，只通过构造传入的 service 对象回调
（保存任务、取配置、账号轮换等），避免循环依赖。
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

from .pan115_transfer import Pan115TransferClient
from .pan123 import Pan123OpenAPIClient

logger = logging.getLogger(__name__)

# 参照 tdr-123help：离线任务失败后自动重新提交的最大次数
TRANSFER_OFFLINE_RESUBMIT_MAX = 3
# 撞 123 "同时下载任务超出最大限制" 时的重试间隔（毫秒），有界指数退避
OFFLINE_SUBMIT_BACKOFF_MS = [5_000, 15_000, 30_000, 60_000]

PAN123_OFFLINE_SUBMIT_NAME_MAX = int(__import__("os").environ.get("PAN123_OFFLINE_SUBMIT_NAME_MAX", "180"))
PAN123_OFFLINE_DISPLAY_PATH_MAX = int(__import__("os").environ.get("PAN123_OFFLINE_DISPLAY_PATH_MAX", "240"))
PAN123_PATH_PART_MAX = int(__import__("os").environ.get("PAN123_PATH_PART_MAX", "180"))
PAN123_FORBIDDEN_NAME_RE = re.compile(r'["\\/:*?|><]')


class TaskCancelled(Exception):
    """任务在运行期间被用户删除，静默退出即可。"""


# ---------------------------------------------------------------------------
# 任务日志：写入任务记录（后台任务详情页可见）并同步到全局日志
# ---------------------------------------------------------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _add_task_log(task: Dict[str, Any], level: str, message: str) -> None:
    task.setdefault("logs", []).append({"time": _utc_now_iso(), "level": level, "message": message})
    task["logs"] = task["logs"][-200:]
    level_map = {"warn": "warning"}
    log_level = level_map.get(level, level)
    if log_level not in ("debug", "info", "warning", "error", "critical"):
        log_level = "info"
    getattr(logger, log_level)(
        message,
        extra={"scope": "transfer", "task_id": task.get("id"), "share_code": task.get("shareCode")},
    )


def _add_unique_task_log(task: Dict[str, Any], level: str, message: str) -> None:
    if any(log.get("message") == message for log in task.get("logs", [])):
        return
    _add_task_log(task, level, message)


# ---------------------------------------------------------------------------
# 文件名 / 路径工具
# ---------------------------------------------------------------------------
def _display_path(file: Dict[str, Any]) -> str:
    return "/".join([*file.get("path", []), file.get("name", "")])


def _format_bytes(bytes_val: Union[int, float]) -> str:
    value = max(0, float(bytes_val) or 0)
    if value < 1024:
        return f"{round(value)} B"
    if value < 1024 * 1024:
        return f"{(value / 1024):.1f} KB" if value < 10 * 1024 else f"{(value / 1024):.0f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{(value / (1024 * 1024)):.1f} MB" if value < 10 * 1024 * 1024 else f"{(value / (1024 * 1024)):.0f} MB"
    return f"{(value / (1024 * 1024 * 1024)):.1f} GB"


def _format_elapsed(elapsed_ms: float) -> str:
    seconds = int(elapsed_ms / 1000)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def _build_pan123_path(path: List[str]) -> List[str]:
    return [part for part in (_build_pan123_path_part(part) for part in path) if part]


def _build_pan123_path_part(part: str) -> str:
    original = str(part or "").strip()
    if not original:
        return ""
    cleaned = _sanitize_pan123_file_name(original) or "目录"
    needs_safe_name = cleaned != original or _char_length(cleaned) > PAN123_PATH_PART_MAX
    if not needs_safe_name:
        return original
    suffix = f".p{_stable_short_hash(original)}"
    max_stem_length = max(1, PAN123_PATH_PART_MAX - _char_length(suffix))
    stem = _truncate_chars(cleaned, max_stem_length).rstrip(". ") or "目录"
    return f"{stem}{suffix}"


def _offline_candidate_names(file: Dict[str, Any]) -> List[str]:
    return list(dict.fromkeys([
        name for name in [file.get("name"), file.get("offlineSubmitName")]
        if name and str(name).strip()
    ]))


def _build_offline_submit_name(file: Dict[str, Any]) -> str:
    original = str(file.get("name") or "download").strip() or "download"
    display = _display_path(file)
    cleaned = _sanitize_pan123_file_name(original) or "download"
    needs_short_name = (
        cleaned != original
        or _char_length(cleaned) > PAN123_OFFLINE_SUBMIT_NAME_MAX
        or _char_length(display) > PAN123_OFFLINE_DISPLAY_PATH_MAX
    )
    if not needs_short_name:
        return original
    stem, ext = _split_file_name(cleaned)
    suffix = f".p{re.sub(r'[^A-Za-z0-9]', '', str(file.get('id') or int(datetime.now(timezone.utc).timestamp() * 1000)))[-10:]}"
    max_stem_length = max(16, PAN123_OFFLINE_SUBMIT_NAME_MAX - _char_length(ext) - _char_length(suffix))
    short_stem = _truncate_chars(stem or "download", max_stem_length).rstrip(". ") or "download"
    return f"{short_stem}{suffix}{ext}"


def _sanitize_pan123_file_name(name: str) -> str:
    return re.sub(r"\s+", " ", PAN123_FORBIDDEN_NAME_RE.sub(" ", str(name or ""))).strip(". ").strip()


def _split_file_name(name: str) -> Tuple[str, str]:
    index = name.rfind(".")
    if index <= 0 or index == len(name) - 1 or len(name) - index > 16:
        return name, ""
    return name[:index], name[index:]


def _truncate_chars(value: str, max_length: int) -> str:
    chars = list(value)
    return value if len(chars) <= max_length else "".join(chars[:max(1, max_length)])


def _char_length(value: str) -> int:
    return len(list(value))


def _stable_short_hash(value: str) -> str:
    hash_val = 2166136261
    for char in value:
        hash_val ^= ord(char)
        hash_val = (hash_val * 16777619) & 0xFFFFFFFF
    return format(hash_val, "x")[:8]


def _unique_dir_ids(values: List[str]) -> List[str]:
    return list(dict.fromkeys([str(value or "0") for value in values if value]))


def _file_key(file: Dict[str, Any]) -> str:
    return f"{'/'.join(file.get('path', []))}/{file.get('name')}:{file.get('size')}"


def _is_done_file(file: Dict[str, Any]) -> bool:
    return file.get("status") in ("success", "skipped")


def _merge_files(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key = {_file_key(file): file for file in existing}
    for file in incoming:
        key = _file_key(file)
        previous = by_key.get(key)
        if previous:
            by_key[key] = {
                **file,
                **previous,
                "path": file.get("path"),
                "name": file.get("name"),
                "size": file.get("size"),
                "sha1": file.get("sha1"),
            }
        else:
            by_key[key] = file
    return list(by_key.values())


def _is_missing_target_dir_error(error: Exception) -> bool:
    message = str(error)
    return bool(re.search(
        r"父级文件ID不存在|指定目录ID文件不存在|parent\s*(?:file\s*)?id.*(?:not\s*found|不存在)|directory\s*id.*(?:not\s*found|不存在)",
        message, re.I,
    ))


def _offline_wait_deadline_ms(file_size: int, configured_ms: int) -> int:
    """按文件大小放宽离线等待上限：小文件用配置值，大文件给足排队+下载时间。"""
    size = max(0, int(file_size or 0))
    if size >= 10 * 1024 * 1024 * 1024:
        adaptive = 4 * 60 * 60_000
    elif size >= 2 * 1024 * 1024 * 1024:
        adaptive = 2 * 60 * 60_000
    elif size >= 500 * 1024 * 1024:
        adaptive = 90 * 60_000
    else:
        adaptive = configured_ms
    return max(configured_ms, adaptive)


async def _delay(ms: int) -> None:
    await asyncio.sleep(ms / 1000)


# ---------------------------------------------------------------------------
# 115 本地盘任务标识（enqueue 与管线共用）
# ---------------------------------------------------------------------------
def _normalize_local_115_ref(path: str) -> str:
    value = str(path or "").strip()
    if not value or value == "0":
        return "/"
    explicit_cid = re.match(r"^(?:cid|id):\s*(\d+)$", value, re.IGNORECASE)
    if explicit_cid:
        return f"cid:{explicit_cid.group(1)}"
    if value.isdigit():
        return f"cid:{value}"
    return "/" + value.strip("/")


def _is_local_115_cid_ref(value: str) -> bool:
    return bool(re.match(r"^cid:\d+$", str(value or "").strip(), re.IGNORECASE))


def _local_115_cid_from_ref(value: str) -> str:
    match = re.match(r"^cid:(\d+)$", str(value or "").strip(), re.IGNORECASE)
    return match.group(1) if match else ""


def _local_115_task_title(path: str) -> str:
    normalized = _normalize_local_115_ref(path)
    if _is_local_115_cid_ref(normalized):
        return f"115 本地盘 CID {_local_115_cid_from_ref(normalized)}"
    if normalized == "/":
        return "115 本地盘"
    return normalized.rstrip("/").rsplit("/", 1)[-1] or "115 本地盘"


def _local_115_task_code(path: str) -> str:
    return f"local:{_normalize_local_115_ref(path).lower()}"


def _local_115_task_url(path: str) -> str:
    normalized = _normalize_local_115_ref(path)
    if _is_local_115_cid_ref(normalized):
        return f"115://local?{urlencode({'cid': _local_115_cid_from_ref(normalized)})}"
    return f"115://local?{urlencode({'path': normalized})}"


def _is_local_115_task(task: Dict[str, Any]) -> bool:
    share_code = str(task.get("shareCode") or "").lower()
    share_url = str(task.get("shareUrl") or "").lower()
    return share_code.startswith("local:") or share_url.startswith("115://local")


def _local_115_path_from_task(task: Dict[str, Any]) -> str:
    share_url = str(task.get("shareUrl") or "").strip()
    if share_url.lower().startswith("115://local"):
        try:
            parsed = urlparse(share_url)
            cid_value = (parse_qs(parsed.query).get("cid") or [""])[0]
            if cid_value:
                return _normalize_local_115_ref(f"cid:{cid_value}")
            path_value = (parse_qs(parsed.query).get("path") or [""])[0]
            if path_value:
                return _normalize_local_115_ref(path_value)
        except Exception:
            pass
    source_text = str(task.get("sourceText") or "").strip()
    if source_text:
        return _normalize_local_115_ref(source_text)
    share_code = str(task.get("shareCode") or "")
    if share_code.lower().startswith("local:"):
        return _normalize_local_115_ref(share_code[6:])
    return "/"


def _is_local_115_file(file: Dict[str, Any]) -> bool:
    return str(file.get("sourceType") or "").lower() == "115_local" or bool(file.get("pickCode") or file.get("pick_code"))


def _transfer_cookie_pool(config: Dict[str, Any]) -> List[Dict[str, str]]:
    values = [
        *(config.get("pan115Cookies", []) if isinstance(config.get("pan115Cookies"), list) else []),
        config.get("pan115Cookie", ""),
    ]
    lines = list(dict.fromkeys([
        line.strip()
        for value in values
        for line in re.split(r"\n\s*\n|[\r\n]+", str(value or ""))
        if line.strip()
    ]))
    result: List[Dict[str, str]] = []
    for index, line in enumerate(lines):
        split_index = line.find("|")
        if split_index > 0:
            name = line[:split_index].strip()
            cookie = line[split_index + 1:].strip()
            if name and cookie:
                result.append({"name": name, "cookie": cookie})
                continue
        result.append({"name": f"Cookie {index + 1}", "cookie": line})
    return result


# ---------------------------------------------------------------------------
# 123 目录缓存
# ---------------------------------------------------------------------------
class TransferDirCache:
    """任务级 123 目录缓存。

    规划阶段把每个目标目录真正只列一次；秒传/跳过判定读缓存，等待阶段
    强制刷新。大分享能把 O(N²) 次列目录降为常数次，避免触发 123 限流。
    """

    def __init__(self) -> None:
        self._path_ids: Dict[str, str] = {}
        self._listings: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def _lock_for(self, dir_id: str) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._locks.get(dir_id)
            if lock is None:
                lock = self._locks.setdefault(dir_id, asyncio.Lock())
            return lock

    async def resolve_dir(self, pan123: "Pan123OpenAPIClient", root_dir_id: str, path: List[str]) -> str:
        key = f"{root_dir_id}:{'/'.join(path)}"
        cached = self._path_ids.get(key)
        if cached:
            return cached
        dir_id = str(await pan123.ensure_path(root_dir_id, path))
        self._path_ids[key] = dir_id
        return dir_id

    async def list_dir(
        self, pan123: "Pan123OpenAPIClient", dir_id: str, force: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        if not force:
            cached = self._listings.get(str(dir_id))
            if cached is not None:
                return cached
        async with await self._lock_for(str(dir_id)):
            if not force:
                cached = self._listings.get(str(dir_id))
                if cached is not None:
                    return cached
            files = await pan123.list_files(dir_id)
            listing = {
                str(file.get("filename") or file.get("name") or ""): file
                for file in files
                if file.get("filename") or file.get("name")
            }
            self._listings[str(dir_id)] = listing
            return listing

    def invalidate_dir(self, dir_id: Optional[str]) -> None:
        if dir_id:
            self._listings.pop(str(dir_id), None)

    async def find_same_file(
        self, pan123: "Pan123OpenAPIClient", dir_id: str, name: str, size: int, force: bool = False
    ) -> Optional[Dict[str, Any]]:
        listing = await self.list_dir(pan123, dir_id, force=force)
        file = listing.get(str(name))
        if file and int(file.get("type") or 0) != 1 and int(file.get("size") or 0) == int(size or 0):
            return file
        return None


# ---------------------------------------------------------------------------
# 离线下载条目（等待阶段的照看对象）
# ---------------------------------------------------------------------------
class OfflineItem:
    def __init__(
        self,
        file: Dict[str, Any],
        target_dir_id: str,
        target_root_id: str,
        before_ids: Optional[Dict[str, Set[int]]] = None,
        account: Optional[Dict[str, str]] = None,
    ) -> None:
        self.file = file
        self.target_dir_id = target_dir_id
        self.target_root_id = target_root_id
        self.before_ids = before_ids or {}
        self.account = account
        self.started_ms = time.monotonic() * 1000
        self.done = False
        self.failed = False
        # submitted=False 表示还在排队等空位，未真正提交到 123
        self.submitted = False


# ---------------------------------------------------------------------------
# 批量离线管理器：滚动提交（阶段 4）+ 统一等待（阶段 5）
# ---------------------------------------------------------------------------
class OfflineDownloadManager:
    """参照 tdr-123help 的 DownloadManager：

    - 同时在 123 排队的离线任务最多 max_inflight 个（即后台"并发"配置，
      1-5 个）：先提交一批，完成一个自动补交排队中的下一个
    - 提交动作本身另有全局信号量限速；万一撞"同时下载的任务超出最大
      限制"按 5/15/30/60 秒退避重试（不做无限死等）
    - 等待：一个循环照看所有离线任务，不轮询 123 的离线进度接口
      （OpenAPI 侧该接口不稳定），完成与否以"列目标目录+大小比对"为准
    - 失败：等待超时自动重新提交（最多 3 次）；疑似错误页文件移入回收站
    """

    def __init__(self, pipeline: "TransferPipeline", max_inflight: int = 5) -> None:
        self.pipeline = pipeline
        self.max_inflight = max(1, max_inflight)
        self.items: List[OfflineItem] = []
        self.pending: List[OfflineItem] = []
        self._warned_suspicious_artifacts: Set[str] = set()

    def queue(self, item: OfflineItem) -> None:
        """排入待提交队列（不立即提交）。"""
        self.items.append(item)
        self.pending.append(item)

    def inflight_items(self) -> List[OfflineItem]:
        return [item for item in self.items if item.submitted and not item.done and not item.failed]

    def unresolved_count(self) -> int:
        return len([item for item in self.items if not item.done and not item.failed])

    async def fill(self) -> None:
        """用排队中的文件把进行中的离线任务补到并发上限。"""
        while self.pending and len(self.inflight_items()) < self.max_inflight:
            item = self.pending.pop(0)
            await self.submit(item)

    # ------------------------------------------------------------------
    # 阶段 4：提交
    # ------------------------------------------------------------------
    async def submit(self, item: OfflineItem) -> None:
        service = self.pipeline.service
        task = self.pipeline.task
        file = item.file
        display = _display_path(file)

        file["status"] = "running"
        file["startedAt"] = _utc_now_iso()
        file["error"] = None
        try:
            target_dir_id = str(file.get("targetDirId") or "")
            before_ids = await self.pipeline.snapshot_candidate_file_ids(self.pipeline.target_root_id, target_dir_id)
            item.before_ids = before_ids
            pan115 = Pan115TransferClient(item.account["cookie"] if item.account else "")
            download_url = await service._get_pan115_download_url(task, file, pan115, item.account)
            file["sourceUrl"] = download_url
            offline_submit_name = _build_offline_submit_name(file)
            file["offlineSubmitName"] = None if offline_submit_name == file["name"] else offline_submit_name
            file["offlineStatus"] = "submitting"
            file["offlineStatusText"] = "提交中"
            file["offlineProgress"] = 0
            file["method"] = "offline"
            # 先把"准备提交"落库：进程中途挂掉重启后不会重复建离线任务
            if not await service._save_transfer_task(task):
                raise TaskCancelled()

            offline_task_id = await self._create_with_backoff(file, download_url)
            file["offlineTaskId"] = offline_task_id
            file["offlineStatus"] = "running"
            file["offlineStatusText"] = "离线中"
            file["offlineProgress"] = 0
            file["offlineResubmits"] = 0
            file["finishedAt"] = None
            if file.get("offlineSubmitName"):
                _add_task_log(task, "info", f"已提交 123 离线下载（用临时短名）：{display} #{offline_task_id}")
            else:
                _add_task_log(task, "info", f"已提交 123 离线下载：{display} #{offline_task_id}")
            if not await service._save_transfer_task(task):
                raise TaskCancelled()
            item.submitted = True
        except TaskCancelled:
            raise
        except Exception as error:
            file["status"] = "failed"
            if file.get("method") == "offline":
                file["offlineStatus"] = "failed"
                file["offlineStatusText"] = "失败"
            file["error"] = str(error)
            file["finishedAt"] = _utc_now_iso()
            item.failed = True
            _add_task_log(task, "error", f"离线提交失败：{display}（{error}）")

    async def _create_with_backoff(self, file: Dict[str, Any], download_url: str) -> int:
        pan123 = self.pipeline.pan123
        submit_name = file.get("offlineSubmitName") or file["name"]
        last_error: Optional[Exception] = None
        for attempt in range(len(OFFLINE_SUBMIT_BACKOFF_MS) + 1):
            target_dir_id = str(file.get("targetDirId") or "")
            try:
                async with self.pipeline.service._get_offline_submit_semaphore():
                    return await pan123.create_offline_download(download_url, target_dir_id, submit_name)
            except TaskCancelled:
                raise
            except Exception as error:
                if _is_missing_target_dir_error(error):
                    # 目标目录失效：重建目录后立即重试一次（不计入退避）
                    await self.pipeline.refresh_target_dir(file)
                    refreshed_dir = str(file.get("targetDirId") or "")
                    async with self.pipeline.service._get_offline_submit_semaphore():
                        return await pan123.create_offline_download(download_url, refreshed_dir, submit_name)
                last_error = error
                if attempt >= len(OFFLINE_SUBMIT_BACKOFF_MS):
                    break
                wait_ms = OFFLINE_SUBMIT_BACKOFF_MS[attempt]
                _add_task_log(
                    self.pipeline.task, "warn",
                    f"123 离线通道繁忙，{wait_ms // 1000} 秒后重试提交"
                    f"（第 {attempt + 1}/{len(OFFLINE_SUBMIT_BACKOFF_MS)} 次）：{_display_path(file)}（{error}）",
                )
                await _delay(wait_ms)
        raise last_error or RuntimeError("123 离线任务提交失败")

    # ------------------------------------------------------------------
    # 阶段 5：统一等待
    # ------------------------------------------------------------------
    async def wait_all(self) -> None:
        if not self.items:
            return
        service = self.pipeline.service
        task = self.pipeline.task
        pan123 = self.pipeline.pan123
        transfer_config = self.pipeline.config
        poll_ms = max(2000, int(transfer_config.get("offlinePollMs") or 5000))
        max_polls = max(1, int(transfer_config.get("offlineMaxPolls") or 240))
        last_progress_log_ms = 0.0

        await self.fill()
        while True:
            unresolved = self.unresolved_count()
            if unresolved == 0:
                break
            inflight = self.inflight_items()
            progressed = bool(self.pending)
            for item in inflight:
                file = item.file
                candidate_names = _offline_candidate_names(file)
                # 完成判定（照 tdr-123help）：列目标目录，候选名 + 大小一致才算落盘。
                # 不轮询 123 离线进度接口——完成、失败都由落盘检测和超时兜底。
                created: Optional[Dict[str, Any]] = None
                try:
                    listing = await self.pipeline.dir_cache.list_dir(pan123, item.target_dir_id, force=True)
                    for candidate_name in candidate_names:
                        candidate = listing.get(candidate_name)
                        if (
                            candidate
                            and int(candidate.get("type") or 0) != 1
                            and int(candidate.get("size") or 0) == int(file.get("size", 0))
                        ):
                            created = candidate
                            break
                except Exception as error:
                    _add_unique_task_log(task, "warn", f"123 目标目录暂时查不动，本轮跳过（{error}）")
                if not created:
                    suspicious = await self._find_suspicious_artifact(item)
                    if suspicious:
                        self._mark_failed(item, f"123 离线落盘了疑似错误页文件，已移入回收站：{suspicious}")
                        progressed = True
                        continue
                    created = await self.pipeline.recover_offline_file(
                        file, item.target_dir_id, item.target_root_id, item.before_ids
                    )
                if created:
                    await self._mark_done(item, created)
                    progressed = True
                    continue

                deadline_ms = _offline_wait_deadline_ms(file.get("size", 0), max_polls * poll_ms)
                if time.monotonic() * 1000 - item.started_ms < deadline_ms:
                    continue
                # 超时：先自动重新提交（最多 3 次），把等待计时重新起算
                resubmits = int(file.get("offlineResubmits") or 0)
                if file.get("sourceUrl") and resubmits < TRANSFER_OFFLINE_RESUBMIT_MAX:
                    file["offlineResubmits"] = resubmits + 1
                    _add_task_log(
                        task, "warn",
                        f"等了很久还没落盘，自动重新提交离线任务"
                        f"（第 {resubmits + 1}/{TRANSFER_OFFLINE_RESUBMIT_MAX} 次）：{_display_path(file)}",
                    )
                    try:
                        async with service._get_offline_submit_semaphore():
                            new_task_id = await pan123.create_offline_download(
                                file["sourceUrl"], item.target_dir_id,
                                file.get("offlineSubmitName") or file["name"],
                            )
                    except TaskCancelled:
                        raise
                    except Exception as submit_error:
                        self._mark_failed(item, f"超时后重新提交失败：{submit_error}")
                        progressed = True
                        continue
                    file["offlineTaskId"] = new_task_id
                    file["offlineStatus"] = "running"
                    file["offlineStatusText"] = "离线中"
                    file["offlineProgress"] = 0
                    item.started_ms = time.monotonic() * 1000
                    await service._save_transfer_task(task)
                    progressed = True
                else:
                    self._mark_failed(
                        item,
                        f"等待 123 离线完成超时（已自动重新提交 {resubmits} 次）",
                    )
                    progressed = True

            # 有空位就补交排队中的文件（完成一个补一个）
            if self.pending:
                await self.fill()

            done_count = len(self.items) - self.unresolved_count()
            now_ms = time.monotonic() * 1000
            if progressed or now_ms - last_progress_log_ms >= 60_000:
                waiting = len(self.inflight_items())
                queued = len(self.pending)
                queue_text = f"，排队中 {queued}" if queued else ""
                _add_task_log(
                    task, "info",
                    f"离线下载进行中：已完成 {done_count}/{len(self.items)}，"
                    f"正在下载 {waiting}{queue_text}，{poll_ms // 1000} 秒后再检查",
                )
                last_progress_log_ms = now_ms
                if not await service._save_transfer_task(task):
                    raise TaskCancelled()
            await _delay(poll_ms)

    async def _mark_done(self, item: OfflineItem, created: Dict[str, Any]) -> None:
        service = self.pipeline.service
        task = self.pipeline.task
        file = item.file
        self.pipeline.dir_cache.invalidate_dir(item.target_dir_id)
        created = await self._rename_if_needed(item, created)
        await service._remember_transfer_hash(file, created)
        file["status"] = "success"
        file["method"] = file.get("method") or "offline"
        file["pan123FileId"] = created.get("fileId")
        file["offlineStatus"] = "success"
        file["offlineStatusText"] = "成功"
        file["offlineProgress"] = 100
        file["finishedAt"] = _utc_now_iso()
        _add_task_log(
            task, "info",
            f"下载完成并核对大小一致：{_display_path(file)}（{_format_bytes(file.get('size', 0))}）",
        )
        deletion_task = await service._delete_115_source_after_success_if_needed(
            task, file, item.account or service._fallback_account()
        )
        if deletion_task:
            task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
        item.done = True

    def _mark_failed(self, item: OfflineItem, reason: str) -> None:
        file = item.file
        file["status"] = "failed"
        if file.get("method") == "offline":
            file["offlineStatus"] = "failed"
            file["offlineStatusText"] = "失败"
        file["error"] = reason
        file["finishedAt"] = _utc_now_iso()
        _add_task_log(self.pipeline.task, "error", f"下载失败：{_display_path(file)}（{reason}）")
        item.failed = True

    async def _rename_if_needed(self, item: OfflineItem, found: Dict[str, Any]) -> Dict[str, Any]:
        file = item.file
        filename = str(found.get("filename") or found.get("name") or "")
        if filename == file["name"]:
            return {**found, "filename": file["name"]}
        await self.pipeline.pan123.rename_file(found["fileId"], file["name"])
        _add_task_log(self.pipeline.task, "info", f"离线文件已改名回原名：{filename} -> {_display_path(file)}")
        return {**found, "filename": file["name"]}

    async def _find_suspicious_artifact(self, item: OfflineItem) -> Optional[str]:
        """识别"离线下到的是错误页"这类坏文件并移入回收站。"""
        task = self.pipeline.task
        file = item.file
        expected_size = int(file.get("size") or 0)
        if not math.isfinite(expected_size) or expected_size <= 1024 * 1024:
            return None
        candidate_names = _offline_candidate_names(file)
        existing_ids = item.before_ids.get(item.target_dir_id, set())
        try:
            files = await self.pipeline.pan123.list_files(item.target_dir_id)
        except Exception:
            return None
        for candidate in files:
            if candidate.get("type") == 1:
                continue
            if int(candidate.get("fileId") or candidate.get("id") or 0) in existing_ids:
                continue
            if candidate.get("filename") not in candidate_names:
                continue
            size = int(candidate.get("size") or 0)
            if size <= 0 or size >= expected_size or size > 1024 * 1024:
                continue
            key = f"{item.target_dir_id}:{candidate.get('fileId')}:{candidate.get('filename')}:{size}"
            if key in self._warned_suspicious_artifacts:
                continue
            self._warned_suspicious_artifacts.add(key)
            _add_task_log(
                task, "warn",
                f"发现疑似错误页文件：{_display_path(file)} -> {candidate.get('filename')}"
                f"（{_format_bytes(size)}），移入回收站",
            )
            try:
                await self.pipeline.pan123.trash_files([int(candidate.get("fileId") or candidate.get("id") or 0)])
            except Exception as error:
                logger.warning("疑似错误页文件移入回收站失败", extra={"error": str(error)})
            return f"{candidate.get('filename')}（{_format_bytes(size)}）"
        return None


# ---------------------------------------------------------------------------
# 六阶段管线
# ---------------------------------------------------------------------------
class TransferPipeline:
    def __init__(self, service: Any, task: Dict[str, Any], transfer_config: Dict[str, Any]) -> None:
        self.service = service
        self.task = task
        self.config = transfer_config
        self.dir_cache = TransferDirCache()
        self.pan123: Pan123OpenAPIClient = None  # type: ignore[assignment]
        self.target_root_id = str(transfer_config.get("targetDirId") or "0").strip() or "0"
        # 同时在 123 排队的离线任务上限 = 后台"并发"配置（1-5）
        self.offline = OfflineDownloadManager(self, service._max_offline_slots(transfer_config))
        self.started_ms = time.monotonic() * 1000

    # ------------------------------------------------------------------
    async def run(self) -> None:
        task = self.task
        self.pan123 = await self.service._create_pan123_client()
        if _is_local_115_task(task):
            _add_task_log(task, "info", "开始搬运 115 本地盘目录")
        else:
            _add_task_log(task, "info", "开始搬运 115 分享")

        files = await self.phase_inspect()
        if not files or _is_final_pipeline_status(task):
            return
        todo = await self.phase_plan(files)
        offline_files = await self.phase_reuse(todo)
        await self.phase_submit(offline_files)
        await self.offline.wait_all()
        await self.phase_finalize()

    # ------------------------------------------------------------------
    # 阶段 1：解析
    # ------------------------------------------------------------------
    async def phase_inspect(self) -> List[Dict[str, Any]]:
        task = self.task
        transfer_config = self.config
        is_local_task = _is_local_115_task(task)

        if is_local_task:
            local_path = _local_115_path_from_task(task)
            source_account = self.service._local_source_pan115_account()
            inspected = await self.service._inspect_pan115_local_path(task, local_path, source_account, transfer_config)
            inspection = inspected["inspection"]
            task["title"] = inspection.get("title") or task.get("title")
            task["receiveCode"] = ""
            task["shareUrl"] = _local_115_task_url(str(inspection.get("local_path") or local_path))
            task["shareCode"] = _local_115_task_code(str(inspection.get("local_path") or local_path))
            task["rootCid"] = inspection.get("root_cid")
            task["localDirMap"] = inspection.get("dir_map", {})
        else:
            link = {
                "url": task["shareUrl"],
                "clean_url": task["shareUrl"],
                "share_code": task["shareCode"],
                "receive_code": task.get("receiveCode"),
            }
            accounts = self.service._filter_live_accounts(_transfer_cookie_pool(transfer_config), task)
            inspected = await self.service._inspect_pan115_share(task, link, accounts, transfer_config.get("pan115Cookie", ""))
            inspection = inspected["inspection"]
            task["title"] = inspection.get("title") or task.get("title")
            task["receiveCode"] = inspection.get("receive_code") or task.get("receiveCode")

        task["files"] = _merge_files(task.get("files", []), inspection.get("files", []))
        task["totalFiles"] = len(task["files"])
        task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
        total_size = sum(int(f.get("size") or 0) for f in task["files"])
        _add_task_log(
            task, "info",
            f"解析完成：共 {task['totalFiles']} 个文件，合计 {_format_bytes(total_size)}",
        )
        if not await self.service._save_transfer_task(task):
            raise TaskCancelled()
        return task["files"]

    # ------------------------------------------------------------------
    # 阶段 2：规划（每个目标目录只列一次，先挑出已存在的文件）
    # ------------------------------------------------------------------
    async def phase_plan(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        task = self.task
        pan123 = self.pan123
        todo: List[Dict[str, Any]] = []
        skipped = 0

        pending = [f for f in files if not _is_done_file(f)]
        # 先解析每个文件的目标路径，同一目录的文件归成一组
        groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        for file in pending:
            target_path = _build_pan123_path(file.get("path", []))
            file["targetPath"] = target_path
            groups.setdefault(tuple(target_path), []).append(file)

        for target_path, group in groups.items():
            target_dir_id = await self.dir_cache.resolve_dir(pan123, self.target_root_id, list(target_path))
            # 该目录只列这一次，组内所有文件共用这份"已有文件"清单
            listing = await self.dir_cache.list_dir(pan123, target_dir_id)
            for file in group:
                file["targetDirId"] = target_dir_id
                existing = listing.get(str(file.get("name") or ""))
                if existing and int(existing.get("type") or 0) != 1 and int(existing.get("size") or 0) == int(file.get("size") or 0):
                    await self._skip_existing(file, existing)
                    skipped += 1
                    continue
                if existing and int(existing.get("type") or 0) != 1:
                    _add_unique_task_log(
                        task, "info",
                        f"同名文件大小不同，将重新搬运：{_display_path(file)}",
                    )
                todo.append(file)

        if todo and skipped:
            _add_task_log(task, "info", f"规划完成：{skipped} 个文件已存在直接跳过，待搬运 {len(todo)} 个")
        elif skipped:
            _add_task_log(task, "info", f"规划完成：{skipped} 个文件 123 里已经有了，全部跳过")
        elif todo:
            _add_task_log(task, "info", f"规划完成：待搬运 {len(todo)} 个文件")
        if not await self.service._save_transfer_task(task):
            raise TaskCancelled()
        return todo

    async def _skip_existing(self, file: Dict[str, Any], existing: Dict[str, Any]) -> None:
        await self.service._remember_transfer_hash(file, existing)
        file["status"] = "skipped"
        file["method"] = "exists"
        file["pan123FileId"] = existing.get("fileId")
        file["finishedAt"] = _utc_now_iso()
        _add_task_log(self.task, "info", f"123 里已有同名同大小文件，跳过：{_display_path(file)}")
        deletion_task = await self.service._delete_115_source_after_success_if_needed(
            self.task, file, self.deletion_account()
        )
        if deletion_task:
            self.task.setdefault("_pendingPan115Deletions", []).append(deletion_task)

    def deletion_account(self) -> Dict[str, str]:
        """删 115 源文件只发生在本地盘任务上，固定用助手账号。"""
        if _is_local_115_task(self.task):
            account = self.service._local_source_account_or_none()
            if account:
                return account
        return self.service._fallback_account()

    # ------------------------------------------------------------------
    # 阶段 3：秒传 + 断点恢复
    # ------------------------------------------------------------------
    async def phase_reuse(self, todo: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        task = self.task
        service = self.service
        offline_files: List[Dict[str, Any]] = []
        counters = {"reused": 0, "resumed": 0}
        if not todo:
            return offline_files

        lock = asyncio.Lock()
        next_idx = 0
        concurrency = max(1, min(service._max_offline_slots(self.config), len(todo)))

        async def worker() -> None:
            nonlocal next_idx
            while True:
                async with lock:
                    index = next_idx
                    next_idx += 1
                if index >= len(todo):
                    return
                result = await self._reuse_one(todo[index])
                if result == "offline":
                    offline_files.append(todo[index])
                else:
                    counters[result] = counters.get(result, 0) + 1
                task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
                if not await service._save_transfer_task(task):
                    raise TaskCancelled()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker_task in workers:
                if worker_task is not asyncio.current_task():
                    worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

        if counters["reused"] or counters["resumed"]:
            _add_task_log(
                task, "info",
                f"秒传完成：秒传成功 {counters['reused']} 个，断点恢复 {counters['resumed']} 个，"
                f"需要离线下载 {len(offline_files)} 个",
            )
        elif offline_files:
            _add_task_log(task, "info", f"秒传均未命中，{len(offline_files)} 个文件转入 123 离线下载")
        return offline_files

    async def _reuse_one(self, file: Dict[str, Any]) -> str:
        """返回 'reused'（秒传/认领成功）| 'resumed'（恢复等待离线）| 'offline'（需提交离线）。"""
        task = self.task
        service = self.service
        display = _display_path(file)
        target_dir_id = str(file.get("targetDirId") or "")

        # 断点恢复：上次运行已经提交过离线任务
        had_offline_attempt = bool(file.get("offlineTaskId") or file.get("sourceUrl") or file.get("method") == "offline")
        if had_offline_attempt:
            recovered = await self.recover_offline_file(file, target_dir_id, self.target_root_id)
            if recovered:
                await service._remember_transfer_hash(file, recovered)
                file["status"] = "success"
                file["method"] = "offline"
                file["pan123FileId"] = recovered.get("fileId")
                file["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "info", f"发现上次已离线完成，直接认领：{display}")
                deletion_task = await service._delete_115_source_after_success_if_needed(task, file, self.deletion_account())
                if deletion_task:
                    task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                return "reused"

            existing_offline_task = await self._find_offline_task_global(
                self.pan123, file.get("offlineTaskId"), _offline_candidate_names(file)
            )
            if not existing_offline_task:
                task_id_text = f" #{file.get('offlineTaskId')}" if file.get("offlineTaskId") else ""
                file["status"] = "failed"
                file["error"] = f"原 123 离线任务{task_id_text} 未找到，已停止自动重复添加；请确认离线列表后手动重试"
                file["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "error", file["error"])
                return "reused"

            file["offlineTaskId"] = existing_offline_task["id"]
            _add_task_log(task, "info", f"继续等待上次未完成的 123 离线任务 #{existing_offline_task['id']}：{display}")
            resumed_item = OfflineItem(file, target_dir_id, self.target_root_id)
            resumed_item.submitted = True  # 断点恢复视为已提交，占用一个离线并发位
            self.offline.items.append(resumed_item)
            return "resumed"

        # SHA1 秒传
        if file.get("sha1"):
            for attempt in range(2):
                try:
                    reused_file_id = await self.pan123.sha1_reuse(target_dir_id, file["name"], file["sha1"], file.get("size", 0))
                    if reused_file_id:
                        self.dir_cache.invalidate_dir(target_dir_id)
                        created_file = await self.dir_cache.find_same_file(self.pan123, target_dir_id, file["name"], file.get("size", 0))
                        if created_file:
                            # 回查 123 侧 etag 记入学习表，供 123→115 反向搬运秒传使用
                            await service._remember_transfer_hash(file, created_file)
                        file["status"] = "success"
                        file["method"] = "sha1_reuse"
                        file["pan123FileId"] = reused_file_id
                        file["finishedAt"] = _utc_now_iso()
                        _add_task_log(task, "info", f"SHA1 秒传成功：{display}")
                        deletion_task = await service._delete_115_source_after_success_if_needed(task, file, self.deletion_account())
                        if deletion_task:
                            task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                        return "reused"
                    break
                except Exception as error:
                    if attempt == 0 and _is_missing_target_dir_error(error):
                        await self.refresh_target_dir(file)
                        continue
                    _add_task_log(task, "warn", f"SHA1 秒传出错，转离线下载：{display}（{error}）")
                    break

        # MD5 秒传（etag 可来自本地缓存）
        rapid_etag = file.get("md5")
        if not rapid_etag and file.get("sha1"):
            cached_hash = service._store.get_transfer_hash(file["sha1"], file.get("size", 0))
            if cached_hash:
                rapid_etag = cached_hash.get("etag")
                file["md5"] = cached_hash.get("etag")
        if rapid_etag:
            for attempt in range(2):
                try:
                    reused_file_id = await self.pan123.md5_reuse(target_dir_id, file["name"], rapid_etag, file.get("size", 0))
                    if reused_file_id:
                        self.dir_cache.invalidate_dir(target_dir_id)
                        await service._remember_known_transfer_hash(file, rapid_etag)
                        file["status"] = "success"
                        file["method"] = "md5_reuse"
                        file["pan123FileId"] = reused_file_id
                        file["finishedAt"] = _utc_now_iso()
                        _add_task_log(task, "info", f"MD5 秒传成功：{display}")
                        deletion_task = await service._delete_115_source_after_success_if_needed(task, file, self.deletion_account())
                        if deletion_task:
                            task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                        return "reused"
                    break
                except Exception as error:
                    if attempt == 0 and _is_missing_target_dir_error(error):
                        await self.refresh_target_dir(file)
                        continue
                    _add_task_log(task, "warn", f"MD5 秒传出错，转离线下载：{display}（{error}）")
                    break

        return "offline"

    # ------------------------------------------------------------------
    # 阶段 4：滚动提交离线（并发上限内先提交一批，完成一个补一个）
    # ------------------------------------------------------------------
    async def phase_submit(self, offline_files: List[Dict[str, Any]]) -> None:
        if not offline_files:
            return
        accounts = self.service._filter_live_accounts(_transfer_cookie_pool(self.config))
        # 本地盘文件只存在于助手账号里，取直链不换号；分享任务轮询账号池
        fixed_account: Optional[Dict[str, str]] = None
        if _is_local_115_task(self.task):
            fixed_account = self.service._local_source_account_or_none()

        for index, file in enumerate(offline_files):
            if fixed_account:
                account = fixed_account
            elif accounts:
                account = accounts[index % len(accounts)]
            else:
                account = self.service._fallback_account()
            self.offline.queue(OfflineItem(
                file, str(file.get("targetDirId") or ""), self.target_root_id, None, account,
            ))

        await self.offline.fill()

        queued = len(self.offline.pending)
        inflight = len(self.offline.inflight_items())
        if queued:
            _add_task_log(
                self.task, "info",
                f"离线下载开始：先提交 {inflight} 个（并发上限 {self.offline.max_inflight}），"
                f"其余 {queued} 个排队，完成一个自动补交一个",
            )
        else:
            _add_task_log(self.task, "info", f"离线下载开始：{inflight} 个全部提交（未超并发上限）")

    # ------------------------------------------------------------------
    # 阶段 6：收尾
    # ------------------------------------------------------------------
    async def phase_finalize(self) -> None:
        task = self.task
        pending_deletions = task.get("_pendingPan115Deletions", [])
        if pending_deletions:
            await asyncio.gather(*pending_deletions, return_exceptions=True)
        if _is_local_115_task(task):
            account = self.service._local_source_account_or_none()
            if account:
                await self.service._cleanup_empty_local_folders(task, account)

        total = len(task.get("files", []))
        success = len([f for f in task["files"] if f.get("status") == "success"])
        skipped = len([f for f in task["files"] if f.get("status") == "skipped"])
        failed = len([f for f in task["files"] if f.get("status") == "failed"])
        elapsed_text = _format_elapsed(time.monotonic() * 1000 - self.started_ms)
        if failed:
            task["status"] = "failed" if failed == total else "partial"
            failed_names = [f.get("name") or "?" for f in task["files"] if f.get("status") == "failed"]
            preview = "、".join(str(name) for name in failed_names[:5]) + ("…" if len(failed_names) > 5 else "")
            _add_task_log(
                task, "warn",
                f"搬运完成（耗时 {elapsed_text}）：成功 {success}，跳过 {skipped}，失败 {failed}。失败文件：{preview}",
            )
        else:
            task["status"] = "success"
            _add_task_log(
                task, "info",
                f"搬运全部完成（耗时 {elapsed_text}）：成功 {success}，跳过 {skipped}，失败 0",
            )
        task["finishedAt"] = _utc_now_iso()

    # ------------------------------------------------------------------
    # 共用小工具
    # ------------------------------------------------------------------
    async def refresh_target_dir(self, file: Dict[str, Any]) -> None:
        previous_dir_id = str(file.get("targetDirId") or "")
        self.dir_cache.invalidate_dir(previous_dir_id)
        target_path = file.get("targetPath") or _build_pan123_path(file.get("path", []))
        new_dir_id = await self.pan123.ensure_path(self.target_root_id, target_path)
        self.dir_cache.invalidate_dir(new_dir_id)
        file["targetDirId"] = new_dir_id
        _add_task_log(
            self.task, "warn",
            f"目标目录失效，已重新创建：{previous_dir_id or '未知'} -> {new_dir_id}",
        )

    async def snapshot_candidate_file_ids(self, target_root_id: str, target_dir_id: str) -> Dict[str, Set[int]]:
        result: Dict[str, Set[int]] = {}
        for dir_id in _unique_dir_ids([target_dir_id, target_root_id or "0"]):
            try:
                files = await self.pan123.list_files(dir_id)
            except Exception:
                files = []
            result[dir_id] = {int(item.get("fileId") or item.get("id") or 0) for item in files if item.get("type") != 1}
        return result

    async def _find_offline_task_global(
        self, pan123: Pan123OpenAPIClient, task_id: Optional[int], filenames: List[str]
    ) -> Optional[Dict[str, Any]]:
        """断点恢复时在 123 离线列表里找已有任务（只查一次列表，不做进度轮询）。"""
        try:
            tasks = await pan123.list_offline_tasks()
        except Exception as error:
            # 列表暂时查不到时宁可按"任务还在"继续等（有超时兜底），不误判任务丢失
            _add_unique_task_log(self.task, "warn", f"123 离线列表暂时查不到，先按任务仍在处理（{error}）")
            return {"id": task_id or 0, "name": filenames[0] if filenames else ""}
        for item in tasks:
            if item.get("id") == task_id:
                return item
        for item in tasks:
            if item.get("name") in filenames:
                return item
        return None

    async def recover_offline_file(
        self,
        file: Dict[str, Any],
        target_dir_id: str,
        target_root_id: str,
        exclude_by_dir: Optional[Dict[str, Set[int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """断点恢复：在目标目录/根目录里找同大小文件，找到就挪回来改名。"""
        exclude_by_dir = exclude_by_dir or {}
        locations = [
            {"dirId": dir_id, "shouldMove": dir_id != target_dir_id}
            for dir_id in _unique_dir_ids([target_dir_id, target_root_id or "0"])
        ]
        for location in locations:
            try:
                found = await self.pan123.find_file_by_size(
                    location["dirId"], file.get("size", 0),
                    exclude_name=file["name"] if location["dirId"] == target_dir_id else None,
                    exclude_file_ids=exclude_by_dir.get(location["dirId"]),
                )
            except Exception:
                continue
            if not found:
                continue
            if location["shouldMove"]:
                await self.pan123.move_files([found["fileId"]], target_dir_id)
            if found.get("filename") != file["name"]:
                await self.pan123.rename_file(found["fileId"], file["name"])
            action = "发现已离线同大小文件并移动改名" if location["shouldMove"] else "发现已离线同大小文件并重命名"
            _add_task_log(self.task, "info", f"{action}：{found.get('filename')} -> {_display_path(file)}")
            return {**found, "filename": file["name"]}
        return None


def _is_final_pipeline_status(task: Dict[str, Any]) -> bool:
    return task.get("status") in ("success", "partial", "failed")
