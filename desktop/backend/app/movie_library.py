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

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts",
    ".m2ts", ".m2t", ".mts", ".mpg", ".mpeg", ".rm", ".rmvb", ".iso",
    ".vob", ".3gp", ".asf", ".divx", ".f4v",
}

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
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or entry.get("fileName") or "").replace("\\", "/").strip().strip("/")
        if not path or set(path.split("/")) & {".", ".."}:
            continue
        files.append({
            "path": path,
            "fileName": path.rsplit("/", 1)[-1],
            "etag": _normalize_etag(entry.get("etag"), uses_base62),
            "size": int(entry.get("size") or 0),
            "s3KeyFlag": str(entry.get("s3KeyFlag") or ""),
        })
    return {"commonPath": str(common_path or "").strip().strip("/"), "files": files}


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


def split_category(dir_path: str) -> Tuple[str, str]:
    """取作品目录的一级/二级分类（二级只在作品之上有更深层级时才有意义）。"""
    segs = [s for s in str(dir_path or "").split("/") if s]
    cat = segs[0] if segs else ""
    sub = segs[1] if len(segs) > 2 and len(segs) > 1 and segs[1] else ""
    return cat, sub


def aggregate_works(common_path: str, files: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把归一化的文件列表按作品聚合（对齐参考项目）：
    带 {tmdb-N}/[tmdb-N] 的那一级为聚合根，Season 子目录并入；返回 dir -> 作品信息。"""
    groups: Dict[str, Dict[str, Any]] = {}
    fallback_root = common_path.rsplit("/", 1)[-1] if common_path else ""
    for fi in files:
        if not isinstance(fi, dict):
            continue
        path = str(fi.get("path") or "")
        if "/" not in path:
            if not fallback_root:
                continue
            d, name = fallback_root, path
        else:
            d, name = path.rsplit("/", 1)
        segs = d.split("/")
        root = None
        for i in range(len(segs) - 1, -1, -1):
            if "{tmdb-" in segs[i] or "[tmdb-" in segs[i]:
                root = "/".join(segs[: i + 1])
                break
        if root is None:
            root = d
        g = groups.get(root)
        if g is None:
            g = groups[root] = {"files": [], "count": 0, "video_count": 0, "total_size": 0}
        g["files"].append(fi)
        g["count"] += 1
        g["total_size"] += int(fi.get("size") or 0)
        if os.path.splitext(name)[1].lower() in VIDEO_EXT:
            g["video_count"] += 1

    works: Dict[str, Dict[str, Any]] = {}
    for d, g in groups.items():
        title, year, tmdb_id = parse_dir_name(d)
        pinyin_full, pinyin_first = pinyin_keys(title)
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
            "files": g["files"],
        }
    return works


