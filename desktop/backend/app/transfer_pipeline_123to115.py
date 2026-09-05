"""123 → 115 搬运管线（只走秒传通道，镜像 transfer_pipeline.py 的主线结构）。

一次任务的执行分四个阶段，日志保持大白话：

  0 中转   来源是 123 分享链接时，先用 OpenAPI 把分享内容按 MD5 秒传到
           本机网盘的「秒传」目录（分享列表自带文件 MD5=Etag，
           内容本就在 123 服务器上，正常全部命中），任务结束移入回收站
  1 解析   递归展开 123 源目录（OpenAPI）成文件清单
  2 规划   在 115 目标目录下按原路径建目录，先挑出已经存在、无需搬运的文件
  3 秒传   用本地 transfer_hashes 学习表把 (etag, size) / (文件名, size)
           反查成 SHA1，调 115 uplb 4.0/initupload 秒传
  4 收尾   汇总结果；无法秒传的文件直接标记失败（终态通知会说明原因）

为什么不做离线下载兜底：115 离线拉 123 直链对大文件极慢（实测 100GB+
需要 1-2 天）且直链容易被 115 服务器拒收；秒传通道对 115→123 搬运过的
媒体文件命中率很高。反向秒传只对"曾经从 115 搬进 123"的文件可用——
123 的接口不返回文件 SHA1（只有 Etag=MD5），学习表在 115→123 方向
搬运时记录了 (SHA1, 大小) ↔ Etag 的对应关系。

123 侧全部走 OpenAPI（OAuth 授权 token，自动刷新）：自己的目录直接列，
分享链接先中转秒传成自己的文件，直链（二次验证取片段）也就都走官方
OpenAPI download_info，不再有网页直链被拦截页接管的问题。

本模块不 import TransferService，只通过构造传入的 service 对象回调。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx

from .pan115 import Pan115Client, select_115_account
from .pan115_transfer import PAN123_OFFLINE_USER_AGENT
from .pan123 import Pan123OpenAPIClient
from .transfer_pipeline import (
    TaskCancelled,
    _add_task_log,
    _delay,
    _display_path,
    _format_bytes,
    _format_elapsed,
    _is_done_file,
    _utc_now_iso,
)

# 安全阀：单任务最多展开的文件数 / 目录深度，防止误填根目录把全盘搬走
MAX_FILES_PER_TASK = 20000
MAX_DIR_DEPTH = 32
# 二次验证（status 7）要读的直链字节片段上限，防御异常大的 sign_check
SIGN_CHECK_MAX_BYTES = 32 * 1024 * 1024
# 分享中转：秒传逐文件间隔与根目录名
STAGE_FILE_INTERVAL_MS = 300
STAGE_ROOT_NAME = "秒传"


class Pan123to115Error(RuntimeError):
    pass


class TransferPipeline123to115:
    def __init__(self, service: Any, task: Dict[str, Any], transfer_config: Dict[str, Any]) -> None:
        self.service = service
        self.task = task
        self.config = transfer_config
        # sourceDirId 只在入队时的内存对象里；落库后靠 shareUrl（123://dir?id=）恢复
        self.source_root_id = str(task.get("sourceDirId") or "").strip()
        if not self.source_root_id:
            match = re.match(r"123://dir\?id=(\d+)", str(task.get("shareUrl") or ""))
            self.source_root_id = match.group(1) if match else ""
        if not self.source_root_id and not self.is_share_source:
            # 默认从 115→123 搬运的落地目录往回搬
            self.source_root_id = str(transfer_config.get("targetDirId") or "0").strip() or "0"
        self.target_root_cid = str(task.get("targetDirId") or transfer_config.get("pan115TargetCid") or "0").strip() or "0"
        self.pan123: Pan123OpenAPIClient = None  # type: ignore[assignment]
        self.pan115: Pan115Client = None  # type: ignore[assignment]
        # 规划阶段建立的 115 目录与 listing 缓存
        self._dir_cache: Dict[Tuple[str, ...], str] = {}
        self._listing_cache: Dict[str, List[Dict[str, Any]]] = {}
        # 分享中转：本任务创建的中转目录 ID、已中转文件数与秒传失败清单
        self._staged_root_id = ""
        self._staged_count = 0
        self._stage_failures: List[Dict[str, Any]] = []
        self.started_ms = time.monotonic() * 1000

    @property
    def is_share_source(self) -> bool:
        share_url = str(self.task.get("shareUrl") or "").strip()
        return bool(share_url) and not share_url.startswith("123://dir")

    # ------------------------------------------------------------------
    async def run(self) -> None:
        task = self.task
        try:
            self.pan123 = await self.service._create_pan123_client()
            if self.is_share_source:
                self.source_root_id = await self._stage_share_to_drive()
            self.pan115 = self._create_pan115_client()
            if self.is_share_source:
                _add_task_log(
                    task, "info",
                    f"开始把 123 分享搬往 115（CID {self.target_root_cid}），只走秒传通道",
                )
            else:
                _add_task_log(
                    task, "info",
                    f"开始把 123 目录（ID {self.source_root_id}）搬往 115（CID {self.target_root_cid}），"
                    f"123 侧使用 123 OpenAPI（已授权账号），只走秒传通道",
                )

            files = await self.phase_inspect()
            if not files or task.get("status") in ("success", "partial", "failed"):
                return
            todo = await self.phase_plan(files)
            await self.phase_reuse(todo)
            await self.phase_finalize()
        finally:
            await self._cleanup_staged()

    # ------------------------------------------------------------------
    # 客户端构建
    # ------------------------------------------------------------------
    def _create_pan115_client(self) -> Pan115Client:
        submission = self.service._store.read_submission_config()
        helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
        try:
            account = select_115_account(helper)
        except Exception as error:
            raise Pan123to115Error(
                "123→115 搬运需要使用 115 助手的账号 Cookie 作为目标账号，请先在后台“115 助手”里配置"
            ) from error
        return Pan115Client(account["cookie"], int(self.config.get("requestIntervalMs") or 2500))

    # ------------------------------------------------------------------
    # 阶段 0：分享中转（MD5 秒传到本机网盘的临时目录）
    # ------------------------------------------------------------------
    async def _stage_share_to_drive(self) -> str:
        task = self.task
        share_url = str(task.get("shareUrl") or "").strip()
        share_password = str(task.get("receiveCode") or "").strip()
        share_key = str(task.get("shareCode") or "share").strip() or "share"
        await self._trash_legacy_stage_root()
        stage_root = await self.pan123.ensure_path("0", [STAGE_ROOT_NAME, f"{share_key}-{uuid4().hex[:6]}"])
        self._staged_root_id = str(stage_root)
        _add_task_log(
            task, "info",
            f"先把分享内容按 MD5 秒传到 123 中转目录「{STAGE_ROOT_NAME}/{share_key}」（不耗带宽，任务结束自动清理）",
        )
        root_items = await self.service._pan123_share_client.list_share_root_items(share_url, share_password)
        if not root_items:
            raise Pan123to115Error("123 分享根目录为空或已失效，无法搬运")
        await self._stage_share_items(root_items, str(stage_root), [])
        if self._stage_failures:
            _add_task_log(
                task, "warn",
                f"中转完成，{len(self._stage_failures)} 个文件 MD5 秒传未命中（将按失败处理）",
            )
        return str(stage_root)

    async def _stage_share_items(self, items: List[Dict[str, Any]], target_dir_id: str, path: List[str]) -> None:
        task = self.task
        share_url = str(task.get("shareUrl") or "").strip()
        share_password = str(task.get("receiveCode") or "").strip()
        for item in items:
            if self._staged_count >= MAX_FILES_PER_TASK or len(path) >= MAX_DIR_DEPTH:
                return
            name = str(item.get("FileName") or item.get("filename") or item.get("name") or "").strip()
            if not name:
                continue
            if int(item.get("Type") or item.get("type") or 0) == 1:
                try:
                    dir_id = await self.pan123.create_folder(target_dir_id, name)
                except Exception as error:
                    self._stage_failures.append({"path": [*path, name], "name": name, "size": 0, "reason": f"创建中转目录失败：{error}"})
                    _add_task_log(task, "warn", f"创建中转目录失败：{'/'.join([*path, name])}（{error}）")
                    continue
                child_items = await self.service._pan123_share_client.list_share_items(
                    share_url, share_password, str(item.get("FileId") or item.get("fileId") or "")
                )
                await self._stage_share_items(child_items, str(dir_id), [*path, name])
                continue
            size = int(item.get("Size") or item.get("BaseSize") or item.get("size") or 0)
            etag = str(item.get("Etag") or item.get("etag") or "").strip()
            try:
                reused = await self.pan123.md5_reuse(target_dir_id, name, etag, size)
                if not reused:
                    self._stage_failures.append({
                        "path": [*path, name], "name": name, "size": size,
                        "reason": "123 服务器上没有相同内容（MD5），中转秒传未命中",
                    })
            except Exception as error:
                reused = 0
                self._stage_failures.append({"path": [*path, name], "name": name, "size": size, "reason": f"中转秒传失败：{error}"})
                _add_task_log(task, "warn", f"中转秒传失败：{'/'.join([*path, name])}（{error}）")
            self._staged_count += 1
            if self._staged_count % 20 == 0:
                _add_task_log(task, "info", f"中转进度：已处理 {self._staged_count} 个文件")
            await _delay(STAGE_FILE_INTERVAL_MS)

    async def _trash_legacy_stage_root(self) -> None:
        """清理改名前遗留的旧中转目录（best effort）。"""
        legacy_names = ("123分享转存中转", "秒传文件夹")
        try:
            for entry in await self.pan123.list_files("0"):
                if int(entry.get("type") or 0) != 1:
                    continue
                name = str(entry.get("name") or "")
                if name not in legacy_names:
                    continue
                legacy_id = int(entry.get("fileId") or entry.get("id") or 0)
                if legacy_id > 0:
                    await self.pan123.trash_files([legacy_id])
                    _add_task_log(self.task, "info", f"已清理旧中转目录「{name}」（移入回收站）")
        except Exception as error:
            _add_task_log(self.task, "warn", f"旧中转目录清理失败（可手动删除）：{error}")

    async def _cleanup_staged(self) -> None:
        """任务结束后把中转目录整体移入回收站（可恢复，失败时提示手动清理）。"""
        root_id = self._staged_root_id
        self._staged_root_id = ""
        if not root_id or self.pan123 is None:
            return
        try:
            await self.pan123.trash_files([int(root_id)])
            _add_task_log(self.task, "info", f"已清理 123 中转目录（移入回收站）")
        except Exception as error:
            _add_task_log(self.task, "warn", f"中转目录清理失败（可手动删除「{STAGE_ROOT_NAME}」）：{error}")

    # ------------------------------------------------------------------
    # 阶段 1：解析（递归展开 123 源目录成文件清单）
    # ------------------------------------------------------------------
    async def phase_inspect(self) -> List[Dict[str, Any]]:
        task = self.task
        files: List[Dict[str, Any]] = []
        visited: set = set()

        async def visit(dir_id: str, path: List[str]) -> None:
            if len(files) >= MAX_FILES_PER_TASK or len(path) >= MAX_DIR_DEPTH:
                return
            dir_key = str(dir_id or "0")
            if dir_key in visited:
                return
            visited.add(dir_key)
            entries = await self.pan123.list_files(dir_key)
            for entry in entries:
                name = str(entry.get("name") or entry.get("filename") or "").strip()
                if not name:
                    continue
                if int(entry.get("type") or 0) == 1:
                    child_id = str(entry.get("fileId") or entry.get("id") or "").strip()
                    if child_id:
                        await visit(child_id, [*path, name])
                    continue
                if len(files) >= MAX_FILES_PER_TASK:
                    return
                files.append({
                    "id": str(entry.get("fileId") or entry.get("id") or ""),
                    "fileId": int(entry.get("fileId") or entry.get("id") or 0),
                    "name": name,
                    "size": int(entry.get("size") or 0),
                    "etag": str(entry.get("etag") or ""),
                    "s3KeyFlag": str(entry.get("s3KeyFlag") or ""),
                    "type": 0,
                    "path": list(path),
                    "status": "pending",
                })

        await visit(self.source_root_id, [])
        # 中转秒传未命中的文件不在中转目录里，这里补进清单并直接标记失败
        for failure in self._stage_failures:
            files.append({
                "id": "",
                "fileId": 0,
                "name": str(failure.get("name") or ""),
                "size": int(failure.get("size") or 0),
                "etag": "",
                "s3KeyFlag": "",
                "type": 0,
                "path": list(failure.get("path") or []),
                "status": "failed",
                "method": "123_stage",
                "error": str(failure.get("reason") or "中转秒传未命中"),
                "finishedAt": _utc_now_iso(),
            })
        task["files"] = list(files)
        task["totalFiles"] = len(files)
        task["doneFiles"] = len([f for f in files if _is_done_file(f)])
        total_size = sum(int(f.get("size") or 0) for f in files)
        _add_task_log(
            task, "info",
            f"解析完成：共 {len(files)} 个文件，合计 {_format_bytes(total_size)}",
        )
        if not await self.service._save_transfer_task(task):
            raise TaskCancelled()
        return files

    # ------------------------------------------------------------------
    # 阶段 2：规划（115 侧建目录 + 挑出已存在文件）
    # ------------------------------------------------------------------
    async def _resolve_115_dir(self, path: List[str]) -> str:
        key = tuple(str(part) for part in path)
        cached = self._dir_cache.get(key)
        if cached:
            return cached
        current = self.target_root_cid
        for index, part in enumerate(key):
            prefix = key[: index + 1]
            cached = self._dir_cache.get(prefix)
            if cached:
                current = cached
                continue
            current = await self._ensure_local_dir_cached(current, part)
            self._dir_cache[prefix] = current
        return current

    async def _ensure_local_dir_cached(self, parent_cid: str, name: str) -> str:
        listing = await self._list_115_dir(parent_cid)
        for entry in listing:
            if entry.get("isDir") and entry.get("name") == name:
                return str(entry.get("fid") or "")
        cid = await self.pan115.mkdir_local_dir(parent_cid, name)
        self._listing_cache.pop(parent_cid, None)
        return cid

    async def _list_115_dir(self, cid: str) -> List[Dict[str, Any]]:
        cached = self._listing_cache.get(str(cid))
        if cached is not None:
            return cached
        entries = await self.pan115.list_local_entries(cid)
        self._listing_cache[str(cid)] = entries
        return entries

    async def phase_plan(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        task = self.task
        todo: List[Dict[str, Any]] = []
        skipped = 0
        groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        for file in [f for f in files if not _is_done_file(f)]:
            groups.setdefault(tuple(str(part) for part in file.get("path", [])), []).append(file)

        for path, group in groups.items():
            target_cid = await self._resolve_115_dir(list(path))
            listing = await self._list_115_dir(target_cid)
            existing = {str(entry.get("name") or ""): entry for entry in listing if not entry.get("isDir")}
            for file in group:
                file["targetCid"] = target_cid
                hit = existing.get(str(file.get("name") or ""))
                if hit and int(hit.get("size") or 0) == int(file.get("size") or 0):
                    file["status"] = "skipped"
                    file["method"] = "exists"
                    file["pan115FileId"] = str(hit.get("fid") or "")
                    file["finishedAt"] = _utc_now_iso()
                    skipped += 1
                    continue
                todo.append(file)

        if todo and skipped:
            _add_task_log(task, "info", f"规划完成：{skipped} 个文件 115 里已有直接跳过，待搬运 {len(todo)} 个")
        elif skipped:
            _add_task_log(task, "info", f"规划完成：{skipped} 个文件 115 里已经有了，全部跳过")
        elif todo:
            _add_task_log(task, "info", f"规划完成：待搬运 {len(todo)} 个文件")
        task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
        if not await self.service._save_transfer_task(task):
            raise TaskCancelled()
        return todo

    # ------------------------------------------------------------------
    # 阶段 3：秒传（学习表反查 SHA1 → 115 upload/init）
    # ------------------------------------------------------------------
    async def phase_reuse(self, todo: List[Dict[str, Any]]) -> None:
        task = self.task
        counters = {"reused": 0, "failed": 0}
        if not todo:
            return

        lock = asyncio.Lock()
        next_idx = 0
        concurrency = max(1, min(self.service._max_offline_slots(self.config), len(todo)))

        async def worker() -> None:
            nonlocal next_idx
            while True:
                async with lock:
                    index = next_idx
                    next_idx += 1
                if index >= len(todo):
                    return
                if await self._reuse_one(todo[index]):
                    counters["reused"] += 1
                else:
                    counters["failed"] += 1
                task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
                if not await self.service._save_transfer_task(task):
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

        if counters["failed"]:
            _add_task_log(
                task, "info",
                f"秒传完成：成功 {counters['reused']} 个，无法秒传 {counters['failed']} 个"
                f"（原因见文件明细；只有经 115→123 秒传搬运过、且 115 服务器上存在相同内容的文件才能反向秒传）",
            )
        else:
            _add_task_log(task, "info", f"秒传完成：成功 {counters['reused']} 个")

    async def _reuse_one(self, file: Dict[str, Any]) -> bool:
        """尝试秒传单个文件；返回 True 表示成功，失败标记原因并通知。"""
        task = self.task
        display = _display_path(file)

        def mark_failed(reason: str) -> bool:
            file["status"] = "failed"
            file["method"] = "sha1_fast"
            file["error"] = reason
            file["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "warn", f"无法秒传：{display}（{reason}）")
            return False

        etag = str(file.get("etag") or "").strip().lower()
        size = int(file.get("size") or 0)
        name = str(file.get("name") or "")
        store = self.service._store
        # 本地学习表：(etag, size) 与 (name, size) 两个反查方向
        learned = store.get_transfer_sha1_by_etag(etag, size) if etag else None
        if not learned:
            learned = store.get_transfer_sha1_by_name(name, size)
        if not learned or not learned.get("sha1"):
            return mark_failed(
                "本地学习表中没有该文件（需经 115→123 秒传搬运过，学习表才会记录它的 115 SHA1）"
            )
        sha1 = str(learned["sha1"])

        async def fetch_range_bytes(range_text: str) -> Optional[bytes]:
            return await self._fetch_range_bytes(file, range_text)

        try:
            result = await self.pan115.upload_init_fast(
                str(file.get("name") or "file"), sha1, int(file.get("size") or 0),
                str(file.get("targetCid") or self.target_root_cid), fetch_range_bytes,
            )
        except TaskCancelled:
            raise
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            return mark_failed(f"115 秒传接口出错：{detail}")
        if result.get("reuse"):
            file["status"] = "success"
            file["method"] = "sha1_fast"
            file["pan115FileId"] = str(result.get("fileId") or "")
            file["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "info", f"SHA1 秒传成功：{display}")
            return True
        if int(result.get("status") or 0) == 7:
            return mark_failed("115 要求二次验证但未能完成（直链片段获取失败或未通过校验）")
        return mark_failed("115 服务器上没有相同内容（SHA1）的文件")

    async def _fetch_range_bytes(self, file: Dict[str, Any], range_text: str) -> Optional[bytes]:
        """115 二次验证：从 123 OpenAPI 直链按 HTTP Range 取片段字节。

        必须带 range 头：丢了这个头会整文件下载（几 GB 进内存），既慢又炸。
        用流式读取，服务器若忽略 Range 想整包下发，按 content-length 直接拒绝。
        """
        if not re.fullmatch(r"\d+-\d+", str(range_text or "").strip()):
            _add_task_log(self.task, "warn", f"115 秒传二次验证范围异常：{_display_path(file)}（{range_text}）")
            return None
        start_text, end_text = range_text.split("-", 1)
        start, end = int(start_text), int(end_text)
        if end < start or end - start + 1 > SIGN_CHECK_MAX_BYTES:
            _add_task_log(self.task, "warn", f"115 秒传二次验证范围过大：{_display_path(file)}")
            return None
        file_id = int(file.get("fileId") or file.get("id") or 0)
        if file_id <= 0:
            return None
        url = await self.pan123.download_info(file_id)
        headers = {
            "accept": "*/*",
            "range": f"bytes={start}-{end}",
            "user-agent": PAN123_OFFLINE_USER_AGENT,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), follow_redirects=True) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    status_code = response.status_code
                    content_length = int(response.headers.get("content-length") or 0)
                    content_type = str(response.headers.get("content-type", "")).lower()
                    if status_code == 200 and content_length > (end - start + 1) + 1024 * 1024:
                        raise Pan123to115Error(
                            f"123 直链忽略 Range 请求（响应 {content_length} 字节，仅需 {end - start + 1} 字节），拒绝整包下载"
                        )
                    if status_code not in (200, 206):
                        snippet = (await response.aread())[:160]
                        raise Pan123to115Error(
                            f"123 直链取二次验证片段失败（HTTP {status_code}，片段：{snippet!r}，Range {start}-{end}）"
                        )
                    if "text/html" in content_type:
                        raise Pan123to115Error(f"123 直链返回 HTML 页（疑似被拦截），Range {start}-{end}")
                    content = await response.aread()
        except httpx.TimeoutException as error:
            raise Pan123to115Error(
                f"123 直链下载片段超时（{type(error).__name__}，Range {start}-{end}）"
            ) from error
        except httpx.HTTPError as error:
            raise Pan123to115Error(
                f"123 直链下载片段失败（{type(error).__name__}: {error or '网络错误'}，Range {start}-{end}）"
            ) from error
        if status_code == 200 and len(content) > end:
            # 服务器忽略 Range 返回了全量内容，主动切片
            content = content[start : end + 1]
        if len(content) != end - start + 1:
            raise Pan123to115Error(
                f"123 直链片段长度不符：需要 {end - start + 1} 字节，实际 {len(content)} 字节（Range {start}-{end}）"
            )
        return content

    # ------------------------------------------------------------------
    # 阶段 4：收尾
    # ------------------------------------------------------------------
    async def phase_finalize(self) -> None:
        task = self.task
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
                f"搬运完成（耗时 {elapsed_text}）：成功 {success}，跳过 {skipped}，失败 {failed}。"
                f"无法秒传的文件：{preview}",
            )
        else:
            task["status"] = "success"
            _add_task_log(
                task, "info",
                f"搬运全部完成（耗时 {elapsed_text}）：成功 {success}，跳过 {skipped}，失败 0",
            )
        task["finishedAt"] = _utc_now_iso()
