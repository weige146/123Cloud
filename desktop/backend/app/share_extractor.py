"""分享链接提取入库：把 123 云盘公开分享链接递归爬成秒传 fastlink JSON，落进影库导入目录。

移植自参考项目「123云盘影库搜索-本地版」的 ShareExtractor（原为 urllib 同步实现，
这里改为 httpx 异步），保留：增量扫描 + 断点续传检查点（存导入目录 _checkpoints/）、
文件类型过滤、每页完成即存档。提取出的 JSON 由影库 watcher 自动加载。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .movie_library import fmt_size, hex_to_base62

logger = logging.getLogger(__name__)

SHARE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
FASTLINK_VER = "3.2.0-tdr.3"

FILE_TYPE_FILTERS: Dict[str, List[str]] = {
    "视频": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m4v", "rmvb", "rm", "webm", "m2ts", "vob", "mpg", "mpeg", "3gp", "f4v"],
    "字幕": ["srt", "ass", "ssa", "sub", "sup", "idx", "smi", "srtx", "vtt"],
    "音频": ["mp3", "flac", "wav", "aac", "ogg", "m4a", "wma", "ape", "alac", "opus", "mka"],
    "图片": ["jpg", "jpeg", "png", "bmp", "gif", "webp", "ico", "tiff", "tif", "heic", "svg"],
    "文档": ["txt", "pdf", "nfo", "doc", "docx", "xls", "xlsx", "epub", "mobi", "info"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"],
}


def get_file_type_category(filename: str) -> str:
    """按扩展名返回类型分类名，未匹配返回「其他」。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for cat, exts in FILE_TYPE_FILTERS.items():
        if ext in exts:
            return cat
    return "其他"


def match_file_type(filename: str, selected_types: Optional[List[str]]) -> bool:
    """文件是否属于选中类型；selected_types 为空表示不过滤。"""
    if not selected_types:
        return True
    return get_file_type_category(filename) in selected_types


class ShareExtractorError(RuntimeError):
    pass


class ShareExtractor:
    """从 123 分享链接提取文件列表生成秒传 JSON，支持断点续传。"""

    def __init__(self, checkpoint_dir: str = ""):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.checkpoint_dir = str(checkpoint_dir or "")
        # 完成后的入库回调：(来源名, fastlink payload) -> 入库结果；数据库优先架构下由 main 注入
        self.importer = None
        self._client: Optional[httpx.AsyncClient] = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(45.0, connect=20.0),
                follow_redirects=False,
                headers={"User-Agent": SHARE_UA, "Platform": "web"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ---- 解析输入 ----
    @staticmethod
    def parse_input(text: str) -> Tuple[str, str]:
        """从文本中提取 shareKey 和分享密码。"""
        raw = str(text or "").strip()
        if not raw:
            raise ShareExtractorError("请输入分享链接或分享 Key")
        share_pwd = ""
        pwd_m = re.search(r"(?:提取码|密码|pwd|password|code|extract)\s*[:：=]?\s*([0-9A-Za-z]+)", raw, re.I)
        if pwd_m:
            share_pwd = pwd_m.group(1)
        url_m = re.search(r"https?://[^\s<>\"']+", raw, re.I)
        source = (url_m.group(0) if url_m else raw.split()[0]).rstrip("，。；;！!")
        share_key = ""
        try:
            url = urlparse(source if re.match(r"https?://", source, re.I) else "https://" + source)
            parts = [p for p in url.path.split("/") if p]
            marker = -1
            for i, p in enumerate(parts):
                if p.lower() == "s":
                    marker = i
            if marker >= 0 and marker + 1 < len(parts):
                share_key = parts[marker + 1]
            elif parts:
                share_key = parts[-1]
            if not share_key and url.hostname and not re.match(r"123pan\.(?:com|cn)$", url.hostname or "", re.I):
                share_key = url.hostname
        except Exception:
            pass
        if not share_key:
            share_key = re.sub(r"^.*\/s\/", "", source, flags=re.I).split("/")[0].split("?")[0].split("#")[0]
        share_key = re.sub(r"\.html$", "", share_key, flags=re.I).strip()
        if not share_key or re.match(r"https?://", share_key, re.I):
            raise ShareExtractorError("无法识别分享 Key")
        return share_key, share_pwd

    # ---- 解析 share 域名 ----
    async def resolve_host(self, share_key: str) -> str:
        """探测可用的分享 API 域名；分享页 404 时回退到稳定的备用域名。"""
        client = self._http()
        for base in ["https://www.123pan.com", "https://www.123865.com"]:
            url = f"{base}/b/api/share/get?limit=1&next=0&parentFileId=0&Page=1&shareKey={share_key}"
            try:
                resp = await client.get(url)
                body = json.loads(resp.text)
                # 只要返回了 JSON（不是 404 HTML），说明该 host 可用
                if isinstance(body, dict) and ("code" in body or "data" in body):
                    return base
            except Exception:
                continue
        # 最后试访问分享页拿重定向的 share 域名
        try:
            resp = await client.get(
                f"https://www.123pan.com/s/{share_key}",
                follow_redirects=True,
                timeout=25.0,
            )
            host = str(resp.url.host or "")
            if ".share.123pan" in host or "share.123" in host:
                return "https://" + host
        except Exception:
            pass
        return "https://www.123865.com"

    # ---- 列目录 ----
    async def list_dir(self, host: str, share_key: str, share_pwd: str,
                       parent_id: str = "0", page: int = 1) -> Tuple[List[Dict[str, Any]], bool]:
        """GET /b/api/share/get 列一个目录页（≤100 条），返回 (文件列表, 是否还有下一页)。
        超时自动重试 5 次，逐次加大超时。"""
        params = [
            ("limit", "100"), ("next", "0"),
            ("orderBy", "file_name"), ("orderDirection", "asc"),
            ("parentFileId", str(parent_id)), ("Page", str(page)),
            ("shareKey", share_key),
        ]
        if share_pwd:
            params.append(("SharePwd", share_pwd))
        qs = "&".join(f"{k}={v}" for k, v in params)
        url = f"{host}/b/api/share/get?{qs}"

        client = self._http()
        body: Dict[str, Any] = {}
        last_err: Optional[Exception] = None
        for attempt in range(5):
            try:
                resp = await client.get(
                    url,
                    headers={"Referer": f"{host}/s/{share_key}"},
                    timeout=httpx.Timeout(45.0 + attempt * 15, connect=20.0),
                )
                if resp.status_code >= 400:
                    raise ShareExtractorError(f"分享接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
                body = json.loads(resp.text)
                break
            except ShareExtractorError:
                raise
            except Exception as error:
                last_err = error
                if attempt < 4:
                    await asyncio.sleep(3 * (attempt + 1))
        else:
            raise ShareExtractorError(f"请求分享接口失败（重试 5 次仍失败）：{last_err}")

        code = body.get("code", body.get("Code", 0))
        msg = body.get("message", body.get("Message", ""))
        if code and str(code) != "0":
            raise ShareExtractorError(f"分享接口报错（{code}）：{msg or '未知错误'}")

        data = body.get("data") or {}
        info_list = data.get("InfoList", data.get("infoList", []))
        if not isinstance(info_list, list):
            info_list = []
        files = []
        for item in info_list:
            fid = str(item.get("FileId", item.get("FileID", item.get("fileId", ""))))
            name = str(item.get("FileName", item.get("name", "")))
            ftype = int(item.get("Type", item.get("type", 0)) or 0)
            etag = str(item.get("Etag", item.get("etag", ""))).strip()
            size = int(item.get("Size", item.get("size", 0)) or 0)
            s3 = str(item.get("S3KeyFlag", item.get("s3KeyFlag", "")) or "")
            if not name:
                continue
            files.append({"id": fid, "name": name, "type": ftype, "etag": etag, "size": size, "s3KeyFlag": s3})
        has_more = len(files) >= 100
        return files, has_more

    # ---- 断点续传检查点 ----
    def _checkpoint_dir(self) -> str:
        path = self.checkpoint_dir or os.path.join(os.getcwd(), "library_checkpoints")
        os.makedirs(path, exist_ok=True)
        return path

    def _checkpoint_paths(self, share_key: str, selected_sig: str) -> Tuple[str, str, str]:
        sig = hashlib.md5((share_key + "|" + selected_sig).encode()).hexdigest()[:12]
        base = self._checkpoint_dir()
        return os.path.join(base, f"cp_{sig}.json"), os.path.join(base, f"temp_{sig}.jsonl"), sig

    @staticmethod
    def _selected_sig(selected_items: Optional[List[Dict[str, Any]]]) -> str:
        if selected_items:
            return "|".join(sorted(str(i.get("id", "")) for i in selected_items))
        return "all"

    def _load_checkpoint(self, share_key: str, selected_sig: str) -> Optional[Dict[str, Any]]:
        cp_path, _, _ = self._checkpoint_paths(share_key, selected_sig)
        if not os.path.exists(cp_path):
            return None
        try:
            with open(cp_path, "r", encoding="utf-8") as f:
                cp = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError):
            try:
                os.remove(cp_path)
            except OSError:
                pass
            return None
        cp["_cp_path"] = cp_path
        return cp

    @staticmethod
    def _save_checkpoint(cp: Dict[str, Any]) -> None:
        cp["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(cp["_cp_path"], "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in cp.items() if not k.startswith("_")}, f,
                      ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _delete_checkpoint(cp: Dict[str, Any]) -> None:
        cp_path = cp.get("_cp_path", "")
        temp_path = cp.get("temp_file", "")
        for path in (cp_path, temp_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # ---- 构造 fastlink JSON ----
    @staticmethod
    def build_fastlink(files: List[Dict[str, Any]], cat: str = "", sub: str = "", title: str = "") -> Dict[str, Any]:
        """按油猴脚本 buildFastlinkData 同构组装，etag hex→base62。

        title：自定义作品片名，整个分享挂到 `<title>/` 下；
        cat/sub：无 title 时给 path 加 `<cat>/<sub>/` 前缀。"""
        if not files:
            raise ShareExtractorError("分享中没有文件")
        files = [dict(f) for f in files]
        title = str(title or "").strip().strip("/")
        if title:
            for f in files:
                name = f.get("fileName") or str(f.get("path") or "").rsplit("/", 1)[-1]
                f["path"] = title + "/" + name
                f["fileName"] = name
            cat = ""
        elif cat:
            prefix = cat.rstrip("/")
            if sub:
                prefix += "/" + sub.strip("/")
            prefix += "/"
            for f in files:
                f["path"] = prefix + str(f.get("path") or "")
        # 计算公共路径
        paths = [str(f.get("path") or "") for f in files if f.get("path")]
        common = ""
        if paths:
            parts = paths[0].split("/")[:-1]
            for p in paths[1:]:
                cur = p.split("/")[:-1]
                while parts and any(parts[i] != (cur[i] if i < len(cur) else None) for i in range(len(parts))):
                    parts.pop()
            common = "/".join(parts) + "/" if parts else ""
        for f in files:
            p = str(f.get("path") or "")
            if common and p.startswith(common):
                f["path"] = p[len(common):]
            etag = str(f.get("etag") or "").strip().lower()
            if etag and re.match(r"^[0-9a-f]{32}$", etag):
                f["etag"] = hex_to_base62(etag)
        total_size = sum(int(f.get("size") or 0) for f in files)
        return {
            "scriptVersion": FASTLINK_VER,
            "exportVersion": "1.0",
            "usesBase62EtagsInExport": True,
            "commonPath": common,
            "totalFilesCount": len(files),
            "totalSize": total_size,
            "formattedTotalSize": fmt_size(total_size),
            "files": files,
        }

    # ---- 增量扫描 ----
    async def _crawl(self, host: str, share_key: str, share_pwd: str,
                     checkpoint: Dict[str, Any], progress: Dict[str, Any]) -> Tuple[int, int]:
        """增量递归遍历；每页完成即写临时文件 + 存检查点，保证断点续传数据准确。"""
        temp_path = checkpoint["temp_file"]
        selected_types = checkpoint.get("file_filters")

        mode = "a" if checkpoint.get("total_files", 0) > 0 else "w"
        with open(temp_path, mode, encoding="utf-8") as temp_fp:
            pending: List[Dict[str, Any]] = list(checkpoint.get("pending_folders") or [])
            scanned = set(checkpoint.get("scanned_folder_ids") or [])
            skipped = int(checkpoint.get("skipped") or 0)

            while pending:
                folder = pending.pop(0)
                fid = str(folder["id"])
                fpath = str(folder["path"])
                page = int(folder.get("page", 1))
                if fid in scanned:
                    continue
                while True:
                    try:
                        items, has_more = await self.list_dir(host, share_key, share_pwd, fid, page)
                    except Exception as error:
                        failed_dirs = list(checkpoint.get("failed_dirs") or [])
                        failed_dirs.append({"path": fpath, "page": page, "error": str(error)})
                        checkpoint["failed_dirs"] = failed_dirs
                        scanned.add(fid)
                        checkpoint["scanned_folder_ids"] = list(scanned)
                        progress["step"] = f"跳过失败目录：{fpath}（{error}）"
                        logger.info("影库提取：跳过失败目录 %s（%s）", fpath, error)
                        await asyncio.sleep(1)
                        break
                    new_folders = []
                    for it in items:
                        path = (fpath + "/" + it["name"]).lstrip("/")
                        if it["type"] == 1:  # 文件夹
                            new_folders.append({"id": it["id"], "path": path, "page": 1})
                        elif match_file_type(it["name"], selected_types):
                            rec = {
                                "path": path,
                                "fileName": it["name"],
                                "etag": it["etag"],
                                "size": it["size"],
                                "s3KeyFlag": it["s3KeyFlag"],
                                "type": 0,
                            }
                            temp_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            checkpoint["total_files"] = int(checkpoint.get("total_files", 0)) + 1
                        else:
                            skipped += 1

                    progress["files"] = checkpoint["total_files"]
                    progress["skipped"] = skipped
                    progress["dirs"] = len(scanned) + len(pending) + len(new_folders)
                    progress["step"] = f"遍历目录：{fpath or '/'}"

                    pending.extend(new_folders)
                    if has_more:
                        pending.insert(0, {"id": fid, "path": fpath, "page": page + 1})
                    else:
                        scanned.add(fid)

                    temp_fp.flush()
                    checkpoint["scanned_folder_ids"] = list(scanned)
                    checkpoint["pending_folders"] = pending
                    checkpoint["file_filters"] = selected_types
                    checkpoint["skipped"] = skipped
                    self._save_checkpoint(checkpoint)

                    if not has_more:
                        break
                    page += 1
        checkpoint["skipped"] = int(checkpoint.get("skipped") or 0)
        return int(checkpoint.get("total_files", 0)), skipped

    # ---- 任务编排 ----
    def start_task(self, raw_url: str, cat: str = "", sub: str = "",
                   title: str = "", selected_items: Optional[List[Dict[str, Any]]] = None,
                   file_filters: Optional[List[str]] = None, resume: bool = False) -> str:
        """创建后台提取任务，返回 task_id（GET /api/library/share/task 轮询进度）。"""
        tid = uuid.uuid4().hex[:12]
        self.tasks[tid] = {
            "taskId": tid,
            "status": "running",
            "progress": {"step": "解析分享链接…", "files": 0, "dirs": 0, "skipped": 0},
            "result": None,
            "error": None,
            "createdAt": time.time(),
        }
        coro = self._run(
            tid, raw_url, cat, sub, title, selected_items, file_filters, resume,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self.tasks[tid]["_task"] = loop.create_task(coro)
        else:
            threading.Thread(target=lambda: asyncio.run(coro), name="share-extract", daemon=True).start()
        return tid

    async def _run(self, tid: str, raw_url: str, cat: str, sub: str,
                   title: str, selected_items: Optional[List[Dict[str, Any]]],
                   file_filters: Optional[List[str]], resume: bool) -> None:
        task = self.tasks[tid]
        try:
            share_key, share_pwd = self.parse_input(raw_url)
            task["progress"]["step"] = "解析分享域名…"
            host = await self.resolve_host(share_key)
            selected_sig = self._selected_sig(selected_items)

            cp = self._load_checkpoint(share_key, selected_sig)
            if cp and resume:
                old_filters = set(cp.get("file_filters") or [])
                new_filters = set(file_filters or [])
                if old_filters != new_filters:
                    task["progress"]["step"] = "过滤设置已变更，重新开始提取…"
                    self._delete_checkpoint(cp)
                    cp = None
                else:
                    # 校验临时文件行数与计数一致，防旧数据不准
                    temp_file = cp.get("temp_file", "")
                    actual_lines = 0
                    if temp_file and os.path.exists(temp_file):
                        with open(temp_file, "r", encoding="utf-8") as tf:
                            actual_lines = sum(1 for line in tf if line.strip())
                    if actual_lines != int(cp.get("total_files", 0)):
                        task["progress"]["step"] = "检查点数据不一致，重新开始提取…"
                        self._delete_checkpoint(cp)
                        cp = None
                    else:
                        task["progress"]["step"] = f"从检查点恢复…（已扫描 {cp.get('total_files', 0)} 个文件）"
                        task["progress"]["files"] = int(cp.get("total_files", 0))
                        task["progress"]["skipped"] = int(cp.get("skipped", 0))
            elif cp and not resume:
                self._delete_checkpoint(cp)
                cp = None

            if not cp:
                cp_path, temp_path, sig = self._checkpoint_paths(share_key, selected_sig)
                pending: List[Dict[str, Any]] = []
                direct_files: List[Dict[str, Any]] = []
                if selected_items:
                    for item in selected_items:
                        item_type = int(item.get("type", 0) or 0)
                        item_id = str(item.get("id", ""))
                        item_name = str(item.get("name", ""))
                        if item_type == 1:
                            pending.append({"id": item_id, "path": item_name, "page": 1})
                        elif match_file_type(item_name, file_filters):
                            direct_files.append({
                                "path": item_name,
                                "fileName": item_name,
                                "etag": str(item.get("etag", "")),
                                "size": int(item.get("size", 0) or 0),
                                "s3KeyFlag": str(item.get("s3KeyFlag", "")),
                                "type": 0,
                            })
                else:
                    pending = [{"id": "0", "path": "", "page": 1}]
                with open(temp_path, "w", encoding="utf-8") as tf:
                    for f in direct_files:
                        tf.write(json.dumps(f, ensure_ascii=False) + "\n")
                cp = {
                    "_cp_path": cp_path,
                    "share_key": share_key,
                    "host": host,
                    "selected_sig": selected_sig,
                    "file_filters": file_filters,
                    "scanned_folder_ids": [],
                    "pending_folders": pending,
                    "total_files": len(direct_files),
                    "skipped": 0,
                    "temp_file": temp_path,
                    "cat": cat, "sub": sub, "title": title,
                }
                self._save_checkpoint(cp)

            task["progress"]["files"] = int(cp.get("total_files", 0))
            task["progress"]["step"] = "正在增量扫描…"
            task["progress"]["skipped"] = int(cp.get("skipped", 0))

            total, skipped = await self._crawl(host, share_key, share_pwd, cp, task["progress"])
            if total == 0:
                self._delete_checkpoint(cp)
                raise ShareExtractorError("分享中没有可提取的文件（可能被类型过滤全部跳过）")

            task["progress"]["step"] = f"生成秒传 JSON…（共 {total} 个文件）"
            files: List[Dict[str, Any]] = []
            with open(cp["temp_file"], "r", encoding="utf-8") as tf:
                for line in tf:
                    line = line.strip()
                    if line:
                        files.append(json.loads(line))

            out = self.build_fastlink(files, cat, sub, title)
            fname = f"导入-{share_key[:8]}-{time.strftime('%Y%m%d-%H%M%S')}"
            failed_dirs = list(cp.get("failed_dirs") or [])
            self._delete_checkpoint(cp)

            imported = None
            if self.importer is not None:
                imported = self.importer(fname, out)  # 数据库优先：提取结果直接入库
            else:  # 兜底：未注入入库回调时落盘到当前目录
                with open(f"{fname}.123fastlink.json", "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
            logger.info(
                "影库提取：完成 %s（%d 个文件，%s，跳过 %d 个，失败目录 %d 个）%s",
                share_key, out["totalFilesCount"], out["formattedTotalSize"], skipped, len(failed_dirs),
                f"，入库新增 {imported['added']} 个" if imported else "",
            )

            task["status"] = "done"
            task["result"] = {
                "file": fname,
                "totalFilesCount": out["totalFilesCount"],
                "totalSize": out["totalSize"],
                "formattedTotalSize": out["formattedTotalSize"],
                "commonPath": out["commonPath"],
                "shareKey": share_key,
                "skipped": skipped,
                "failedDirs": len(failed_dirs),
                "imported": imported,
            }
            fail_msg = f" · {len(failed_dirs)} 个目录跳过" if failed_dirs else ""
            task["progress"]["step"] = (
                f"已完成！{out['totalFilesCount']} 个文件 · 跳过 {skipped} 个{fail_msg} · {out['formattedTotalSize']}"
            )
        except Exception as error:
            task["status"] = "error"
            task["error"] = str(error)
            task["progress"]["step"] = f"失败：{error}"
            logger.warning("影库提取：任务失败 %s", error)

    def get_task(self, tid: str) -> Optional[Dict[str, Any]]:
        task = self.tasks.get(tid)
        if task is None:
            return None
        return {k: v for k, v in task.items() if not k.startswith("_")}

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """最近的提取任务（按创建时间倒序）。"""
        tasks = [self.get_task(tid) for tid in self.tasks]
        tasks = [t for t in tasks if t]
        tasks.sort(key=lambda t: t.get("createdAt", 0), reverse=True)
        return tasks[: max(1, limit)]

    # ---- 检查点查询 / 删除 ----
    def check_checkpoint(self, raw_url: str, selected_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        try:
            share_key, _ = self.parse_input(raw_url)
            cp = self._load_checkpoint(share_key, self._selected_sig(selected_items))
            if cp:
                return {
                    "hasCheckpoint": True,
                    "totalFiles": int(cp.get("total_files", 0)),
                    "skipped": int(cp.get("skipped", 0)),
                    "pendingFolders": len(cp.get("pending_folders") or []),
                    "scannedFolders": len(cp.get("scanned_folder_ids") or []),
                    "failedDirs": len(cp.get("failed_dirs") or []),
                    "updatedAt": str(cp.get("updated_at") or ""),
                }
        except Exception:
            pass
        return {"hasCheckpoint": False}

    def delete_checkpoint(self, raw_url: str, selected_items: Optional[List[Dict[str, Any]]] = None) -> bool:
        try:
            share_key, _ = self.parse_input(raw_url)
            cp = self._load_checkpoint(share_key, self._selected_sig(selected_items))
            if cp:
                self._delete_checkpoint(cp)
                return True
        except Exception:
            pass
        return False


extractor = ShareExtractor()
