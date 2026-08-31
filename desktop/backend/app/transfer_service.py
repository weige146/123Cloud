"""115 to 123 cloud transfer service（任务调度门面）。

任务编排保留在这里：入队、去重、并发调度、暂停窗口、账号池健康、
115 直链获取与轮换、通知钩子。单次搬运的六个执行阶段（解析→规划→
秒传→离线→等待→收尾）在 transfer_pipeline.TransferPipeline 里。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
from uuid import uuid4

import httpx

from .pan115 import select_115_account
from .pan115_transfer import (
    classify_pan115_account_error,
    extract_115_links,
    PAN123_OFFLINE_USER_AGENT,
    Pan115TransferClient,
)
from .pan123 import Pan123Client, Pan123OpenAPIClient, Pan123OpenTokenStore
from .session_store import SessionStore, pan123_open_token_key
# 管线与工具函数统一定义在 transfer_pipeline，这里引用并向上层保持兼容导出
from .transfer_pipeline import (  # noqa: F401
    TaskCancelled,
    TransferDirCache,
    TransferPipeline,
    _add_task_log,
    _add_unique_task_log,
    _build_offline_submit_name,
    _build_pan123_path,
    _delay,
    _display_path,
    _file_key,
    _format_bytes,
    _is_done_file,
    _is_local_115_cid_ref,
    _is_local_115_file,
    _is_local_115_task,
    _is_missing_target_dir_error,
    _local_115_cid_from_ref,
    _local_115_path_from_task,
    _local_115_task_code,
    _local_115_task_title,
    _local_115_task_url,
    _merge_files,
    _normalize_local_115_ref,
    _offline_candidate_names,
    _offline_wait_deadline_ms,
    _transfer_cookie_pool,
    _utc_now_iso,
)

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
PAN123_SHARE_COPY_MIN_INTERVAL_MS = int(os.environ.get("PAN123_SHARE_COPY_MIN_INTERVAL_MS", "10000"))
PAN123_SHARE_COPY_MAX_ATTEMPTS = int(os.environ.get("PAN123_SHARE_COPY_MAX_ATTEMPTS", "4"))
PAN123_SHARE_COPY_RETRY_BASE_MS = int(os.environ.get("PAN123_SHARE_COPY_RETRY_BASE_MS", "10000"))
# 失效 115 账号的全局冷却时长；期间其他任务不再选用该账号
PAN115_ACCOUNT_COOLDOWN_MS = _normalize_non_negative_ms(
    os.environ.get("TRANSFER_115_ACCOUNT_COOLDOWN_MS"), 30 * 60_000
)
# 全局 123 离线提交并发闸门，避免多任务同时提交撞"同时下载超出最大限制"
TRANSFER_OFFLINE_SUBMIT_CONCURRENCY = max(1, int(os.environ.get("TRANSFER_OFFLINE_SUBMIT_CONCURRENCY", "3")))


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
TransferNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]]
TransferQueuedNotifier = Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, Any]]
TransferCookieNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]]
TransferCleanupNotifier = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# TransferService
# ---------------------------------------------------------------------------
class TransferService:
    def __init__(self, store: SessionStore, read_pan_session: Optional[Callable[[], Coroutine[Any, Any, Optional[Dict[str, Any]]]]] = None):
        self._store = store
        self._read_pan_session = read_pan_session
        self._running = False
        self._active_task_ids: Set[str] = set()
        self._deleted_task_ids: Set[str] = set()
        self._notifier: Optional[TransferNotifier] = None
        self._queued_notifier: Optional[TransferQueuedNotifier] = None
        self._cookie_notifier: Optional[TransferCookieNotifier] = None
        self._cleanup_notifier: Optional[TransferCleanupNotifier] = None
        self._pan115_download_queue: Optional[asyncio.Future[Any]] = None
        self._last_pan115_download_at = 0.0
        self._cached_transfer_config: Optional[Dict[str, Any]] = None
        # 相同凭证复用同一 Pan123OpenAPIClient 实例，共享内存 token
        self._pan123_open_client: Optional[Pan123OpenAPIClient] = None
        self._pan123_open_client_key: str = ""
        self._pan123_open_client_lock: Optional[asyncio.Lock] = None
        self._pan123_web_client = Pan123Client()
        self._active_pan123_share_copy_task_id: Optional[str] = None
        self._last_pan123_share_copy_submit_at = 0.0
        # 任务级管线（解析/规划/秒传/离线各阶段的缓存挂在管线上）
        self._pipelines: Dict[str, TransferPipeline] = {}
        # 115 账号健康状态：cookie 指纹 -> {"name", "coolUntilMs"}；失效账号冷却期内跳过
        self._account_health: Dict[str, Dict[str, Any]] = {}
        # Python 3.9 下在无事件循环时创建 Semaphore 会踩 get_event_loop 的坑，推迟到协程里创建
        self._offline_submit_semaphore: Optional[asyncio.Semaphore] = None

    def _get_offline_submit_semaphore(self) -> asyncio.Semaphore:
        if self._offline_submit_semaphore is None:
            self._offline_submit_semaphore = asyncio.Semaphore(TRANSFER_OFFLINE_SUBMIT_CONCURRENCY)
        return self._offline_submit_semaphore

    def set_notifier(self, notifier: Optional[TransferNotifier]) -> None:
        """搬运终态（成功/失败/部分失败）通知。"""
        self._notifier = notifier

    def set_queued_notifier(self, notifier: Optional[TransferQueuedNotifier]) -> None:
        """任务入队通知。"""
        self._queued_notifier = notifier

    def set_cookie_notifier(self, notifier: Optional[TransferCookieNotifier]) -> None:
        """115 Cookie 失效告警。"""
        self._cookie_notifier = notifier

    def set_cleanup_notifier(self, notifier: Optional[TransferCleanupNotifier]) -> None:
        """搬运结束后清理 Telegram 里的排队/进度消息。"""
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
        logger.info(
            f"搬运任务已删除：{task.get('title') or task.get('shareCode') or task_id}",
            extra={"task_id": task_id, "share_code": task.get("shareCode")},
        )

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

    def _local_source_account_or_none(self) -> Optional[Dict[str, str]]:
        try:
            return self._local_source_pan115_account()
        except Exception:
            return None

    def _fallback_account(self) -> Dict[str, str]:
        return {
            "name": "默认 Cookie",
            "cookie": str((self._cached_transfer_config or {}).get("pan115Cookie") or ""),
        }

    # -----------------------------------------------------------------------
    # 115 账号池健康（失效跳过 + 全局冷却）
    # -----------------------------------------------------------------------
    @staticmethod
    def _account_fingerprint(cookie: str) -> str:
        return hashlib.sha1(str(cookie or "").strip().encode("utf-8")).hexdigest()[:16]

    def _is_account_cooling(self, account: Dict[str, str]) -> bool:
        health = self._account_health.get(self._account_fingerprint(account.get("cookie", "")))
        if not health:
            return False
        return time.monotonic() * 1000 < health.get("coolUntilMs", 0)

    def _filter_live_accounts(self, accounts: List[Dict[str, str]], task: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        live = [account for account in accounts if not self._is_account_cooling(account)]
        cooling = [account for account in accounts if self._is_account_cooling(account)]
        if cooling and task is not None:
            _add_unique_task_log(
                task, "info",
                "跳过冷却中的 115 账号：" + "、".join(account.get("name") or "?" for account in cooling)
            )
        if not live and cooling:
            # 全部冷却时回退使用全部账号，总比不跑强
            if task is not None:
                _add_unique_task_log(task, "warn", "所有 115 账号均在冷却中，仍尝试使用现有账号")
            return list(accounts)
        return live if live else list(accounts)

    async def _mark_account_cooling(self, task: Optional[Dict[str, Any]], account: Dict[str, str], reason: str) -> bool:
        """标记账号进入冷却；返回是否为本次周期首次标记（用于通知去重）。"""
        fingerprint = self._account_fingerprint(account.get("cookie", ""))
        now_ms = time.monotonic() * 1000
        health = self._account_health.get(fingerprint)
        if health and now_ms < health.get("coolUntilMs", 0):
            return False
        self._account_health[fingerprint] = {
            "name": account.get("name") or "未命名账号",
            "coolUntilMs": now_ms + PAN115_ACCOUNT_COOLDOWN_MS,
        }
        if task is not None:
            minutes = round(PAN115_ACCOUNT_COOLDOWN_MS / 60000)
            _add_unique_task_log(
                task, "warn",
                f"115 账号失效，冷却 {minutes} 分钟：{account.get('name') or '?'}（{reason}）"
            )
        if self._cookie_notifier:
            try:
                await self._cookie_notifier({
                    "account": account.get("name") or "未命名账号",
                    "reason": reason,
                    "cooldownMinutes": round(PAN115_ACCOUNT_COOLDOWN_MS / 60000),
                })
            except Exception as error:
                logger.warning("115 Cookie 失效通知失败", extra={"error": str(error)})
        return True

    def _download_url_candidates(self, primary: Optional[Dict[str, str]], allow_rotation: bool) -> List[Dict[str, str]]:
        """取直链候选账号：主账号优先，其余活账号按池顺序兜底（仅分享任务允许换号）。"""
        candidates: List[Dict[str, str]] = []
        if primary and primary.get("cookie"):
            candidates.append(primary)
        if allow_rotation:
            try:
                pool = self._filter_live_accounts(_transfer_cookie_pool(self._cached_transfer_config or {}))
            except Exception:
                pool = []
            for account in pool:
                if account.get("cookie") and all(account["cookie"] != c.get("cookie") for c in candidates):
                    candidates.append(account)
        return candidates or ([primary] if primary else [])

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
            if not await self._save_transfer_task(task):
                self._active_task_ids.discard(task["id"])
                if self._active_pan123_share_copy_task_id == task["id"]:
                    self._active_pan123_share_copy_task_id = None
                continue
            asyncio.create_task(self._run_task_safely_wrapper(task))

    async def _run_task_safely_wrapper(self, task: Dict[str, Any]) -> None:
        try:
            await self._run_task(task)
        except Exception as error:
            task["status"] = "failed"
            task["error"] = str(error)
            task["finishedAt"] = _utc_now_iso()
            _add_task_log(task, "error", f"任务失败：{error}")
            if not await self._save_transfer_task(task):
                return
            await self._notify_task(task)
            await self._discard_finished_task_record(task)
            logger.error("115 搬运任务失败", extra={"task_id": task["id"], "error": task["error"]})
        finally:
            self._active_task_ids.discard(task["id"])
            self._pipelines.pop(task["id"], None)
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

        pipeline = TransferPipeline(self, task, transfer_config)
        self._pipelines[task["id"]] = pipeline
        try:
            await pipeline.run()
        except TaskCancelled:
            # 任务已被用户删除，静默退出
            return

        failed = len([f for f in task.get("files", []) if f.get("status") == "failed"])
        total = int(task.get("totalFiles") or 0)
        logger.log(
            logging.WARNING if failed else logging.INFO,
            f"{task.get('title') or task.get('shareCode') or '115 任务'} 搬运结束："
            f"{'部分失败' if 0 < failed < total else '失败' if failed else '全部成功'}"
            f"（共 {total} 个文件，失败 {failed} 个）",
            extra={"task_id": task["id"], "status": task.get("status"), "total": total, "failed": failed},
        )
        if not await self._save_transfer_task(task):
            return
        await self._notify_task(task)
        await self._discard_finished_task_record(task)

    async def _run_pan123_share_copy(self, task: Dict[str, Any], transfer_config: Dict[str, Any]) -> None:
        session = self._store.read_session()
        if not session or not session.get("token"):
            raise RuntimeError("后端未登录 123 云盘，无法转存")
        _add_task_log(task, "info", f"开始转存 123 分享：{task.get('title') or task.get('shareUrl')}")
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
            _add_task_log(task, "info", f"分享里有 {len(task['files'])} 个文件，提交 123 官方转存")
            remote_task_id = await self._submit_pan123_share_copy_with_retry(
                task,
                session,
                items,
                transfer_config,
            )
            task["remoteTaskId"] = remote_task_id
            _add_task_log(task, "info", f"123 转存已提交，远端任务 ID：{remote_task_id}，开始等待转存完成")
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
                _add_task_log(task, "warn", f"改用 115 账号解析分享：{account['name']}")
            try:
                client = Pan115TransferClient(account["cookie"])
                inspection = await client.inspect_and_flatten(link)
                if index > 0:
                    _add_task_log(task, "info", f"115 分享解析成功：{account['name']}")
                return {"accountIndex": index, "inspection": inspection}
            except Exception as error:
                message = str(error)
                errors.append(f"{account['name']}：{message}")
                if classify_pan115_account_error(error) == "expired":
                    await self._mark_account_cooling(task, account, message)
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
        _add_task_log(task, "info", f"115 本地盘使用账号：{account['name']}")
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
        client_id = str(transfer_config.get("pan123ClientId") or "").strip()
        client_secret = str(transfer_config.get("pan123ClientSecret") or "").strip()
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

    async def _get_pan115_download_url(
        self,
        task: Dict[str, Any],
        file: Dict[str, Any],
        pan115: Pan115TransferClient,
        pan115_account: Optional[Dict[str, str]] = None,
    ) -> str:
        transfer_config = await self._get_transfer_config()
        max_attempts = max(1, int(transfer_config.get("downloadMaxAttempts") or PAN115_DOWNLOAD_MAX_ATTEMPTS))
        retry_base_ms = max(0, int(transfer_config.get("downloadRetryBaseMs") or PAN115_DOWNLOAD_RETRY_BASE_MS))

        async def _attempt(client: Pan115TransferClient) -> str:
            last_error = ""
            for attempt in range(1, max_attempts + 1):
                try:
                    if _is_local_115_file(file):
                        download_url = await client.get_local_download_url(str(file.get("pickCode") or file.get("pick_code") or ""))
                    else:
                        download_url = await client.get_download_url(task["shareCode"], task.get("receiveCode") or "", file["id"])
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

        async def _job() -> str:
            # 本地盘文件只存在于指定账号的网盘里，不换号；分享任务可换池内其他活账号
            allow_rotation = not _is_local_115_file(file) and not _is_local_115_task(task)
            candidates = self._download_url_candidates(pan115_account, allow_rotation)
            if not candidates:
                return await _attempt(pan115)
            last_error = ""
            for index, candidate in enumerate(candidates):
                if index == 0:
                    client = pan115
                else:
                    client = Pan115TransferClient(str(candidate.get("cookie") or ""))
                try:
                    return await _attempt(client)
                except Exception as error:
                    last_error = str(error)
                    classification = classify_pan115_account_error(error)
                    if classification == "expired":
                        await self._mark_account_cooling(task, candidate, last_error)
                    elif classification != "transient":
                        # 未知错误换号意义不大，直接失败便于排查
                        raise
                    elif index + 1 < len(candidates):
                        _add_task_log(task, "warn", f"115 取直链持续受限，换号重试：{candidate.get('name') or '?'}")
                    if index + 1 < len(candidates):
                        _add_task_log(
                            task, "warn",
                            f"改用其他 115 账号取直链：{candidates[index + 1].get('name') or '?'}（{_display_path(file)}）"
                        )
                finally:
                    if index > 0:
                        await client.close()
            raise RuntimeError(last_error or "115 未返回文件直链")

        return await self._enqueue_pan115_download(_job)

    async def _enqueue_pan115_download(self, job: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """115 直链请求串行化 + 限速：所有取直链/删除操作排一条队，间隔可配。"""
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

    # -----------------------------------------------------------------------
    # Notification helpers
    # -----------------------------------------------------------------------
    async def _notify_task(self, task: Dict[str, Any]) -> None:
        """搬运终态通知 + Telegram 相关消息清理。

        成功：先删掉排队/进度消息和用户的链接消息，再发一条最终结果（保留）；
        失败/部分失败：删掉排队/进度消息和用户链接消息，保留最终的失败说明。
        """
        is_success_115 = task["status"] == "success" and str(task.get("kind") or "") != "pan123_share_copy"
        if is_success_115:
            await self._cleanup_successful_task_messages(task)
        if not self._notifier:
            return
        try:
            ref = await self._notifier(task)
            ref_message_id = ref.get("messageId") if isinstance(ref, dict) else None
            if ref:
                self._remember_notice(task, ref)
                task["transferFinalMessageId"] = ref_message_id
            is_pan123_copy = str(task.get("kind") or "") == "pan123_share_copy"
            cleanup_failure_messages = bool(task.get("chatId")) and task["status"] in ("failed", "partial")
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
                await self._cleanup_notifier({"chatId": cleanup_chat_id, "messageIds": message_ids})
                task["transferNoticeMessageIds"] = []
                if cleanup_failure_messages and not is_pan123_copy:
                    task["transferFinalMessageId"] = None
                await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("搬运结果通知失败", extra={"task_id": task["id"], "error": str(error)})

    async def _cleanup_successful_task_messages(self, task: Dict[str, Any]) -> None:
        cleanup_chat_id = task.get("transferNoticeChatId") or task.get("chatId")
        message_ids = [
            *_transfer_cleanup_message_ids(task),
            *([task.get("messageId")] if task.get("messageId") else []),
        ]
        message_ids = [m for m in message_ids if isinstance(m, (int, float)) and not math.isnan(m)]
        if not cleanup_chat_id or not message_ids or not self._cleanup_notifier:
            return
        try:
            await self._cleanup_notifier({"chatId": cleanup_chat_id, "messageIds": message_ids})
            task["transferNoticeMessageIds"] = []
            task["transferFinalMessageId"] = None
            await self._save_transfer_task(task)
        except Exception as error:
            logger.warning("搬运成功消息清理失败", extra={"task_id": task["id"], "error": str(error)})

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
            logger.warning("搬运入队通知失败", extra={"error": str(error)})

    async def _notify_deleted_task(self, task: Dict[str, Any]) -> None:
        """后台手动删除任务时，顺手清掉 Telegram 里的排队/进度消息。"""
        if not self._cleanup_notifier:
            return
        try:
            message_ids = _transfer_cleanup_message_ids(task)
            cleanup_chat_id = task.get("transferNoticeChatId") or task.get("chatId")
            if cleanup_chat_id and message_ids:
                await self._cleanup_notifier({"chatId": cleanup_chat_id, "messageIds": message_ids})
        except Exception as error:
            logger.warning("搬运删除通知失败", extra={"task_id": task["id"], "error": str(error)})

    def _remember_notice(self, task: Dict[str, Any], ref: Dict[str, Any]) -> None:
        chat_id = ref.get("chatId")
        message_id = ref.get("messageId")
        if not isinstance(chat_id, (int, float)) or math.isnan(chat_id) or not isinstance(message_id, (int, float)) or math.isnan(message_id):
            return
        task["transferNoticeChatId"] = int(chat_id)
        existing = set(task.get("transferNoticeMessageIds", []) or [])
        existing.add(int(message_id))
        task["transferNoticeMessageIds"] = list(existing)

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
        try:
            self._store.delete_transfer_task(task["id"])
            logger.info(
                "已完成的任务记录已清理（结果保留在日志里）",
                extra={"task_id": task["id"], "share_code": task.get("shareCode"), "status": task["status"]},
            )
        except Exception as error:
            logger.warning("任务记录清理失败", extra={"task_id": task["id"], "error": str(error)})

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


def _utc_now_iso_service() -> str:
    return _utc_now_iso()


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


def _transfer_cleanup_message_ids(task: Dict[str, Any]) -> List[int]:
    ids = list(dict.fromkeys([
        *(task.get("transferNoticeMessageIds", []) or []),
        task.get("transferFinalMessageId"),
    ]))
    return [int(id_) for id_ in ids if isinstance(id_, (int, float)) and not math.isnan(id_)]


def _transfer_status_label(status: Optional[str]) -> str:
    if status == "queued":
        return "排队中"
    if status == "running":
        return "进行中"
    return str(status or "")


def _is_final_transfer_status(status: Optional[str]) -> bool:
    return status in ("success", "partial", "failed")


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
    # 优先标准库 zoneinfo（pytz 不一定是依赖，缺失时曾静默回退 UTC 小时，导致暂停窗口按错时区生效）
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(time_zone)).hour
    except Exception:
        pass
    try:
        import pytz

        return now.astimezone(pytz.timezone(time_zone)).hour
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
