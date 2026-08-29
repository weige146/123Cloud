"""
TransferService - 115 to 123 cloud transfer service.

Translated from the legacy TypeScript project.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import httpx

from .pan115 import select_115_account
from .pan115_transfer import extract_115_links, PAN123_OFFLINE_USER_AGENT, Pan115TransferClient
from .pan123 import Pan123Client, Pan123OpenAPIClient, Pan123OpenTokenStore
from .session_store import SessionStore, pan123_open_token_key

logger = logging.getLogger(__name__)


def _normalize_non_negative_ms(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback
    try:
        parsed = int(value)
        return max(0, parsed) if math.isfinite(parsed) else fallback
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Module-level constants mapped from environment variables
# ---------------------------------------------------------------------------
DEFAULT_POLL_MS = int(os.environ.get("TRANSFER_OFFLINE_POLL_MS", "5000"))
DEFAULT_MAX_POLLS = int(os.environ.get("TRANSFER_OFFLINE_MAX_POLLS", "240"))
TRANSFER_PAUSE_TIME_ZONE = os.environ.get("TRANSFER_PAUSE_TIME_ZONE", "Asia/Shanghai")
TRANSFER_PAUSE_START_HOUR = int(os.environ.get("TRANSFER_PAUSE_START_HOUR", "18"))
TRANSFER_PAUSE_END_HOUR = int(os.environ.get("TRANSFER_PAUSE_END_HOUR", "1"))
PAN115_DOWNLOAD_MIN_INTERVAL_MS = int(os.environ.get("TRANSFER_115_DOWNLOAD_MIN_INTERVAL_MS", "2500"))
PAN115_DOWNLOAD_MAX_ATTEMPTS = int(os.environ.get("TRANSFER_115_DOWNLOAD_MAX_ATTEMPTS", "5"))
PAN115_DOWNLOAD_RETRY_BASE_MS = int(os.environ.get("TRANSFER_115_DOWNLOAD_RETRY_BASE_MS", "8000"))
PAN115_DOWNLOAD_USER_AGENT = "Mozilla/5.0 115disk/31.4.2 115Browser/31.4.2 115wangpan_android/34.0.0"
FILE_CONCURRENCY_MAX = int(os.environ.get("TRANSFER_FILE_CONCURRENCY_MAX", "5"))
SCAN_EXISTING_ORGANIZED = os.environ.get("TRANSFER_SCAN_EXISTING_ORGANIZED") == "1"
PAN123_OFFLINE_SUBMIT_NAME_MAX = int(os.environ.get("PAN123_OFFLINE_SUBMIT_NAME_MAX", "180"))
PAN123_OFFLINE_DISPLAY_PATH_MAX = int(os.environ.get("PAN123_OFFLINE_DISPLAY_PATH_MAX", "240"))
PAN123_PATH_PART_MAX = int(os.environ.get("PAN123_PATH_PART_MAX", "180"))
PAN123_SHARE_COPY_MIN_INTERVAL_MS = int(os.environ.get("PAN123_SHARE_COPY_MIN_INTERVAL_MS", "10000"))
PAN123_SHARE_COPY_MAX_ATTEMPTS = int(os.environ.get("PAN123_SHARE_COPY_MAX_ATTEMPTS", "4"))
PAN123_SHARE_COPY_RETRY_BASE_MS = int(os.environ.get("PAN123_SHARE_COPY_RETRY_BASE_MS", "10000"))
TRANSFER_PROGRESS_NOTIFY_INTERVAL_MS = _normalize_non_negative_ms(
    os.environ.get("TRANSFER_PROGRESS_NOTIFY_INTERVAL_MS"), 60_000
)
PAN123_FORBIDDEN_NAME_RE = re.compile(r'["\\/:*?|><]')


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
TransferNoticeRef = Dict[str, Any]
TransferNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, Optional[TransferNoticeRef]]]
TransferQueuedNotifier = Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, Optional[List[TransferNoticeRef]]]]
TransferCookieNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, Optional[TransferNoticeRef]]]
TransferCleanupNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]
AsyncLimiter = Callable[[Callable[[], Coroutine[Any, Any, Any]]], Coroutine[Any, Any, Any]]


# ---------------------------------------------------------------------------
# TransferService
# ---------------------------------------------------------------------------
class TransferService:
    def __init__(self, store: SessionStore, read_pan_session: Optional[Callable[[], Coroutine[Any, Any, Optional[Dict[str, Any]]]]] = None):
        self._store = store
        self._read_pan_session = read_pan_session
        self._running = False
        self._active_task_ids: Set[str] = set()
        self._active_offline_files = 0
        self._offline_slot_waiters: List[asyncio.Future[None]] = []
        self._deleted_task_ids: Set[str] = set()
        self._notifier: Optional[TransferNotifier] = None
        self._queued_notifier: Optional[TransferQueuedNotifier] = None
        self._deleted_notifier: Optional[TransferNotifier] = None
        self._cookie_notifier: Optional[TransferCookieNotifier] = None
        self._cleanup_notifier: Optional[TransferCleanupNotifier] = None
        self._pan115_download_queue: Optional[asyncio.Future[Any]] = None
        self._last_pan115_download_at = 0.0
        self._progress_notice_state: Dict[str, Dict[str, Any]] = {}
        self._warned_suspicious_offline_artifacts: Set[str] = set()
        self._cached_transfer_config: Optional[Dict[str, Any]] = None
        # 相同凭证复用同一 Pan123OpenAPIClient 实例，共享内存 token
        self._pan123_open_client: Optional[Pan123OpenAPIClient] = None
        self._pan123_open_client_key: str = ""
        self._pan123_open_client_lock: Optional[asyncio.Lock] = None
        self._pan123_web_client = Pan123Client()
        self._active_pan123_share_copy_task_id: Optional[str] = None
        self._last_pan123_share_copy_submit_at = 0.0

    def set_notifier(self, notifier: Optional[TransferNotifier]) -> None:
        self._notifier = notifier

    def set_queued_notifier(self, notifier: Optional[TransferQueuedNotifier]) -> None:
        self._queued_notifier = notifier

    def set_deleted_notifier(self, notifier: Optional[TransferNotifier]) -> None:
        self._deleted_notifier = notifier

    def set_cookie_notifier(self, notifier: Optional[TransferCookieNotifier]) -> None:
        self._cookie_notifier = notifier

    def set_cleanup_notifier(self, notifier: Optional[TransferCleanupNotifier]) -> None:
        self._cleanup_notifier = notifier

    async def init(self) -> None:
        config = self._store.read_config()
        self._remember_config(config.get("transfer"))
        self._store.reset_running_transfer_tasks()
        self.kick()

    async def close(self) -> None:
        if self._pan123_open_client_lock is None:
            self._pan123_open_client_lock = asyncio.Lock()
        async with self._pan123_open_client_lock:
            client = self._pan123_open_client
            self._pan123_open_client = None
            self._pan123_open_client_key = ""
            if client is not None:
                await client.close()
        await self._pan123_web_client.close()

    async def requeue_task(self, task_id: str) -> Dict[str, Any]:
        task = self._store.get_transfer_task(task_id)
        if not task:
            raise RuntimeError("任务不存在")
        if task.get("status") == "running":
            raise RuntimeError("任务正在运行中，请稍后再重试")
        if task.get("status") == "success":
            raise RuntimeError("任务已完成，无需重试")

        task["status"] = "queued"
        task["error"] = None
        task["finishedAt"] = None
        task["startedAt"] = None
        files = task.get("files", [])
        new_files = []
        for file in files:
            if _is_done_file(file):
                new_files.append(file)
                continue
            new_files.append({
                **file,
                "status": "pending",
                "method": None,
                "error": None,
                "startedAt": None,
                "finishedAt": None,
                "sourceUrl": None,
                "offlineTaskId": None,
                "offlineSubmitName": None,
                "offlineStatus": None,
                "offlineStatusText": None,
                "offlineProgress": None,
                "offlineSpeed": None,
                "offlineSpeedText": None,
            })
        task["files"] = new_files
        task["doneFiles"] = len([f for f in new_files if _is_done_file(f)])
        _add_task_log(task, "info", "已手动重新排队")
        self._store.save_transfer_task(task)
        self.kick()
        return task

    async def delete_task(self, task_id: str) -> None:
        task = self._store.get_transfer_task(task_id)
        if not task:
            raise RuntimeError("任务不存在")
        self._deleted_task_ids.add(task_id)
        await self._notify_deleted_task(task)
        self._store.delete_transfer_task(task_id)
        logger.info("115 搬运任务已删除", extra={"task_id": task_id, "share_code": task.get("shareCode")})

    async def enqueue_from_text(
        self,
        text: str,
        source: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        config = self._store.read_config()
        self._remember_config(config.get("transfer"))
        if not config.get("transfer", {}).get("enabled"):
            raise RuntimeError("115 搬运未启用，请先在后台开启")

        links = _unique_115_links(extract_115_links(text))
        if not links:
            return []
        if any(not str(link.get("receive_code") or "").strip() for link in links):
            raise RuntimeError("115 分享缺少提取码；请在同一条消息里带上提取码")
        pause_notice = self._current_pause_notice_for_config(config.get("transfer"))
        if pause_notice:
            raise RuntimeError(pause_notice)

        tasks: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []
        for link in links:
            now = _utc_now_iso()
            task = {
                "id": str(uuid4()),
                "source": source,
                "sourceText": text,
                "chatId": chat_id,
                "userId": user_id,
                "messageId": message_id,
                "shareUrl": link["clean_url"],
                "shareCode": link["share_code"],
                "receiveCode": link.get("receive_code"),
                "status": "queued",
                "totalFiles": 0,
                "doneFiles": 0,
                "files": [],
                "logs": [{"time": now, "level": "info", "message": "任务已入队"}],
                "createdAt": now,
                "updatedAt": now,
            }
            duplicate = await self._find_active_duplicate(task)
            if duplicate:
                duplicates.append(duplicate)
                continue
            self._store.save_transfer_task(task)
            tasks.append(task)

        if not tasks and duplicates:
            duplicate = duplicates[0]
            raise RuntimeError(
                f"该 115 分享链接已有未完成任务（{_transfer_status_label(duplicate.get('status'))}），请勿重复提交"
            )

        await self._notify_queued_tasks(tasks)
        self.kick()
        return tasks

    async def enqueue_pan123_share_copy(
        self,
        share_url: str,
        share_password: str,
        share_info: Dict[str, Any],
        source: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        config = self._store.read_config()
        transfer_config = config.get("transfer", {}) if isinstance(config.get("transfer"), dict) else {}
        target_dir_id = str(transfer_config.get("targetDirId") or "0").strip() or "0"
        now = _utc_now_iso()
        task = {
            "id": str(uuid4()),
            "kind": "pan123_share_copy",
            "source": source,
            "sourceText": share_url,
            "chatId": chat_id,
            "userId": user_id,
            "messageId": message_id,
            "shareUrl": share_url,
            "shareCode": str(share_info.get("shareKey") or ""),
            "receiveCode": str(share_password or ""),
            "title": str(share_info.get("shareName") or "123 分享转存"),
            "shareOwnerUserId": int(share_info.get("userId") or 0),
            "targetDirId": target_dir_id,
            "status": "queued",
            "totalFiles": 0,
            "doneFiles": 0,
            "files": [],
            "logs": [{"time": now, "level": "info", "message": "123 分享转存任务已入队"}],
            "createdAt": now,
            "updatedAt": now,
        }
        duplicate = await self._find_active_duplicate(task)
        if duplicate:
            raise RuntimeError(
                f"该 123 分享已有未完成转存任务（{_transfer_status_label(duplicate.get('status'))}），请勿重复提交"
            )
        self._store.save_transfer_task(task)
        await self._notify_queued_tasks([task])
        self.kick()
        return task

    async def enqueue_local_path(
        self,
        path_115: str,
        source: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        config = self._store.read_config()
        transfer_config = config.get("transfer", {}) if isinstance(config.get("transfer"), dict) else {}
        self._remember_config(transfer_config)
        if not transfer_config.get("enabled"):
            raise RuntimeError("115 搬运未启用，请先在后台开启")

        if not str(path_115 or "").strip():
            raise RuntimeError("请输入 115 本地盘目录路径或 CID")
        normalized_path = _normalize_local_115_ref(path_115)
        pause_notice = self._current_pause_notice_for_config(transfer_config)
        if pause_notice:
            raise RuntimeError(pause_notice)

        now = _utc_now_iso()
        task = {
            "id": str(uuid4()),
            "source": source,
            "sourceText": normalized_path,
            "chatId": chat_id,
            "userId": user_id,
            "messageId": message_id,
            "shareUrl": _local_115_task_url(normalized_path),
            "shareCode": _local_115_task_code(normalized_path),
            "receiveCode": "",
            "title": _local_115_task_title(normalized_path),
            "status": "queued",
            "totalFiles": 0,
            "doneFiles": 0,
            "files": [],
            "logs": [{"time": now, "level": "info", "message": "115 本地盘任务已入队"}],
            "createdAt": now,
            "updatedAt": now,
        }
        duplicate = await self._find_active_duplicate(task)
        if duplicate:
            raise RuntimeError(
                f"该 115 本地盘目录已有未完成任务（{_transfer_status_label(duplicate.get('status'))}），请勿重复提交"
            )
        self._store.save_transfer_task(task)
        await self._notify_queued_tasks([task])
        self.kick()
        return task

    async def _find_active_duplicate(self, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """查找相同 shareCode 的活跃（未完成的）任务，防止重复提交。"""
        existing_tasks = self._store.list_transfer_tasks(100)
        for existing in existing_tasks:
            if (
                str(existing.get("kind") or "pan115_share") == str(task.get("kind") or "pan115_share")
                and
                existing.get("shareCode", "").lower() == task.get("shareCode", "").lower()
                and not _is_final_transfer_status(existing.get("status"))
            ):
                return existing
        return None

    def kick(self) -> None:
        if self._running:
            return
        self._running = True
        asyncio.create_task(self._process_loop_wrapper())

    async def _process_loop_wrapper(self) -> None:
        try:
            await self._process_loop()
        finally:
            self._running = False

    def current_pause_notice(self) -> Optional[str]:
        return self._current_pause_notice_for_config(self._cached_transfer_config)

    def _current_pause_notice_for_config(self, config: Optional[Dict[str, Any]]) -> Optional[str]:
        settings = _transfer_runtime_settings(config)
        if not settings.get("pauseEnabled") or not _is_within_transfer_pause(datetime.now(timezone.utc), settings):
            return None
        return (
            f"当前 {_format_transfer_pause_window(settings)} 是 123 离线任务不稳定时段，"
            f"已暂停 115 搬运，请 {_format_hour(settings.get('pauseEndHour'))} 后再提交或继续处理。"
        )

    def _remember_config(self, config: Optional[Dict[str, Any]]) -> None:
        self._cached_transfer_config = config

    def _local_source_pan115_account(self) -> Dict[str, str]:
        submission = self._store.read_submission_config()
        helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
        try:
            account = select_115_account(helper)
        except Exception as error:
            raise RuntimeError("115 本地盘搬运需要使用 115 助手 Cookie，请先在后台“115 助手”配置 Cookie") from error
        return {
            "name": f"115 助手：{account.get('name') or '默认账号'}",
            "cookie": str(account.get("cookie") or ""),
        }

    async def create_status_pan123_client(self) -> Pan123OpenAPIClient:
        return await self._create_pan123_client()

    # -----------------------------------------------------------------------
    # Internal process loop
    # -----------------------------------------------------------------------
    async def _process_loop(self) -> None:
        transfer_config = await self._get_transfer_config()
        max_active_tasks = self._max_offline_slots(transfer_config)
        while len(self._active_task_ids) < max_active_tasks:
            paused = bool(self._current_pause_notice_for_config(transfer_config))
            pan123_copy_busy = bool(self._active_pan123_share_copy_task_id)
            if paused:
                if pan123_copy_busy:
                    return
                task = self._store.next_queued_transfer_task("pan123_share_copy")
            else:
                task = self._store.next_queued_transfer_task(
                    exclude_kind="pan123_share_copy" if pan123_copy_busy else ""
                )
            if not task:
                return
            if task["id"] in self._active_task_ids:
                return
            self._active_task_ids.add(task["id"])
            if str(task.get("kind") or "") == "pan123_share_copy":
                self._active_pan123_share_copy_task_id = task["id"]
            task["status"] = "running"
            task["error"] = None
            task["startedAt"] = task.get("startedAt") or _utc_now_iso()
            _add_unique_task_log(task, "info", "任务已进入并发搬运调度")
            if not await self._save_transfer_task(task):
                self._active_task_ids.discard(task["id"])
                if self._active_pan123_share_copy_task_id == task["id"]:
                    self._active_pan123_share_copy_task_id = None
                continue
            await self._notify_task_progress(task, force=True)
            asyncio.create_task(self._run_task_safely_wrapper(task))

    async def _run_task_safely_wrapper(self, task: Dict[str, Any]) -> None:
        try:
            await self._run_task(task)
        except Exception as error:
            task["status"] = "failed"
            task["error"] = str(error)
            task["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "error", task["error"])
            if not await self._save_transfer_task(task):
                return
            await self._notify_task(task)
            await self._discard_finished_task_record(task)
            logger.error("115 搬运任务失败", extra={"task_id": task["id"], "error": task["error"]})
        finally:
            self._active_task_ids.discard(task["id"])
            if self._active_pan123_share_copy_task_id == task["id"]:
                self._active_pan123_share_copy_task_id = None
            self.kick()

    async def _run_task(self, task: Dict[str, Any]) -> None:
        config = self._store.read_config()
        transfer_config = config.get("transfer", {})
        self._remember_config(transfer_config)
        task["status"] = "running"
        task["startedAt"] = task.get("startedAt") or _utc_now_iso()
        if str(task.get("kind") or "") == "pan123_share_copy":
            await self._run_pan123_share_copy(task, transfer_config)
            return
        is_local_task = _is_local_115_task(task)
        _add_task_log(task, "info", "开始扫描 115 本地盘" if is_local_task else "开始解析 115 分享")
        if not await self._save_transfer_task(task):
            return

        pan115_accounts = _transfer_cookie_pool(transfer_config)
        pan115_cursor = 0
        selected_pan115_account: Optional[Dict[str, str]] = None

        def next_pan115_account() -> Dict[str, str]:
            nonlocal pan115_cursor
            if selected_pan115_account:
                return selected_pan115_account
            idx = pan115_cursor
            pan115_cursor += 1
            return pan115_accounts[idx % max(1, len(pan115_accounts))] if pan115_accounts else {
                "name": "默认 Cookie",
                "cookie": transfer_config.get("pan115Cookie", ""),
            }

        pan123 = await self._create_pan123_client()
        _add_task_log(task, "info", "123 秒传、目录和离线下载使用 OpenAPI")
        if is_local_task:
            local_path = _local_115_path_from_task(task)
            source_account = self._local_source_pan115_account()
            inspected = await self._inspect_pan115_local_path(task, local_path, source_account, transfer_config)
            pan115_cursor = inspected["accountIndex"]
            selected_pan115_account = inspected.get("account")
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
            inspected = await self._inspect_pan115_share(task, link, pan115_accounts, transfer_config.get("pan115Cookie", ""))
            pan115_cursor = inspected["accountIndex"]
            inspection = inspected["inspection"]
            task["title"] = inspection.get("title") or task.get("title")
            task["receiveCode"] = inspection.get("receive_code") or task.get("receiveCode")
        task["files"] = _merge_files(task.get("files", []), inspection.get("files", []))
        task["totalFiles"] = len(task["files"])
        task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
        _add_task_log(task, "info", f"已展开 {task['totalFiles']} 个文件")
        if len(pan115_accounts) > 1 and not is_local_task:
            _add_task_log(
                task, "info",
                f"115 Cookie 池已启用：{'、'.join(item['name'] for item in pan115_accounts)} 轮询取直链"
            )
        if selected_pan115_account:
            _add_task_log(task, "info", f"115 本地盘使用账号：{selected_pan115_account['name']}")
        if not await self._save_transfer_task(task):
            return
        await self._notify_task_progress(task, force=True)

        if not task["files"]:
            task["status"] = "failed"
            task["error"] = "115 本地盘目录中没有可搬运文件" if is_local_task else "115 分享中没有可搬运文件"
            task["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "error", task["error"])
            if not await self._save_transfer_task(task):
                return
            await self._notify_task(task)
            await self._discard_finished_task_record(task)
            return

        concurrency = max(
            1,
            min(
                self._max_offline_slots(transfer_config),
                max(1, len([f for f in task["files"] if not _is_done_file(f)]) or 1),
            ),
        )
        offline_limiter = self._with_offline_slot
        next_idx = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal next_idx
            while True:
                async with lock:
                    index = next_idx
                    next_idx += 1
                if index >= len(task["files"]):
                    break
                file = task["files"][index]
                if not file or _is_done_file(file):
                    continue
                await self._process_file(
                    task, file, next_pan115_account(), pan123,
                    transfer_config.get("targetDirId") or "0", offline_limiter
                )
                task["doneFiles"] = len([f for f in task["files"] if _is_done_file(f)])
                if not await self._save_transfer_task(task):
                    return
                await self._notify_task_progress(task)

        await asyncio.gather(*[worker() for _ in range(concurrency)])

        # 等待所有 115 源文件删除任务完成，然后清理空文件夹
        pending_deletions = task.get("_pendingPan115Deletions", [])
        if pending_deletions:
            await asyncio.gather(*pending_deletions, return_exceptions=True)
        if _is_local_115_task(task) and selected_pan115_account:
            await self._cleanup_empty_local_folders(task, selected_pan115_account)

        failed = len([f for f in task["files"] if f.get("status") == "failed"])
        if failed:
            task["status"] = "failed" if failed == len(task["files"]) else "partial"
        else:
            task["status"] = "success"
        task["finishedAt"] = _utc_now_iso()
        _add_task_log(
            task, "warn" if failed else "info",
            f"任务完成，但 {failed} 个文件失败" if failed else "任务全部完成"
        )
        if not await self._save_transfer_task(task):
            return
        await self._notify_task(task)
        await self._discard_finished_task_record(task)
        logger.log(
            logging.WARNING if failed else logging.INFO,
            "115 搬运任务结束",
            extra={"task_id": task["id"], "status": task["status"], "total": task["totalFiles"], "failed": failed}
        )

    async def _run_pan123_share_copy(self, task: Dict[str, Any], transfer_config: Dict[str, Any]) -> None:
        session = self._store.read_session()
        if not session or not session.get("token"):
            raise RuntimeError("后端未登录 123 云盘，无法转存")
        _add_task_log(task, "info", "开始处理 123 分享转存")
        if not await self._save_transfer_task(task):
            return

        remote_task_id = int(task.get("remoteTaskId") or 0)
        if remote_task_id <= 0:
            items = await self._pan123_web_client.list_share_root_items(
                str(task.get("shareUrl") or ""),
                str(task.get("receiveCode") or ""),
            )
            task["files"] = [
                {
                    "id": str(item.get("FileId") or item.get("fileId") or item.get("id") or ""),
                    "name": str(item.get("FileName") or item.get("filename") or item.get("name") or ""),
                    "size": int(item.get("Size") or item.get("BaseSize") or item.get("size") or 0),
                    "status": "pending",
                }
                for item in items
                if str(item.get("FileId") or item.get("fileId") or item.get("id") or "").strip()
            ]
            task["totalFiles"] = len(task["files"])
            if not task["files"]:
                raise RuntimeError("123 分享根目录中没有可转存的文件")
            remote_task_id = await self._submit_pan123_share_copy_with_retry(
                task,
                session,
                items,
                transfer_config,
            )
            task["remoteTaskId"] = remote_task_id
            _add_task_log(task, "info", f"123 转存已提交，远端任务 ID：{remote_task_id}")
            if not await self._save_transfer_task(task):
                return
        else:
            _add_task_log(task, "info", f"继续查询已有 123 转存任务：{remote_task_id}")

        max_polls = max(1, int(transfer_config.get("offlineMaxPolls") or DEFAULT_MAX_POLLS))
        poll_ms = max(2000, int(transfer_config.get("offlinePollMs") or DEFAULT_POLL_MS))
        for attempt in range(max_polls):
            try:
                status = await self._pan123_web_client.get_share_copy_task(
                    session,
                    str(task.get("shareUrl") or ""),
                    remote_task_id,
                )
            except Exception as error:
                if not _is_transient_pan123_share_copy_error(error) or attempt + 1 >= max_polls:
                    raise
                wait_ms = max(poll_ms, _pan123_share_copy_retry_base_ms(transfer_config))
                _add_unique_task_log(task, "warn", f"123 转存状态查询频繁，{wait_ms // 1000} 秒后重试")
                if not await self._save_transfer_task(task):
                    return
                await _delay(wait_ms)
                continue
            error_code = int(status.get("errorCode") or 0)
            remote_status = int(status.get("status") or 0)
            reason = str(status.get("reason") or "").strip()
            task["remoteStatus"] = remote_status
            task["remoteProgress"] = str(status.get("progress") or "")
            if remote_status == 2:
                for file in task.get("files") or []:
                    if isinstance(file, dict):
                        file["status"] = "success"
                        file["method"] = "123_copy"
                        file["finishedAt"] = _utc_now_iso()
                task["doneFiles"] = task.get("totalFiles") or len(task.get("files") or [])
                task["status"] = "success"
                task["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "info", "123 分享转存完成")
                if not await self._save_transfer_task(task):
                    return
                await self._notify_task(task)
                return
            status_error: Optional[RuntimeError] = None
            if error_code:
                status_error = RuntimeError(reason or f"123 转存失败（错误码 {error_code}）")
            elif reason:
                status_error = RuntimeError(reason)
            elif remote_status not in {0, 1}:
                status_error = RuntimeError(f"123 转存失败（状态 {remote_status}）")
            if status_error:
                if _is_transient_pan123_share_copy_error(status_error) and attempt + 1 < max_polls:
                    wait_ms = max(poll_ms, _pan123_share_copy_retry_base_ms(transfer_config))
                    _add_unique_task_log(task, "warn", f"123 转存状态返回操作频繁，{wait_ms // 1000} 秒后重试")
                    if not await self._save_transfer_task(task):
                        return
                    await _delay(wait_ms)
                    continue
                raise status_error
            if attempt + 1 < max_polls:
                await _delay(poll_ms)
        raise RuntimeError(f"123 转存状态查询超时（远端任务 {remote_task_id}）")

    async def _submit_pan123_share_copy_with_retry(
        self,
        task: Dict[str, Any],
        session: Dict[str, Any],
        items: List[Dict[str, Any]],
        transfer_config: Dict[str, Any],
    ) -> int:
        min_interval_ms = _pan123_share_copy_min_interval_ms(transfer_config)
        max_attempts = _pan123_share_copy_max_attempts(transfer_config)
        retry_base_ms = _pan123_share_copy_retry_base_ms(transfer_config)
        for attempt in range(1, max_attempts + 1):
            loop = asyncio.get_running_loop()
            elapsed_ms = max(0, int((loop.time() - self._last_pan123_share_copy_submit_at) * 1000))
            wait_ms = max(0, min_interval_ms - elapsed_ms) if self._last_pan123_share_copy_submit_at else 0
            if wait_ms:
                _add_unique_task_log(task, "info", f"为避免 123 限流，等待 {max(1, math.ceil(wait_ms / 1000))} 秒后提交")
                await _delay(wait_ms)
            self._last_pan123_share_copy_submit_at = loop.time()
            try:
                return await self._pan123_web_client.create_share_copy_task(
                    session,
                    str(task.get("shareUrl") or ""),
                    str(task.get("receiveCode") or ""),
                    str(task.get("targetDirId") or "0"),
                    items,
                )
            except Exception as error:
                if not _is_transient_pan123_share_copy_error(error) or attempt >= max_attempts:
                    raise
                retry_ms = max(min_interval_ms, retry_base_ms * attempt)
                _add_task_log(
                    task,
                    "warn",
                    f"123 返回操作频繁，第 {attempt}/{max_attempts} 次提交失败，{max(1, math.ceil(retry_ms / 1000))} 秒后重试",
                )
                if not await self._save_transfer_task(task):
                    raise RuntimeError("123 转存任务已取消")
                await _delay(retry_ms)
        raise RuntimeError("123 转存提交重试失败")

    async def _inspect_pan115_share(
        self,
        task: Dict[str, Any],
        link: Dict[str, Any],
        accounts: List[Dict[str, str]],
        fallback_cookie: str,
    ) -> Dict[str, Any]:
        candidates = accounts if accounts else [{"name": "默认 Cookie", "cookie": fallback_cookie}]
        errors: List[str] = []

        for index, account in enumerate(candidates):
            if index > 0:
                _add_task_log(task, "warn", f"改用 115 Cookie 解析分享：{account['name']}")
            try:
                client = Pan115TransferClient(account["cookie"])
                inspection = await client.inspect_and_flatten(link)
                if index > 0:
                    _add_task_log(task, "info", f"115 分享解析成功：{account['name']}")
                return {"accountIndex": index, "inspection": inspection}
            except Exception as error:
                message = str(error)
                errors.append(f"{account['name']}：{message}")
                if len(candidates) > 1:
                    _add_task_log(task, "warn", f"115 分享解析失败：{account['name']}（{message}）")

        raise RuntimeError("；".join(errors) or "115 分享解析失败")

    async def _inspect_pan115_local_path(
        self,
        task: Dict[str, Any],
        path_115: str,
        source_account: Dict[str, str],
        transfer_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        account = {
            "name": str(source_account.get("name") or "115 助手"),
            "cookie": str(source_account.get("cookie") or ""),
        }
        _add_task_log(task, "info", f"115 本地盘资源使用账号：{account['name']}")
        try:
            client = Pan115TransferClient(account["cookie"])
            inspection = await client.inspect_local_path(
                path_115,
                _transfer_exclude_suffixes(transfer_config),
                _transfer_exclude_cids(transfer_config),
            )
            return {"accountIndex": 0, "account": account, "inspection": inspection}
        except Exception as error:
            raise RuntimeError(f"{account['name']}：{error}") from error

    async def _create_pan123_client(self) -> Pan123OpenAPIClient:
        config = self._store.read_config()
        transfer_config = config.get("transfer") if isinstance(config.get("transfer"), dict) else {}
        client_id = str(config.get("pan123OpenApiClientId") or transfer_config.get("pan123ClientId") or "").strip()
        client_secret = str(config.get("pan123OpenApiClientSecret") or transfer_config.get("pan123ClientSecret") or "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("请先配置 123 OpenAPI ClientID 和 ClientSecret")
        token_key = pan123_open_token_key(client_id, client_secret)
        if self._pan123_open_client is not None and self._pan123_open_client_key == token_key:
            return self._pan123_open_client
        if self._pan123_open_client_lock is None:
            self._pan123_open_client_lock = asyncio.Lock()
        async with self._pan123_open_client_lock:
            if self._pan123_open_client is not None and self._pan123_open_client_key == token_key:
                return self._pan123_open_client
            previous_client = self._pan123_open_client
            self._pan123_open_client = None
            self._pan123_open_client_key = ""
            if previous_client is not None:
                await previous_client.close()
            token_store = _Pan123OpenTokenStoreAdapter(self._store, token_key)
            self._pan123_open_client = Pan123OpenAPIClient(
                client_id,
                client_secret,
                token_store=token_store,
            )
            self._pan123_open_client_key = token_key
            return self._pan123_open_client

    async def _get_transfer_config(self) -> Dict[str, Any]:
        config = self._store.read_config().get("transfer", {})
        self._remember_config(config)
        return config

    def _max_offline_slots(self, config: Dict[str, Any]) -> int:
        return max(1, min(max(1, FILE_CONCURRENCY_MAX), int(config.get("concurrency") or 1)))

    async def _with_offline_slot(self, job: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        await self._acquire_offline_slot()
        try:
            return await job()
        finally:
            self._release_offline_slot()

    async def _acquire_offline_slot(self) -> None:
        while self._active_offline_files >= self._max_offline_slots(await self._get_transfer_config()):
            future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
            self._offline_slot_waiters.append(future)
            try:
                await future
            except asyncio.CancelledError:
                if future in self._offline_slot_waiters:
                    self._offline_slot_waiters.remove(future)
                raise
        self._active_offline_files += 1

    def _release_offline_slot(self) -> None:
        self._active_offline_files = max(0, self._active_offline_files - 1)
        if self._offline_slot_waiters:
            waiter = self._offline_slot_waiters.pop(0)
            if not waiter.done():
                waiter.set_result(None)
        self.kick()

    # -----------------------------------------------------------------------
    # process_file
    # -----------------------------------------------------------------------
    async def _process_file(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan115_account: Dict[str, str],
        pan123: Pan123OpenAPIClient,
        target_root_id: str,
        offline_limiter: AsyncLimiter,
    ) -> None:
        file["status"] = "running"
        file["startedAt"] = _utc_now_iso()
        file["error"] = None
        if not await self._save_transfer_task(task):
            return

        try:
            target_path = _build_pan123_path(file.get("path", []))
            file["targetPath"] = target_path
            if "/".join(target_path) != "/".join(file.get("path", [])):
                _add_unique_task_log(
                    task, "info",
                    f"已将 123 不支持的目录名映射为安全目录：{'/'.join(file.get('path', []) or ['/'])} -> {'/'.join(target_path or ['/'])}"
                )
            target_dir_id = await pan123.ensure_path(target_root_id, target_path)
            file["targetDirId"] = target_dir_id

            async def refresh_target_dir(stage: str) -> None:
                nonlocal target_dir_id
                previous_dir_id = target_dir_id
                target_dir_id = await pan123.ensure_path(target_root_id, target_path)
                file["targetDirId"] = target_dir_id
                _add_task_log(task, "warn", f"{stage}发现目标目录失效，已重新解析：{previous_dir_id} -> {target_dir_id}")

            existing = await pan123.find_same_file(target_dir_id, file["name"], file.get("size", 0))
            if existing:
                await self._remember_transfer_hash(file, existing)
                file["status"] = "skipped"
                file["method"] = "exists"
                file["pan123FileId"] = existing.get("fileId")
                file["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "info", f"已存在，跳过：{_display_path(file)}")
                deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                if deletion_task:
                    task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                return

            organized_existing = None
            if SCAN_EXISTING_ORGANIZED:
                organized_existing = await self._find_existing_organized_file(task, file, pan123, target_root_id)
            if organized_existing:
                await self._remember_transfer_hash(file, organized_existing["file"])
                file["status"] = "skipped"
                file["method"] = "exists"
                file["pan123FileId"] = organized_existing["file"].get("fileId")
                file["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "info", f"已在其他目录找到同大小文件，标记完成：{organized_existing['path']}")
                deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                if deletion_task:
                    task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                return

            had_offline_attempt = bool(file.get("offlineTaskId") or file.get("sourceUrl") or file.get("method") == "offline")
            if had_offline_attempt:
                recovered = await self._recover_offline_file(task, file, pan123, target_root_id, target_dir_id)
                if recovered:
                    await self._remember_transfer_hash(file, recovered)
                    file["status"] = "success"
                    file["method"] = "offline"
                    file["pan123FileId"] = recovered.get("fileId")
                    file["finishedAt"] = _utc_now_iso()
                    deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                    if deletion_task:
                        task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                    return

                existing_offline_task = await self._find_offline_task(
                    pan123, file.get("offlineTaskId"), _offline_candidate_names(file)
                )
                if not existing_offline_task:
                    task_id_text = f" #{file.get('offlineTaskId')}" if file.get("offlineTaskId") else ""
                    raise RuntimeError(
                        f"原 123 离线任务{task_id_text} 未找到，已停止自动重复添加；请确认离线列表后手动重试"
                    )

                file["offlineTaskId"] = existing_offline_task["id"]
                _add_unique_task_log(task, "info", f"继续等待已有 123 离线任务 #{existing_offline_task['id']}")
                resumed = await self._wait_for_offline_file(
                    task, file, pan123, target_root_id, target_dir_id, {}
                )
                if not resumed:
                    raise RuntimeError("等待已有 123 离线文件落盘超时")
                await self._remember_transfer_hash(file, resumed)
                file["status"] = "success"
                file["method"] = "offline"
                file["offlineStatus"] = "success"
                file["offlineStatusText"] = "成功"
                file["offlineProgress"] = 100
                file["pan123FileId"] = resumed.get("fileId")
                file["finishedAt"] = _utc_now_iso()
                _add_task_log(task, "info", f"已有离线任务完成并校验大小一致：{_display_path(file)}")
                deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                if deletion_task:
                    task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                return

            if file.get("sha1"):
                for attempt in range(2):
                    try:
                        reused_file_id = await pan123.sha1_reuse(target_dir_id, file["name"], file["sha1"], file.get("size", 0))
                        if reused_file_id:
                            file["status"] = "success"
                            file["method"] = "sha1_reuse"
                            file["pan123FileId"] = reused_file_id
                            file["finishedAt"] = _utc_now_iso()
                            _add_task_log(task, "info", f"123 API SHA1 秒传成功：{_display_path(file)}")
                            deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                            if deletion_task:
                                task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                            return
                        _add_task_log(task, "info", f"123 API SHA1 未命中，改用离线：{_display_path(file)}")
                        break
                    except Exception as error:
                        if attempt == 0 and _is_missing_target_dir_error(error):
                            await refresh_target_dir("123 API SHA1 秒传")
                            continue
                        _add_task_log(
                            task, "warn",
                            f"123 API SHA1 秒传失败，改用离线：{_display_path(file)}（{error}）"
                        )
                        break

            rapid_etag = file.get("md5")
            if not rapid_etag and file.get("sha1"):
                cached_hash = self._store.get_transfer_hash(file["sha1"], file.get("size", 0))
                if cached_hash:
                    rapid_etag = cached_hash.get("etag")
                    file["md5"] = cached_hash.get("etag")
                    _add_task_log(task, "info", f"本地缓存找到 MD5 秒传数据：{_display_path(file)}")

            if rapid_etag:
                for attempt in range(2):
                    try:
                        reused_file_id = await pan123.md5_reuse(target_dir_id, file["name"], rapid_etag, file.get("size", 0))
                        if reused_file_id:
                            await self._remember_known_transfer_hash(file, rapid_etag)
                            file["status"] = "success"
                            file["method"] = "md5_reuse"
                            file["pan123FileId"] = reused_file_id
                            file["finishedAt"] = _utc_now_iso()
                            _add_task_log(task, "info", f"123 API MD5 秒传成功：{_display_path(file)}")
                            deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
                            if deletion_task:
                                task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
                            return
                        _add_task_log(task, "info", f"123 API MD5 未命中，改用离线：{_display_path(file)}")
                        break
                    except Exception as error:
                        if attempt == 0 and _is_missing_target_dir_error(error):
                            await refresh_target_dir("123 API MD5 秒传")
                            continue
                        _add_task_log(
                            task, "warn",
                            f"123 API MD5 秒传失败，改用离线：{_display_path(file)}（{error}）"
                        )
                        break

            async def offline_job() -> Optional[Dict[str, Any]]:
                nonlocal target_dir_id
                before_offline_file_ids = await self._snapshot_candidate_file_ids(
                    pan123, target_root_id, target_dir_id
                )
                await self._notify_cookie_use(task, file, pan115_account["name"])
                pan115 = Pan115TransferClient(pan115_account["cookie"])
                download_url = await self._get_pan115_download_url(task, file, pan115)
                file["sourceUrl"] = download_url
                offline_submit_name = _build_offline_submit_name(file)
                file["offlineSubmitName"] = None if offline_submit_name == file["name"] else offline_submit_name
                file["offlineStatus"] = "submitting"
                file["offlineStatusText"] = "提交中"
                file["offlineProgress"] = 0
                file["method"] = "offline"
                # Persist the submission intent before the remote request. If
                # the process dies after 123 accepts it but before the response
                # arrives, restart recovery must not create the same task again.
                if not await self._save_transfer_task(task):
                    return None
                try:
                    offline_task_id = await pan123.create_offline_download(
                        download_url, target_dir_id, file.get("offlineSubmitName") or file["name"]
                    )
                except Exception as error:
                    if not _is_missing_target_dir_error(error):
                        raise
                    await refresh_target_dir("123 API 离线创建")
                    before_offline_file_ids = await self._snapshot_candidate_file_ids(
                        pan123, target_root_id, target_dir_id
                    )
                    offline_task_id = await pan123.create_offline_download(
                        download_url, target_dir_id, file.get("offlineSubmitName") or file["name"]
                    )
                file["offlineTaskId"] = offline_task_id
                file["offlineStatus"] = "running"
                file["offlineStatusText"] = "离线中"
                file["offlineProgress"] = 0
                file["method"] = "offline"
                display = _display_path(file)
                if not file.get("offlineSubmitName"):
                    _add_task_log(task, "info", f"已创建离线任务：{display} #{offline_task_id}")
                else:
                    _add_task_log(
                        task, "info",
                        f"已用临时短名创建离线任务：{display} -> {file['offlineSubmitName']} #{offline_task_id}"
                    )
                # The remote ID is the idempotency anchor used after a restart;
                # save it before the first potentially slow status request.
                if not await self._save_transfer_task(task):
                    return None
                return await self._wait_for_offline_file(
                    task, file, pan123, target_root_id, target_dir_id, before_offline_file_ids
                )

            created = await offline_limiter(offline_job)
            if not created:
                raise RuntimeError("等待 123 离线文件落盘超时")
            await self._remember_transfer_hash(file, created)
            file["status"] = "success"
            file["offlineStatus"] = "success"
            file["offlineStatusText"] = "成功"
            file["offlineProgress"] = 100
            file["pan123FileId"] = created.get("fileId")
            file["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "info", f"离线完成并校验大小一致：{_display_path(file)}")
            deletion_task = await self._delete_115_source_after_success_if_needed(task, file, pan115_account)
            if deletion_task:
                task.setdefault("_pendingPan115Deletions", []).append(deletion_task)
        except Exception as error:
            file["status"] = "failed"
            if file.get("method") == "offline":
                file["offlineStatus"] = "failed"
                file["offlineStatusText"] = "失败"
            file["error"] = str(error)
            file["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "error", f"{_display_path(file)}：{file['error']}")

    # -----------------------------------------------------------------------
    # Notification helpers
    # -----------------------------------------------------------------------
    async def _notify_task(self, task: Dict[str, Any]) -> None:
        if task["status"] == "success" and str(task.get("kind") or "") != "pan123_share_copy":
            await self._cleanup_successful_task_messages(task)
            self._progress_notice_state.pop(task["id"], None)
            return
        if not self._notifier:
            self._progress_notice_state.pop(task["id"], None)
            return
        try:
            ref = await self._notifier(task)
            ref_message_id = ref.get("messageId") if isinstance(ref, dict) else None
            if ref:
                self._remember_notice(task, ref)
                task["transferFinalMessageId"] = ref_message_id
            is_pan123_copy = str(task.get("kind") or "") == "pan123_share_copy"
            cleanup_failure_messages = task.get("source") == "telegram" and task["status"] in ("failed", "partial")
            message_ids = _transfer_cleanup_message_ids(task)
            if cleanup_failure_messages:
                message_ids = list({*message_ids, task.get("messageId")})
                message_ids = [m for m in message_ids if isinstance(m, (int, float)) and not math.isnan(m)]
                if is_pan123_copy and ref_message_id:
                    message_ids = [m for m in message_ids if m != ref_message_id]
            else:
                message_ids = [m for m in message_ids if m != ref_message_id]
            cleanup_chat_id = task.get("transferNoticeChatId") or task.get("chatId")
            if cleanup_chat_id and message_ids and self._cleanup_notifier:
                await self._cleanup_notifier({"task": task, "chatId": cleanup_chat_id, "messageIds": message_ids})
                task["transferNoticeMessageIds"] = []
                if cleanup_failure_messages and not is_pan123_copy:
                    task["transferFinalMessageId"] = None
                await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("115 搬运结果通知失败", extra={"task_id": task["id"], "error": str(error)})
        finally:
            self._progress_notice_state.pop(task["id"], None)

    async def _cleanup_successful_task_messages(self, task: Dict[str, Any]) -> None:
        cleanup_chat_id = task.get("transferNoticeChatId") or task.get("chatId")
        message_ids = [
            *_transfer_cleanup_message_ids(task),
            *( [task.get("messageId")] if task.get("source") == "telegram" else [])
        ]
        message_ids = [m for m in message_ids if isinstance(m, (int, float)) and not math.isnan(m)]
        if not cleanup_chat_id or not message_ids or not self._cleanup_notifier:
            return
        try:
            await self._cleanup_notifier({"task": task, "chatId": cleanup_chat_id, "messageIds": message_ids})
            task["transferNoticeMessageIds"] = []
            task["transferFinalMessageId"] = None
            await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("115 搬运成功消息清理失败", extra={"task_id": task["id"], "error": str(error)})

    async def _notify_task_progress(self, task: Dict[str, Any], force: bool = False) -> None:
        if not self._notifier or _is_final_transfer_status(task["status"]):
            return
        transfer_config = await self._get_transfer_config()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        fingerprint = _transfer_notice_fingerprint(task)
        previous = self._progress_notice_state.get(task["id"])
        if not force and previous and previous.get("fingerprint") == fingerprint:
            return
        interval = transfer_config.get("progressNotifyIntervalMs", TRANSFER_PROGRESS_NOTIFY_INTERVAL_MS)
        if not force and previous and interval > 0 and now - previous.get("sentAt", 0) < interval:
            return

        self._progress_notice_state[task["id"]] = {"sentAt": now, "fingerprint": fingerprint}
        try:
            ref = await self._notifier(task)
            if ref:
                self._remember_notice(task, ref)
                await self._save_transfer_task(task)
        except Exception as error:
            if previous:
                self._progress_notice_state[task["id"]] = previous
            else:
                self._progress_notice_state.pop(task["id"], None)
            logger.warning("115 搬运进度通知失败", extra={"task_id": task["id"], "error": str(error)})

    async def _notify_queued_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        if not self._queued_notifier or not tasks:
            return
        try:
            refs = await self._queued_notifier(tasks)
            if not refs:
                return
            for ref in refs:
                task = next((item for item in tasks if item["id"] == ref.get("taskId")), None)
                if not task:
                    continue
                self._remember_notice(task, ref)
                await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("115 搬运入队通知失败", extra={"error": str(error)})

    async def _notify_deleted_task(self, task: Dict[str, Any]) -> None:
        if not self._deleted_notifier:
            self._progress_notice_state.pop(task["id"], None)
            return
        try:
            ref = await self._deleted_notifier(task)
            message_ids = [m for m in _transfer_cleanup_message_ids(task) if m != ref.get("messageId")]
            cleanup_chat_id = task.get("transferNoticeChatId") or task.get("chatId")
            if cleanup_chat_id and message_ids and self._cleanup_notifier:
                await self._cleanup_notifier({"task": task, "chatId": cleanup_chat_id, "messageIds": message_ids})
        except Exception as error:
            logger.warning("115 搬运删除通知失败", extra={"task_id": task["id"], "error": str(error)})
        finally:
            self._progress_notice_state.pop(task["id"], None)

    async def _get_pan115_download_url(self, task: Dict[str, Any], file: Dict[str, Any], pan115: Pan115TransferClient) -> str:
        transfer_config = await self._get_transfer_config()
        max_attempts = max(1, int(transfer_config.get("downloadMaxAttempts") or PAN115_DOWNLOAD_MAX_ATTEMPTS))
        retry_base_ms = max(0, int(transfer_config.get("downloadRetryBaseMs") or PAN115_DOWNLOAD_RETRY_BASE_MS))

        async def _job() -> str:
            last_error = ""
            for attempt in range(1, max_attempts + 1):
                try:
                    if _is_local_115_file(file):
                        download_url = await pan115.get_local_download_url(str(file.get("pickCode") or file.get("pick_code") or ""))
                    else:
                        download_url = await pan115.get_download_url(task["shareCode"], task.get("receiveCode") or "", file["id"])
                    try:
                        await self._validate_pan115_download_url(task, file, download_url)
                    except Exception as validation_error:
                        if not _is_local_115_file(file) or not re.search(r"预检.*(?:HTTP\\s*)?403", str(validation_error), re.I):
                            raise
                        _add_task_log(task, "warn", f"115 直链本机预检返回 403，交由 123 离线验证：{_display_path(file)}")
                    return download_url
                except Exception as error:
                    last_error = str(error)
                    if not _is_transient_115_download_error(last_error) or attempt >= max_attempts:
                        break
                    wait_ms = retry_base_ms * attempt
                    _add_task_log(
                        task, "warn",
                        f"115 取直链受限，{round(wait_ms / 1000)} 秒后重试：{_display_path(file)}（第 {attempt}/{max_attempts} 次，{last_error}）"
                    )
                    await _delay(wait_ms)
            raise RuntimeError(last_error or "115 未返回文件直链")

        return await self._enqueue_pan115_download(_job)

    async def _enqueue_pan115_download(self, job: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        previous = self._pan115_download_queue

        async def _run() -> Any:
            try:
                if previous:
                    await previous
            except Exception:
                pass
            transfer_config = await self._get_transfer_config()
            elapsed = int(datetime.now(timezone.utc).timestamp() * 1000) - self._last_pan115_download_at
            configured_interval = transfer_config.get("downloadMinIntervalMs")
            min_interval_ms = PAN115_DOWNLOAD_MIN_INTERVAL_MS if configured_interval is None else int(configured_interval)
            wait_ms = max(0, max(0, min_interval_ms) - elapsed)
            if wait_ms > 0:
                await _delay(wait_ms)
            try:
                return await job()
            finally:
                self._last_pan115_download_at = int(datetime.now(timezone.utc).timestamp() * 1000)

        future = asyncio.ensure_future(_run())

        async def _swallow() -> None:
            try:
                await future
            except Exception:
                pass

        self._pan115_download_queue = asyncio.ensure_future(_swallow())
        return await future

    async def _delete_115_source_after_success_if_needed(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan115_account: Dict[str, str],
    ) -> Optional[asyncio.Task[None]]:
        if not _is_local_115_file(file):
            return None
        transfer_config = await self._get_transfer_config()
        if not bool(transfer_config.get("delete115AfterSuccess")):
            return None
        if file.get("pan115Deleted"):
            return None
        file_id = str(file.get("localFileId") or file.get("id") or "").strip()
        if not file_id:
            return None

        if file.get("pan115DeletePending"):
            return None
        file["pan115DeletePending"] = True
        parent_dir_id = str(file.get("parentDirId") or "").strip()

        async def _delete_later() -> None:
            async def _job() -> None:
                client = Pan115TransferClient(pan115_account["cookie"])
                await client.delete_local_files([file_id])

            try:
                await self._enqueue_pan115_download(_job)
                file["pan115Deleted"] = True
                file["pan115DeleteError"] = None
                _add_task_log(task, "info", f"已删除 115 源文件：{_display_path(file)}")
                if parent_dir_id:
                    task.setdefault("foldersToCleanup", set()).add(parent_dir_id)
            except Exception as error:
                file["pan115DeleteError"] = str(error)
                _add_task_log(task, "warn", f"115 源文件删除失败，已保留：{_display_path(file)}（{error}）")
            finally:
                file["pan115DeletePending"] = False
                await self._save_transfer_task(task)

        return asyncio.create_task(_delete_later())

    async def _cleanup_empty_local_folders(
        self,
        task: Dict[str, Any],
        pan115_account: Dict[str, str],
    ) -> None:
        transfer_config = await self._get_transfer_config()
        if not bool(transfer_config.get("delete115AfterSuccess")):
            return
        folders_to_cleanup = task.get("foldersToCleanup")
        if not folders_to_cleanup:
            return
        dir_map = task.get("localDirMap") or {}
        root_cid = str(task.get("rootCid") or "").strip()
        if not root_cid:
            return

        # 按路径深度降序排序，从最深层的文件夹开始处理
        cids_to_check = sorted(
            {str(cid).strip() for cid in folders_to_cleanup if str(cid).strip()},
            key=lambda cid: len(dir_map.get(cid, {}).get("path", [])),
            reverse=True,
        )
        if not cids_to_check:
            return

        client = Pan115TransferClient(pan115_account["cookie"])
        deleted_cids: Set[str] = set()

        for cid in cids_to_check:
            if cid == root_cid or cid in deleted_cids:
                continue
            try:
                page = await client.list_local_dir(cid, 1000, 0)
                items = page.get("data", []) if isinstance(page.get("data"), list) else []
                if not items:
                    # 文件夹为空，可以删除
                    await client.delete_local_files([cid])
                    deleted_cids.add(cid)
                    dir_info = dir_map.get(cid, {})
                    folder_name = "/".join(dir_info.get("path", []) + [dir_info.get("name", "")])
                    _add_task_log(task, "info", f"已删除 115 空文件夹：{folder_name}")
                    # 将父文件夹加入检查队列
                    parent_cid = str(dir_info.get("parentCid") or "").strip()
                    if parent_cid and parent_cid != root_cid and parent_cid not in deleted_cids:
                        # 重新检查父文件夹是否为空
                        parent_page = await client.list_local_dir(parent_cid, 1000, 0)
                        parent_items = parent_page.get("data", []) if isinstance(parent_page.get("data"), list) else []
                        if not parent_items:
                            await client.delete_local_files([parent_cid])
                            deleted_cids.add(parent_cid)
                            parent_info = dir_map.get(parent_cid, {})
                            parent_name = "/".join(parent_info.get("path", []) + [parent_info.get("name", "")])
                            _add_task_log(task, "info", f"已删除 115 空文件夹：{parent_name}")
            except Exception as error:
                logger.warning("115 本地盘空文件夹清理失败", extra={"cid": cid, "error": str(error)})

    async def _notify_cookie_use(self, task: Dict[str, Any], file: Dict[str, Any], cookie_name: str) -> None:
        _add_task_log(task, "info", f"使用 115 Cookie 取直链：{cookie_name} - {_display_path(file)}")
        if not self._cookie_notifier:
            return
        try:
            ref = await self._cookie_notifier({"task": task, "file": file, "cookieName": cookie_name})
            if ref:
                self._remember_notice(task, ref)
                await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("115 Cookie 使用通知失败", extra={"task_id": task["id"], "cookie_name": cookie_name, "error": str(error)})

    def _remember_notice(self, task: Dict[str, Any], ref: TransferNoticeRef) -> None:
        chat_id = ref.get("chatId")
        message_id = ref.get("messageId")
        if not isinstance(chat_id, (int, float)) or math.isnan(chat_id) or not isinstance(message_id, (int, float)) or math.isnan(message_id):
            return
        task["transferNoticeChatId"] = int(chat_id)
        existing = set(task.get("transferNoticeMessageIds", []) or [])
        existing.add(int(message_id))
        task["transferNoticeMessageIds"] = list(existing)

    async def _recover_offline_file(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan123: Pan123OpenAPIClient,
        target_root_id: str,
        target_dir_id: str,
        exclude_by_dir: Optional[Dict[str, Set[int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        exclude_by_dir = exclude_by_dir or {}
        locations = [
            {"dirId": dir_id, "shouldMove": dir_id != target_dir_id}
            for dir_id in _unique_dir_ids([target_dir_id, target_root_id or "0"])
        ]

        for location in locations:
            found = await pan123.find_file_by_size(
                location["dirId"], file.get("size", 0),
                exclude_name=file["name"] if location["dirId"] == target_dir_id else None,
                exclude_file_ids=exclude_by_dir.get(location["dirId"]),
            )
            if not found:
                continue
            if location["shouldMove"]:
                await pan123.move_files([found["fileId"]], target_dir_id)
            if found.get("filename") != file["name"]:
                await pan123.rename_file(found["fileId"], file["name"])
            action = "发现已离线同大小文件并移动改名" if location["shouldMove"] else "发现已离线同大小文件并重命名"
            _add_task_log(
                task, "info",
                f"{action}：{found.get('filename')} -> {_display_path(file)}"
            )
            return {**found, "filename": file["name"]}
        return None

    async def _wait_for_offline_file(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan123: Pan123OpenAPIClient,
        target_root_id: str,
        target_dir_id: str,
        before_offline_file_ids: Dict[str, Set[int]],
    ) -> Optional[Dict[str, Any]]:
        transfer_config = await self._get_transfer_config()
        max_polls = max(1, int(transfer_config.get("offlineMaxPolls") or DEFAULT_MAX_POLLS))
        poll_ms = max(2000, int(transfer_config.get("offlinePollMs") or DEFAULT_POLL_MS))
        for i in range(max_polls):
            candidate_names = _offline_candidate_names(file)
            offline = await self._find_offline_task(pan123, file.get("offlineTaskId"), candidate_names)
            if offline:
                file["offlineStatus"] = offline["status"]
                file["offlineStatusText"] = offline["statusText"]
                file["offlineProgress"] = offline["progress"]
                file["offlineSpeed"] = offline.get("speed")
                file["offlineSpeedText"] = offline.get("speedText")
                if not await self._save_transfer_task(task):
                    return None
                await self._notify_task_progress(task)
                if offline["status"] == "failed":
                    raise RuntimeError(offline.get("message") or "123 离线任务失败")

            created = None
            for candidate_name in candidate_names:
                created = await pan123.find_same_file(target_dir_id, candidate_name, file.get("size", 0))
                if created:
                    break
            if not created:
                suspicious = await self._find_suspicious_offline_artifact(task, pan123, target_dir_id, file, before_offline_file_ids)
                if suspicious:
                    raise RuntimeError(
                        f"123 离线落盘了疑似错误页文件，已移入回收站：{suspicious['filename']}（{_format_bytes(suspicious.get('size', 0))}）"
                    )
                created = await self._recover_offline_file(task, file, pan123, target_root_id, target_dir_id, before_offline_file_ids)
            if created:
                return await self._rename_offline_result_if_needed(task, file, pan123, created)
            await _delay(poll_ms)
        return None

    async def _find_offline_task(
        self,
        pan123: Pan123OpenAPIClient,
        task_id: Optional[int],
        filenames: List[str],
    ) -> Optional[Dict[str, Any]]:
        if task_id and getattr(pan123, "clientKind", "") == "openapi":
            try:
                process = await pan123.get_offline_process(task_id)
                status = "failed" if process["status"] < 0 else (
                    "success" if process["status"] >= 2 or process["process"] >= 100 else "running"
                )
                return {
                    "id": task_id,
                    "name": filenames[0] or f"离线任务 #{task_id}",
                    "status": status,
                    "statusText": "失败" if status == "failed" else ("成功" if status == "success" else "离线中"),
                    "progress": process["process"],
                }
            except Exception as error:
                logger.warning(
                    "123 API 离线任务状态查询失败",
                    extra={"task_id": task_id, "error": str(error)},
                )
        try:
            tasks = await pan123.list_offline_tasks()
            for item in tasks:
                if item.get("id") == task_id:
                    return item
            for item in tasks:
                if item.get("name") in filenames:
                    return item
            return None
        except Exception as error:
            logger.warning("123 离线任务列表查询失败", extra={"error": str(error)})
            return None

    async def _rename_offline_result_if_needed(
        self, task: Dict[str, Any], file: Dict[str, Any], pan123: Pan123OpenAPIClient, found: Dict[str, Any]
    ) -> Dict[str, Any]:
        if found.get("filename") != file["name"]:
            await pan123.rename_file(found["fileId"], file["name"])
            _add_task_log(task, "info", f"离线文件已重命名回原名：{found['filename']} -> {_display_path(file)}")
            return {**found, "filename": file["name"]}
        return found

    async def _remember_transfer_hash(self, file: Dict[str, Any], pan123_file: Dict[str, Any]) -> None:
        if not file.get("sha1") or not pan123_file.get("etag"):
            return
        saved = self._store.save_transfer_hash(file["sha1"], file.get("size", 0), pan123_file["etag"], file["name"])
        if saved:
            file["md5"] = str(pan123_file["etag"]).lower()

    async def _remember_known_transfer_hash(self, file: Dict[str, Any], etag: str) -> None:
        if not file.get("sha1"):
            return
        saved = self._store.save_transfer_hash(file["sha1"], file.get("size", 0), etag, file["name"])
        if saved:
            file["md5"] = str(etag).lower()

    async def _save_transfer_task(self, task: Dict[str, Any]) -> bool:
        if task["id"] in self._deleted_task_ids:
            return False
        self._store.save_transfer_task(task)
        return True

    async def _discard_finished_task_record(self, task: Dict[str, Any]) -> None:
        if not _is_final_transfer_status(task["status"]):
            return
        if str(task.get("kind") or "") == "pan123_share_copy":
            return
        self._deleted_task_ids.add(task["id"])
        self._progress_notice_state.pop(task["id"], None)
        try:
            self._store.delete_transfer_task(task["id"])
            logger.info(
                "115 搬运终态记录已清理",
                extra={"task_id": task["id"], "share_code": task.get("shareCode"), "status": task["status"]}
            )
        except Exception as error:
            logger.warning("115 搬运终态记录清理失败", extra={"task_id": task["id"], "error": str(error)})

    async def _find_existing_organized_file(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan123: Pan123OpenAPIClient,
        target_root_id: str,
    ) -> Optional[Dict[str, Any]]:
        title = str(task.get("title") or "").strip()
        if not title:
            return None

        queue = [
            {"dirId": dir_id, "path": ["全部文件"] if dir_id == "0" else ["目标根目录"], "depth": 0}
            for dir_id in _unique_dir_ids(["0", target_root_id or "0"])
        ]
        visited: Set[str] = set()

        while queue:
            current = queue.pop(0)
            if not current or current["dirId"] in visited:
                continue
            visited.add(current["dirId"])

            files = await pan123.list_files(current["dirId"])
            in_title_folder = title in current["path"]
            if in_title_folder:
                matched = next(
                    (item for item in files if item.get("type") != 1 and int(item.get("size") or 0) == int(file.get("size") or 0)),
                    None,
                )
                if matched:
                    return {"file": matched, "path": "/".join([*current["path"], matched.get("filename", "")])}

            if current["depth"] >= 3:
                continue
            for child in (item for item in files if item.get("type") == 1):
                child_name = str(child.get("filename") or "").strip()
                next_depth = current["depth"] + 1
                if next_depth >= 3 and child_name != title:
                    continue
                queue.append({
                    "dirId": str(child.get("fileId") or child.get("id") or ""),
                    "path": [*current["path"], child_name],
                    "depth": next_depth,
                })
        return None

    async def _snapshot_candidate_file_ids(
        self, pan123: Pan123OpenAPIClient, target_root_id: str, target_dir_id: str
    ) -> Dict[str, Set[int]]:
        result: Dict[str, Set[int]] = {}
        for dir_id in _unique_dir_ids([target_dir_id, target_root_id or "0"]):
            files = await pan123.list_files(dir_id)
            result[dir_id] = {int(item.get("fileId") or item.get("id") or 0) for item in files if item.get("type") != 1}
        return result

    async def _validate_pan115_download_url(self, task: Dict[str, Any], file: Dict[str, Any], download_url: str) -> None:
        expected_size = int(file.get("size") or 0)
        if not math.isfinite(expected_size) or expected_size <= 0:
            return
        download_user_agent = PAN123_OFFLINE_USER_AGENT if _is_local_115_file(file) else PAN115_DOWNLOAD_USER_AGENT

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.head(
                    download_url,
                    headers={"accept": "*/*", "user-agent": download_user_agent},
                )
            head_reason = _inspect_download_probe(response, expected_size, "HEAD")
            if head_reason == "ok":
                return
            if head_reason != "defer":
                raise RuntimeError(head_reason)
        except Exception as error:
            message = str(error)
            if not re.search(r"AbortError|405|501|head", message, re.I):
                raise RuntimeError(message if "预检" in message else f"115 直链预检失败：{message}")

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(
                download_url,
                headers={"accept": "*/*", "range": "bytes=0-0", "user-agent": download_user_agent},
            )
        probe_reason = _inspect_download_probe(response, expected_size, "GET")
        if probe_reason != "ok":
            raise RuntimeError(probe_reason)
        _add_task_log(task, "info", f"115 直链预检通过：{_display_path(file)}")

    async def _find_suspicious_offline_artifact(
        self,
        task: Dict[str, Any],
        pan123: Pan123OpenAPIClient,
        target_dir_id: str,
        file: Dict[str, Any],
        before_offline_file_ids: Dict[str, Set[int]],
    ) -> Optional[Dict[str, Any]]:
        expected_size = int(file.get("size") or 0)
        if not math.isfinite(expected_size) or expected_size <= 0:
            return None
        if expected_size <= 1024 * 1024:
            return None

        candidate_names = _offline_candidate_names(file)
        existing_ids = before_offline_file_ids.get(target_dir_id, set())
        files = await pan123.list_files(target_dir_id)
        for candidate in files:
            if candidate.get("type") == 1:
                continue
            if int(candidate.get("fileId") or candidate.get("id") or 0) in existing_ids:
                continue
            if candidate.get("filename") not in candidate_names:
                continue
            size = int(candidate.get("size") or 0)
            if size <= 0 or size >= expected_size:
                continue
            if size > 1024 * 1024:
                continue
            key = f"{target_dir_id}:{candidate.get('fileId')}:{candidate.get('filename')}:{size}"
            if key in self._warned_suspicious_offline_artifacts:
                continue
            self._warned_suspicious_offline_artifacts.add(key)
            _add_task_log(task, "warn", f"123 发现疑似错误页落盘：{_display_path(file)} -> {candidate['filename']}（{_format_bytes(size)}）")
            await pan123.trash_files([int(candidate.get("fileId") or candidate.get("id") or 0)])
            _add_task_log(task, "info", f"已将疑似错误页文件移入 123 回收站：{candidate['filename']}")
            return candidate
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
class _Pan123OpenTokenStoreAdapter(Pan123OpenTokenStore):
    """将 SessionStore 的 pan123_open_token 三个方法适配为 Pan123OpenAPIClient 的 token_store。

    SessionStore 的同步方法包装为 async，保持 Pan123OpenAPIClient 与 SessionStore 解耦。
    """

    def __init__(self, store: SessionStore, token_key: str):
        self._store = store
        self._token_key = token_key

    async def load(self) -> Optional[Dict[str, Any]]:
        return self._store.get_pan123_open_token(self._token_key)

    async def save(self, access_token: str, expires_at: float) -> None:
        self._store.save_pan123_open_token(self._token_key, access_token, expires_at)

    async def clear(self) -> None:
        self._store.delete_pan123_open_token(self._token_key)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _unique_115_links(links: List[Any]) -> List[Any]:
    seen: Set[str] = set()
    unique: List[Any] = []
    for link in links:
        key = f"{link.get('share_code', '')}:{link.get('receive_code') or ''}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)
    return unique


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
    return str(task.get("shareCode") or "").lower().startswith("local:") or str(task.get("shareUrl") or "").lower().startswith("115://local")


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


def _transfer_exclude_suffixes(config: Dict[str, Any]) -> List[str]:
    values = [
        config.get("excludeSuffix"),
        config.get("excludeSuffixes"),
        config.get("exclude_suffix"),
    ]
    result: List[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item or "") for item in value)
        else:
            result.append(str(value or ""))
    return result


def _transfer_exclude_cids(config: Dict[str, Any]) -> List[str]:
    values = [
        config.get("excludeCid"),
        config.get("excludeCids"),
        config.get("exclude_cid"),
    ]
    result: List[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item or "") for item in value)
        else:
            result.append(str(value or ""))
    return [
        part.strip()
        for value in result
        for part in re.split(r"[\s,;，；]+", value)
        if part.strip()
    ]


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
    return [_parse_cookie_account(line, index) for index, line in enumerate(lines)]


def _parse_cookie_account(line: str, index: int) -> Dict[str, str]:
    split_index = line.find("|")
    if split_index > 0:
        name = line[:split_index].strip()
        cookie = line[split_index + 1 :].strip()
        if name and cookie:
            return {"name": name, "cookie": cookie}
    return {"name": f"Cookie {index + 1}", "cookie": line}


def _unique_dir_ids(values: List[str]) -> List[str]:
    return list(dict.fromkeys([str(value or "0") for value in values if value]))


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


def _file_key(file: Dict[str, Any]) -> str:
    return f"{'/'.join(file.get('path', []))}/{file.get('name')}:{file.get('size')}"


def _is_done_file(file: Dict[str, Any]) -> bool:
    return file.get("status") in ("success", "skipped")


def _transfer_status_label(status: Optional[str]) -> str:
    if status == "queued":
        return "排队中"
    if status == "running":
        return "进行中"
    return str(status or "")


def _transfer_cleanup_message_ids(task: Dict[str, Any]) -> List[int]:
    ids = list(dict.fromkeys([
        *(task.get("transferNoticeMessageIds", []) or []),
        task.get("transferFinalMessageId"),
    ]))
    return [int(id_) for id_ in ids if isinstance(id_, (int, float)) and not math.isnan(id_)]


def _is_final_transfer_status(status: Optional[str]) -> bool:
    return status in ("success", "partial", "failed")


def _transfer_notice_fingerprint(task: Dict[str, Any]) -> str:
    def _or_empty(value: Any) -> str:
        return "" if value is None else str(value)

    file_states = "|".join(
        ":".join([
            str(file.get("id")),
            str(file.get("status")),
            _or_empty(file.get("method")),
            _or_empty(file.get("offlineStatus")),
            _or_empty(file.get("offlineProgress")),
            _or_empty(file.get("error")),
        ])
        for file in task.get("files", [])
    )
    logs = task.get("logs", [])
    last_log = logs[-1].get("message", "") if logs else ""
    return "|".join([
        str(task.get("status")),
        str(task.get("title") or ""),
        str(task.get("totalFiles")),
        str(task.get("doneFiles")),
        str(task.get("error") or ""),
        file_states,
        last_log,
    ])


def _add_task_log(task: Dict[str, Any], level: str, message: str) -> None:
    task.setdefault("logs", []).append({"time": _utc_now_iso(), "level": level, "message": message})
    task["logs"] = task["logs"][-200:]
    level_map = {"warn": "warning"}
    log_level = level_map.get(level, level)
    if log_level not in ("debug", "info", "warning", "error", "critical"):
        log_level = "info"
    getattr(logger, log_level)(
        message,
        extra={"scope": "transfer", "task_id": task.get("id"), "share_code": task.get("shareCode")}
    )


def _add_unique_task_log(task: Dict[str, Any], level: str, message: str) -> None:
    if any(log.get("message") == message for log in task.get("logs", [])):
        return
    _add_task_log(task, level, message)


def _display_path(file: Dict[str, Any]) -> str:
    return "/".join([*file.get("path", []), file.get("name", "")])


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
    return list(dict.fromkeys([name for name in [file.get("name"), file.get("offlineSubmitName")] if name and str(name).strip()]))


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
        cp = ord(char)
        hash_val ^= cp
        hash_val = (hash_val * 16777619) & 0xFFFFFFFF
    return format(hash_val, "x")[:8]


def _is_transient_115_download_error(message: str) -> bool:
    return bool(re.search(r"操作频繁|请稍后|频繁|too\s*many|rate|429|context\.Background|局域网直链|直链预检|错误页", str(message or ""), re.I))


def _is_transient_pan123_share_copy_error(error: Exception) -> bool:
    message = str(error or "")
    code = int(getattr(error, "code", 0) or 0)
    return code in {429, 42902, 42903} or bool(
        re.search(r"操作频繁|请勿频繁|请稍后|访问频繁|请求过快|too\s*many|rate\s*limit|\b429\b", message, re.I)
    )


def _pan123_share_copy_min_interval_ms(config: Dict[str, Any]) -> int:
    return max(0, int(config.get("pan123ShareCopyMinIntervalMs") or PAN123_SHARE_COPY_MIN_INTERVAL_MS))


def _pan123_share_copy_max_attempts(config: Dict[str, Any]) -> int:
    return max(1, min(10, int(config.get("pan123ShareCopyMaxAttempts") or PAN123_SHARE_COPY_MAX_ATTEMPTS)))


def _pan123_share_copy_retry_base_ms(config: Dict[str, Any]) -> int:
    return max(1000, int(config.get("pan123ShareCopyRetryBaseMs") or PAN123_SHARE_COPY_RETRY_BASE_MS))


def _is_missing_target_dir_error(error: Exception) -> bool:
    message = str(error)
    return bool(re.search(r"父级文件ID不存在|指定目录ID文件不存在|parent\s*(?:file\s*)?id.*(?:not\s*found|不存在)|directory\s*id.*(?:not\s*found|不存在)", message, re.I))


def _transfer_runtime_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    return {
        "pauseEnabled": cfg.get("pauseEnabled") if cfg.get("pauseEnabled") is not None else True,
        "pauseTimeZone": cfg.get("pauseTimeZone") or TRANSFER_PAUSE_TIME_ZONE,
        "pauseStartHour": int(cfg["pauseStartHour"]) if cfg.get("pauseStartHour") is not None and math.isfinite(float(cfg["pauseStartHour"])) else TRANSFER_PAUSE_START_HOUR,
        "pauseEndHour": int(cfg["pauseEndHour"]) if cfg.get("pauseEndHour") is not None and math.isfinite(float(cfg["pauseEndHour"])) else TRANSFER_PAUSE_END_HOUR,
        "downloadMinIntervalMs": int(cfg["downloadMinIntervalMs"]) if cfg.get("downloadMinIntervalMs") is not None and math.isfinite(float(cfg["downloadMinIntervalMs"])) else PAN115_DOWNLOAD_MIN_INTERVAL_MS,
        "downloadMaxAttempts": int(cfg["downloadMaxAttempts"]) if cfg.get("downloadMaxAttempts") is not None and math.isfinite(float(cfg["downloadMaxAttempts"])) else PAN115_DOWNLOAD_MAX_ATTEMPTS,
        "downloadRetryBaseMs": int(cfg["downloadRetryBaseMs"]) if cfg.get("downloadRetryBaseMs") is not None and math.isfinite(float(cfg["downloadRetryBaseMs"])) else PAN115_DOWNLOAD_RETRY_BASE_MS,
        "offlinePollMs": int(cfg["offlinePollMs"]) if cfg.get("offlinePollMs") is not None and math.isfinite(float(cfg["offlinePollMs"])) else DEFAULT_POLL_MS,
        "offlineMaxPolls": int(cfg["offlineMaxPolls"]) if cfg.get("offlineMaxPolls") is not None and math.isfinite(float(cfg["offlineMaxPolls"])) else DEFAULT_MAX_POLLS,
        "progressNotifyIntervalMs": int(cfg["progressNotifyIntervalMs"]) if cfg.get("progressNotifyIntervalMs") is not None and math.isfinite(float(cfg["progressNotifyIntervalMs"])) else TRANSFER_PROGRESS_NOTIFY_INTERVAL_MS,
    }


def _is_within_transfer_pause(now: datetime, settings: Optional[Dict[str, Any]] = None) -> bool:
    settings = settings or _transfer_runtime_settings()
    start = _normalize_hour(settings.get("pauseStartHour", 18))
    end = _normalize_hour(settings.get("pauseEndHour", 1))
    if start == end:
        return False
    hour = _hour_in_time_zone(now, settings.get("pauseTimeZone", "Asia/Shanghai"))
    return (start < end and start <= hour < end) or (start > end and (hour >= start or hour < end))


def _hour_in_time_zone(now: datetime, time_zone: str) -> int:
    try:
        import pytz
        tz = pytz.timezone(time_zone)
        localized = now.astimezone(tz)
        return localized.hour
    except Exception:
        return now.hour


def _normalize_hour(value: Any) -> int:
    try:
        hour = int(float(value)) % 24
    except (TypeError, ValueError):
        hour = 0
    return hour + 24 if hour < 0 else hour


def _format_transfer_pause_window(settings: Optional[Dict[str, Any]] = None) -> str:
    settings = settings or _transfer_runtime_settings()
    return f"{_format_hour(settings.get('pauseStartHour'))}-{_format_hour(settings.get('pauseEndHour'))}"


def _format_hour(value: Any) -> str:
    return f"{_normalize_hour(value):02d}:00"


def _inspect_download_probe(response: httpx.Response, expected_size: int, phase: str) -> str:
    content_type = str(response.headers.get("content-type", "")).lower()
    content_length = int(response.headers.get("content-length", "0") or "0")
    content_range = str(response.headers.get("content-range", ""))
    total_from_range = _parse_content_range_total(content_range)
    expected_huge = expected_size > 1024 * 1024

    if phase == "HEAD" and not response.is_success:
        return "defer"
    if not response.is_success and response.status_code != 206:
        return f"115 直链预检返回 HTTP {response.status_code}"
    if content_type and re.search(r"html|json|xml", content_type) and expected_huge:
        return f"115 直链预检返回错误页（{content_type}）"
    if expected_huge and 0 < content_length < 1024:
        return f"115 直链预检内容过小：{_format_bytes(content_length)}"
    if expected_huge and 0 < content_length != expected_size and not content_range and phase == "GET":
        return f"115 直链预检大小不符：期望 {_format_bytes(expected_size)}，实际约 {_format_bytes(content_length)}"
    if expected_huge and total_from_range and total_from_range > 0 and total_from_range != expected_size:
        return f"115 直链预检大小不符：期望 {_format_bytes(expected_size)}，实际约 {_format_bytes(total_from_range)}"
    return "ok"


def _parse_content_range_total(value: str) -> int:
    match = re.search(r"/(\d+)$", str(value or ""))
    return int(match.group(1)) if match else 0


def _format_bytes(bytes_val: Union[int, float]) -> str:
    value = max(0, float(bytes_val) or 0)
    if value < 1024:
        return f"{round(value)} B"
    if value < 1024 * 1024:
        return f"{(value / 1024):.1f} KB" if value < 10 * 1024 else f"{(value / 1024):.0f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{(value / (1024 * 1024)):.1f} MB" if value < 10 * 1024 * 1024 else f"{(value / (1024 * 1024)):.0f} MB"
    return f"{(value / (1024 * 1024 * 1024)):.1f} GB"


async def _delay(ms: int) -> None:
    await asyncio.sleep(ms / 1000)
