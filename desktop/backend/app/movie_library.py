"""影库引擎：扫描导入目录里的秒传 JSON，聚合成可搜索的作品影库。

移植自参考项目「123云盘影库搜索-本地版」的 Library/LibraryManager：

* 兼容两种 JSON：123FastLink 导出（顶层 files[]，etag 为 base62 MD5）与
  123pan-strm-docker 备份（顶层 libraries[]，etag 为 hex MD5）。
* 按 path 父目录聚合「作品」；路径中带 {tmdb-N} / [tmdb-N] 标记的那一级视为
  聚合根（Season 子目录并入所属剧集）；单层 path 用 commonPath 末段兜底。
* 分类完全动态：path 首段为一级分类、深于两级时第二段为二级分类。
* 后台线程定时比对 mtime 热加载，文件静置数秒后才读，避免复制一半被解析。
"""

from __future__ import annotations

import codecs
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

VIDEO_EXT = {
    # PT/BT 站主流影视格式（含 ISO 原盘），用于判定导入 JSON 里的文件条目是否为视频；
    # 不够的可在影库设置「自定义视频扩展名」里补
    ".mkv", ".mp4", ".ts", ".iso", ".m2ts", ".mts", ".m2t",
    ".avi", ".rmvb", ".rm", ".wmv", ".mpg", ".mpeg", ".m4v",
    ".mov", ".flv", ".webm", ".vob", ".f4v", ".divx",
}


def normalize_video_extensions(raw: Any) -> Tuple[str, ...]:
    """把用户输入（逗号/分号/空格分隔或列表/集合）清洗成小写扩展名元组（带点），如 ".mp4" / "mp4" 均可。"""
    if isinstance(raw, str):
        parts = re.split(r"[,;、\s]+", raw)
    elif isinstance(raw, (list, tuple, set, frozenset)):
        parts = [str(item) for item in raw]
    else:
        parts = []
    out: List[str] = []
    seen = set()
    for part in parts:
        ext = part.strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if len(ext) >= 2 and re.match(r"^\.[a-z0-9]{1,8}$", ext) and ext not in seen:
            seen.add(ext)
            out.append(ext)
    return tuple(out)


def video_ext_set(extra: Any = None) -> frozenset:
    """默认视频扩展名 + 用户自定义扩展名（影库设置 videoExtensions）合并成判定集合。"""
    return frozenset(VIDEO_EXT) | frozenset(normalize_video_extensions(extra))

RE_YEAR = re.compile(r"\((\d{4})\)")
# {tmdb-123} 与 [tmdb-123] 两种标记都认（后者是 MegaShare_Search 等第三方导出格式）
RE_TMDB = re.compile(r"[{\[]tmdb-(\d+)[}\]]")

# 123 助手秒传文本格式（与油猴脚本 FASTLINK_PREFIXES 同构）
FASTLINK_PREFIXES = {
    "123FLCPV2$": {"common_path": True, "uses_base62": True},
    "123FLCPV1$": {"common_path": True, "uses_base62": False},
    "123FSLinkV2$": {"common_path": False, "uses_base62": True},
    "123FSLinkV1$": {"common_path": False, "uses_base62": False},
}

SCAN_EXTENSIONS = (".json", ".txt", ".123share", ".123fastlink")

SCAN_STABLE_SECONDS = 2  # 文件修改后至少静置这么久才加载，避免读到复制一半的文件
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600


def hex_to_base62(hex_str: str) -> str:
    """hex MD5 → base62（与油猴脚本同字母表 0-9a-zA-Z）"""
    h = str(hex_str or "").strip().lower()
    if not h or not re.match(r"^[0-9a-f]+$", h):
        return h
    n = int(h, 16)
    if n == 0:
        return "0"
    out = ""
    while n > 0:
        n, r = divmod(n, 62)
        out = BASE62[r] + out
    return out


def b62_to_hex(s: str) -> str:
    """base62 → hex MD5（16 字节 = 32 位 hex）。
    123FastLink JSON 里的 base62 etag 必须反解回 hex 才能喂给 123 网盘秒传 API。"""
    s = str(s or "").strip()
    if not s:
        return ""
    n = 0
    for ch in s:
        if ch not in BASE62:
            return ""
        n = n * 62 + BASE62.index(ch)
    h = "%x" % n
    if len(h) % 2:
        h = "0" + h
    if len(h) < 32:
        h = "0" * (32 - len(h)) + h
    return h


def etag_hex(value: str) -> str:
    """把影库 JSON 里的 etag 统一成 hex MD5：本身就是 32 位 hex 原样返回，base62 反解。"""
    s = str(value or "").strip()
    if re.match(r"^[0-9a-fA-F]{32}$", s):
        return s.lower()
    return b62_to_hex(s)


def fmt_size(size) -> str:
    v = int(size or 0)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if v < 1024 or unit == "PB":
            return (str(round(v)) if v >= 100 or unit == "B" else f"{v:.1f}") + " " + unit
        v /= 1024
    return str(size)


def norm(s) -> str:
    """归一化：去掉空格/标点/下划线并转小写，用于片名模糊匹配。"""
    return re.sub(r"[\s\W_]+", "", str(s or ""), flags=re.UNICODE).lower()


try:
    from pypinyin import lazy_pinyin as _lazy_pinyin
except ImportError:
    _lazy_pinyin = None


def pinyin_keys(text: str) -> Tuple[str, str]:
    """标题的（全拼，拼音首字母），如「海王」→ ("haiwang", "hw")。未装 pypinyin 时返回空。"""
    if not _lazy_pinyin or not text:
        return "", ""
    try:
        parts = _lazy_pinyin(str(text))
        return norm("".join(parts)), norm("".join(p[:1] for p in parts if p))
    except Exception:
        return "", ""


def parse_dir_name(dirname: str) -> Tuple[str, Optional[int], Optional[int]]:
    """从目录最后一段解析 片名/年份/TMDB ID，如「海王 (2018) {tmdb-297802}」。"""
    seg = str(dirname or "").rsplit("/", 1)[-1]
    tmdb = RE_TMDB.search(seg)
    year = RE_YEAR.search(seg)
    title = RE_TMDB.sub("", RE_YEAR.sub("", seg)).strip().strip("()-_ ").strip()
    if not title:
        title = seg
    return title, (int(year.group(1)) if year else None), (int(tmdb.group(1)) if tmdb else None)


def _normalize_etag(value: Any, uses_base62: bool) -> str:
    """按导出格式归一化 etag：base62 反解为 hex，hex 原样保留，无法解析保持原值。"""
    raw = str(value or "").strip()
    if uses_base62 and not re.match(r"^[0-9a-f]{32}$", raw, re.I):
        return b62_to_hex(raw) or raw
    return raw.lower()


def parse_fastlink_entries(common_path: str, entries: List[Dict[str, Any]], uses_base62: bool) -> Dict[str, Any]:
    """把 files 数组归一化成影库内部结构（对齐油猴脚本 normalizeImportedFiles）。"""
    files: List[Dict[str, Any]] = []
    for entry in entries:
        normalized = _normalize_entry(entry, uses_base62)
        if normalized is not None:
            files.append(normalized)
    return {"commonPath": str(common_path or "").strip().strip("/"), "files": files}


def _normalize_entry(entry: Any, uses_base62: bool) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None
    path = str(entry.get("path") or entry.get("fileName") or "").replace("\\", "/").strip().strip("/")
    if not path or set(path.split("/")) & {".", ".."}:
        return None
    return {
        "path": path,
        "fileName": path.rsplit("/", 1)[-1],
        "etag": _normalize_etag(entry.get("etag"), uses_base62),
        "size": int(entry.get("size") or 0),
        "s3KeyFlag": str(entry.get("s3KeyFlag") or ""),
    }


def parse_fastlink_json_data(data: Any) -> Dict[str, Any]:
    """标准 123FastLink JSON / MegaShare 第三方 JSON / [etag,size,path] 数组。"""
    if isinstance(data, list):
        if not all(isinstance(item, list) and len(item) >= 3 for item in data):
            raise ValueError("无效的秒传 JSON 数组")
        data = {"usesBase62EtagsInExport": False, "files": [
            {"etag": item[0], "size": item[1], "path": item[2]} for item in data
        ]}
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("不是影库索引文件（缺少 files 字段）")
    return parse_fastlink_entries(
        str(data.get("commonPath") or ""), data["files"], bool(data.get("usesBase62EtagsInExport")),
    )


def parse_fastlink_text(text: str) -> Dict[str, Any]:
    """123 助手的 V1/V2 秒传文本：`123FLC/L $` 前缀，`$` 分条、`#` 分字段。"""
    raw = str(text or "").strip()
    prefix = next((p for p in FASTLINK_PREFIXES if raw.startswith(p)), None)
    common_path = ""
    uses_base62 = False
    if prefix:
        raw = raw[len(prefix):]
        fmt = FASTLINK_PREFIXES[prefix]
        uses_base62 = fmt["uses_base62"]
        if fmt["common_path"]:
            separator = raw.find("%")
            if separator < 0:
                raise ValueError("秒传文本缺少公共路径分隔符")
            common_path = raw[:separator]
            raw = raw[separator + 1:]
    elif raw.startswith("123F"):
        raise ValueError("不支持的秒传文本版本")
    entries = []
    for chunk in raw.replace("\r\n", "$").replace("\n", "$").split("$"):
        if not chunk:
            continue
        parts = chunk.split("#")
        etag = parts[0] if parts else ""
        size = parts[1] if len(parts) > 1 else 0
        path = "#".join(parts[2:]) if len(parts) > 2 else ""
        entries.append({"etag": etag, "size": size, "path": path})
    return parse_fastlink_entries(common_path, entries, uses_base62)


def parse_123share(text: str) -> Dict[str, Any]:
    """.123share：base64(JSON 节点数组)，按 ParentFileId 还原完整路径。"""
    import base64 as _base64
    clean = re.sub(r"\s+", "", str(text or ""))
    clean = re.sub(r"^data:.*?;base64,", "", clean)
    raw = json.loads(_base64.b64decode(clean).decode("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("无效的 .123share 内容")
    by_id = {
        str(item.get("FileId", item.get("FileID", item.get("fileId", item.get("id", ""))))): item
        for item in raw if isinstance(item, dict)
    }
    entries = []
    for item in raw:
        if not isinstance(item, dict) or int(item.get("Type", item.get("type", 0)) or 0) != 0:
            continue
        names = [str(item.get("FileName", item.get("fileName", item.get("name", ""))))]
        parent_id = str(item.get("ParentFileId", item.get("parentFileId", item.get("parentId", "0"))))
        seen: set = set()
        while parent_id and parent_id != "0" and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            names.insert(0, str(parent.get("FileName", parent.get("fileName", parent.get("name", "")))))
            parent_id = str(parent.get("ParentFileId", parent.get("parentFileId", parent.get("parentId", "0"))))
        entries.append({
            "path": "/".join(n for n in names if n),
            "etag": item.get("Etag", item.get("etag", item.get("md5", ""))),
            "size": item.get("Size", item.get("size", 0)),
        })
    # .123share 规范存 hex etag；真实导出偶见 base62，这里两种都兼容（hex 原样保留）
    return parse_fastlink_entries("", entries, uses_base62=True)


def parse_library_payload(data: Any) -> Dict[str, Any]:
    """影库 JSON 内容归一化：strm-docker libraries[] 与标准 JSON（含 MegaShare）。"""
    if isinstance(data, dict) and isinstance(data.get("libraries"), list) and not data.get("files"):
        all_files: List[Dict[str, Any]] = []
        common_path = ""
        for lib in data["libraries"]:
            if isinstance(lib, dict):
                all_files.extend(lib.get("files") or [])
                if not common_path and lib.get("commonPath"):
                    common_path = str(lib["commonPath"])
        logger.info("影库：检测到 123pan-strm-docker 格式，合并 %d 个子库、%d 个文件", len(data["libraries"]), len(all_files))
        return parse_fastlink_entries(common_path, all_files, bool(data.get("usesBase62EtagsInExport", True)))
    return parse_fastlink_json_data(data)


def parse_library_content(text: str) -> Dict[str, Any]:
    """把文件内容按 123 助手支持的格式依次尝试：JSON → 秒传文本 → .123share。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("文件是空的")
    if raw[0] in "[{":
        try:
            return parse_library_payload(json.loads(raw))
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"JSON 解析失败：{error}")
    try:
        parsed = parse_fastlink_text(raw)
        if parsed["files"]:
            return parsed
    except ValueError:
        pass
    try:
        share = parse_123share(raw)
        if share["files"]:
            return share
    except Exception:
        pass
    raise ValueError("不是影库文件（无法按 123 助手任何格式解析）")


class LibraryFullParseFallback(Exception):
    """流式解析不支持的嵌套结构（如 123pan-strm-docker 的 libraries[]），
    提示调用方回退到整读解析。"""


class _StreamJsonArrayReader:
    """按块读取 UTF-8 文本，用 json.JSONDecoder.raw_decode 逐个解出数组元素。
    巨型秒传 JSON（GB 级、千万条目）不再整读进内存，内存占用只与单个元素相关。"""

    def __init__(self, fileobj, chunk_size: int = 1 << 20):
        self._file = fileobj
        self._chunk_size = chunk_size
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._json = json.JSONDecoder()
        self._buf = ""
        self._pos = 0
        self._eof = False

    def _read_more(self) -> bool:
        """再读一块；EOF 时冲掉增量解码器尾字节。返回是否新增了文本。"""
        if self._eof:
            return False
        data = self._file.read(self._chunk_size)
        if not data:
            self._buf += self._decoder.decode(b"", final=True)
            self._eof = True
            return False
        self._buf += self._decoder.decode(data)
        return True

    def _trim(self) -> None:
        if self._pos >= (1 << 20):
            self._buf = self._buf[self._pos:]
            self._pos = 0

    def peek(self) -> str:
        """跳过空白后返回当前字符；EOF 返回空串。"""
        while True:
            n = len(self._buf)
            while self._pos < n and self._buf[self._pos] in " \t\r\n":
                self._pos += 1
            if self._pos < n:
                return self._buf[self._pos]
            if not self._read_more():
                return ""

    def read_value(self):
        """从当前位置解出下一个完整 JSON 值。文件被截断/损坏时抛 ValueError。"""
        self.peek()
        while True:
            try:
                value, end = self._json.raw_decode(self._buf, self._pos)
                self._pos = end
                self._trim()
                return value
            except json.JSONDecodeError:
                if not self._read_more():
                    raise ValueError("JSON 不完整或已损坏（文件被截断？）")

    def expect(self, char: str) -> str:
        got = self.peek()
        if got != char:
            raise ValueError(f"JSON 结构不符合预期（期望 {char}，实际 {got or 'EOF'}）")
        self._pos += 1
        return got


def open_library_stream(path: str, chunk_size: int = 1 << 20) -> Tuple[Dict[str, Any], Iterator[Dict[str, Any]]]:
    """流式打开本地秒传 JSON：先解析出元信息（commonPath/usesBase62），
    返回 (meta, 文件条目迭代器)——条目已归一化并拼回 commonPath，逐条产出、不全量驻留内存。
    支持 ① {commonPath, usesBase62EtagsInExport, files:[{path,etag,size},...]} ② [[etag,size,path],...]；
    strm-docker 的 libraries[] 嵌套结构抛 LibraryFullParseFallback，由调用方回退整读。"""
    fileobj = open(path, "rb")
    try:
        reader = _StreamJsonArrayReader(fileobj, chunk_size)
        first = reader.peek()
        if first not in "[{":
            raise ValueError("不是 JSON 影库文件")
        uses_base62 = False
        common_path = ""
        entry_state: Dict[str, Any] = {"started": False}

        def stream_entries() -> Iterator[Dict[str, Any]]:
            count = 0
            try:
                while True:
                    ch = reader.peek()
                    if ch == "]":
                        reader._pos += 1
                        break
                    if ch == ",":
                        reader._pos += 1
                        continue
                    if not ch:
                        raise ValueError("JSON 不完整或已损坏（files 数组未闭合）")
                    raw_entry = reader.read_value()
                    if root_first == "[" and isinstance(raw_entry, list) and len(raw_entry) >= 3:
                        raw_entry = {"etag": raw_entry[0], "size": raw_entry[1], "path": raw_entry[2]}
                    normalized = _normalize_entry(raw_entry, uses_base62)
                    if normalized is None:
                        continue
                    count += 1
                    yield normalized
                if count == 0:
                    raise ValueError("不是影库索引文件（files 数组为空）")
                # files 之后的其余键（若有）快速跳过，保证整个 JSON 合法性检查走完
                _skip_trailing(reader, root_first)
            finally:
                fileobj.close()

        root_first = first
        if first == "[":
            reader._pos += 1
            meta = {"commonPath": "", "usesBase62": False}
            entry_state["started"] = True
        else:
            reader._pos += 1
            while True:
                ch = reader.peek()
                if ch == "}":
                    reader._pos += 1
                    break
                if ch == ",":
                    reader._pos += 1
                    continue
                if not ch:
                    raise ValueError("JSON 不完整或已损坏（根对象未闭合）")
                key = reader.read_value()
                if not isinstance(key, str):
                    raise ValueError("不是影库索引文件（键名不是字符串）")
                reader.expect(":")
                if key == "commonPath":
                    common_path = str(reader.read_value() or "").strip().strip("/")
                elif key == "usesBase62EtagsInExport":
                    uses_base62 = reader.read_value() is True
                elif key == "files":
                    reader.expect("[")
                    meta = {"commonPath": common_path, "usesBase62": uses_base62}
                    entry_state["started"] = True
                    break
                elif key == "libraries":
                    raise LibraryFullParseFallback("strm-docker libraries[] 嵌套格式")
                else:
                    reader.read_value()
        if not entry_state["started"]:
            raise ValueError("不是影库索引文件（缺少 files 字段）")
        return meta, stream_entries()
    except LibraryFullParseFallback:
        fileobj.close()
        raise
    except Exception:
        fileobj.close()
        raise


def _skip_trailing(reader: _StreamJsonArrayReader, root_first: str) -> None:
    """files 数组闭合后消费根值剩余部分（对象键值对或数组尾部），确保 JSON 完整合法。"""
    if root_first == "[":
        reader.peek()
        return
    while True:
        ch = reader.peek()
        if ch in ("}", ""):
            return
        if ch == ",":
            reader._pos += 1
            continue
        key = reader.read_value()
        reader.expect(":")
        reader.read_value()


def split_category(dir_path: str) -> Tuple[str, str]:
    """取作品目录的一级/二级分类（二级只在作品之上有更深层级时才有意义）。"""
    segs = [s for s in str(dir_path or "").split("/") if s]
    cat = segs[0] if segs else ""
    sub = segs[1] if len(segs) > 2 and len(segs) > 1 and segs[1] else ""
    return cat, sub


def split_work(path: str, fallback_root: str = "") -> Optional[Tuple[str, str]]:
    """path → (作品聚合根, 文件名)；无斜杠时用 fallback_root 兜底，两级都没有返回 None。
    聚合根：路径中带 {tmdb-N} / [tmdb-N] 标记的那一级（Season 子目录并入），否则整个父目录。"""
    if "/" in str(path):
        d, name = str(path).rsplit("/", 1)
    else:
        if not fallback_root:
            return None
        d, name = fallback_root, str(path)
    segs = d.split("/")
    root = None
    for i in range(len(segs) - 1, -1, -1):
        if "{tmdb-" in segs[i] or "[tmdb-" in segs[i]:
            root = "/".join(segs[: i + 1])
            break
    if root is None:
        root = d
    return root, name


def aggregate_works(common_path: str, files: List[Dict[str, Any]], video_ext: Any = None) -> Dict[str, Dict[str, Any]]:
    """把归一化的文件列表按作品聚合（对齐参考项目）：
    带 {tmdb-N}/[tmdb-N] 的那一级为聚合根，Season 子目录并入；返回 dir -> 作品信息。
    video_ext：视频扩展名判定集合（默认 VIDEO_EXT，可传 video_ext_set(用户自定义) 的合并结果）。"""
    ext_set = frozenset(video_ext) if video_ext else VIDEO_EXT
    groups: Dict[str, Dict[str, Any]] = {}
    fallback_root = common_path.rsplit("/", 1)[-1] if common_path else ""
    for fi in files:
        if not isinstance(fi, dict):
            continue
        split = split_work(str(fi.get("path") or ""), fallback_root)
        if split is None:
            continue
        root, name = split
        g = groups.get(root)
        if g is None:
            g = groups[root] = {"files": [], "count": 0, "video_count": 0, "total_size": 0}
        g["files"].append(fi)
        g["count"] += 1
        g["total_size"] += int(fi.get("size") or 0)
        if os.path.splitext(name)[1].lower() in ext_set:
            g["video_count"] += 1

    works: Dict[str, Dict[str, Any]] = {}
    for d, g in groups.items():
        title, year, tmdb_id = parse_dir_name(d)
        pinyin_full, pinyin_first = pinyin_keys(title)
        resolution, edition = infer_technical(
            str(fi.get("path") or "").rsplit("/", 1)[-1] for fi in g["files"])
        works[d] = {
            "dir": d,
            "title": title,
            "year": year,
            "tmdb_id": tmdb_id,
            "count": g["count"],
            "video_count": g["video_count"],
            "total_size": g["total_size"],
            "pinyin": pinyin_full,
            "pinyin_first": pinyin_first,
            "resolution": resolution,
            "edition": edition,
            "files": g["files"],
        }
    return works


# TMDB origin_country（ISO-3166 代码）→ 优爱腾式中文地区桶。取首个命中，未匹配归「其他」。
_REGION_MAP = {
    "CN": "华语",
    "HK": "港台", "MO": "港台", "TW": "港台",
    "JP": "日韩", "KR": "日韩",
    "US": "欧美", "GB": "欧美", "FR": "欧美", "DE": "欧美", "IT": "欧美", "ES": "欧美",
    "CA": "欧美", "RU": "欧美", "AU": "欧美", "IN": "欧美", "BR": "欧美", "SE": "欧美",
    "DK": "欧美", "NL": "欧美", "BE": "欧美", "PL": "欧美", "IE": "欧美", "NZ": "欧美",
    "MX": "欧美", "AR": "欧美", "NO": "欧美", "PT": "欧美", "AT": "欧美", "CH": "欧美",
    "CZ": "欧美", "HU": "欧美", "UA": "欧美", "TR": "欧美", "ZA": "欧美", "EG": "欧美",
}


def normalize_region(codes: Any) -> str:
    """把 TMDB origin_country 代码列表归一成中文地区桶（华语/港台/日韩/欧美/其他）。

    接受字符串或列表（如 'CN' / ['US','GB']），大小写不敏感，取首个命中；
    空或全未匹配返回「其他」。"""
    if isinstance(codes, str):
        items = [codes]
    elif isinstance(codes, (list, tuple, set)):
        items = [str(c) for c in codes]
    else:
        items = []
    region = ""
    for code in items:
        c = str(code or "").strip().upper()
        if not c:
            continue
        hit = _REGION_MAP.get(c)
        if hit:
            return hit
        if not region:  # 记住「有值但未映射」→ 其他
            region = "其他"
    return region or ""


# TMDB original_language（ISO-639-1）→ 中文语言桶；未映射但有值 → 其他；空 → 空串。
_LANG_MAP = {
    "zh": "中文", "yue": "粤语", "en": "英语", "ja": "日语", "ko": "韩语",
    "fr": "法语", "de": "德语", "es": "西班牙语", "it": "意大利语", "ru": "俄语",
    "hi": "印地语", "th": "泰语", "pt": "葡萄牙语", "sv": "瑞典语", "da": "丹麦语",
    "nl": "荷兰语", "pl": "波兰语", "tr": "土耳其语", "ar": "阿拉伯语", "id": "印尼语",
    "vi": "越南语", "ta": "泰米尔语", "te": "泰卢固语", "hu": "匈牙利语", "cs": "捷克语",
    "no": "挪威语", "el": "希腊语", "he": "希伯来语", "ro": "罗马尼亚语", "uk": "乌克兰语",
    "fa": "波斯语", "fi": "芬兰语", "bn": "孟加拉语", "ms": "马来语", "zh-cn": "中文",
}


def normalize_language(code: Any) -> str:
    c = str(code or "").strip().lower()
    if not c:
        return ""
    return _LANG_MAP.get(c, "其他")


def normalize_air_status(matched_type: str, info: Dict[str, Any]) -> str:
    """剧集更新状态（中文桶）；电影返回空串。取 TMDB in_production / status。"""
    if matched_type != "tv":
        return ""
    if info.get("in_production"):
        return "更新中"
    status = str(info.get("status") or "")
    if status == "Ended":
        return "已完结"
    if status == "Returning Series":
        return "更新中"
    if status == "Canceled":
        return "已停更"
    if status in ("Planned", "In Production"):
        return "未开播"
    return ""


# 文件名 → 分辨率（取命中的最高档）
_RESOLUTIONS = (
    (4, "4K", ("2160P", "4K", "UHD")),
    (3, "1080p", ("1080P", "1080I", "FHD")),
    (2, "720p", ("720P", "HD")),
    (1, "SD", ("480P", "576P", "SDTV")),
)
# 文件名 → 片源版本（取命中的最高优先级）
_EDITIONS = (
    (6, "REMUX", ("REMUX",)),
    (5, "BluRay", ("BLURAY", "BLUE-RAY", "蓝光", "BDMV", "BDRIP")),
    (4, "WEB-DL", ("WEB-DL", "WEBDL", "WEBRIP", "WEB RIP")),
    (3, "HDTV", ("HDTV",)),
    (2, "HDrip", ("HDRIP", "BRRIP", "DVDRIP", "DVDSCR", "PDTV")),
    (1, "其他", ("CAM", "TS", "SCREENER", "SCRE")),
)


def _scan_resolution(upper_name: str) -> Tuple[int, str]:
    for rank, label, marks in _RESOLUTIONS:
        if any(m in upper_name for m in marks):
            return rank, label
    return 0, ""


def _scan_edition(upper_name: str) -> Tuple[int, str]:
    for rank, label, marks in _EDITIONS:
        if any(m in upper_name for m in marks):
            return rank, label
    return 0, ""


def new_tech_state() -> Dict[str, Any]:
    """流式导入按作品增量累积分辨率/版本的状态袋。"""
    return {"res_rank": 0, "resolution": "", "ed_rank": 0, "edition": ""}


def update_tech_state(state: Dict[str, Any], name: str) -> Dict[str, Any]:
    upper = str(name or "").upper()
    if not upper:
        return state
    rank, label = _scan_resolution(upper)
    if rank > state["res_rank"]:
        state["res_rank"], state["resolution"] = rank, label
    erank, elabel = _scan_edition(upper)
    if erank > state["ed_rank"]:
        state["ed_rank"], state["edition"] = erank, elabel
    return state


def tech_result(state: Dict[str, Any]) -> Tuple[str, str]:
    return str(state.get("resolution") or ""), str(state.get("edition") or "")


def infer_technical(names: Iterable[str]) -> Tuple[str, str]:
    """扫描一个作品的全部文件名，返回（最高分辨率, 最高优先级片源版本）。"""
    state = new_tech_state()
    for name in names:
        update_tech_state(state, name)
    return tech_result(state)


