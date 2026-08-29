from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, parse_qsl, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 配置日志格式：包含时间戳、级别、logger 名、消息，便于排查
# uvicorn 已有自己的日志配置，这里只接管应用层 logger
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

from .pan115 import empty_115_recycle, extract_pan115_offline_links, helper_status, submit_115_offline_from_text
from .pan115_cookie import (
    PAN115_QR_DEVICES,
    confirm_pan115_qr_login,
    create_pan115_qr_session,
    get_pan115_qr_session,
    get_pan115_qr_status,
    pan115_qr_sessions,
    pan115_status_text,
)
from .pan123 import Pan123Client, Pan123Error, parse_pan123_share_url
from .pan115_transfer import extract_115_links
from .session_store import SessionStore
from .submission import (
    build_submission_display_preview,
    clear_submission_drafts,
    close_telegram_client,
    delete_telegram_messages,
    delete_submission_draft,
    extract_submission_links,
    handle_submission_telegram_update,
    list_submission_drafts,
    send_telegram_text,
    start_telegram_client,
    submit_existing_draft,
    submit_submission_links,
    submit_submission_text,
    telegram_message_text,
    telegram_message_id,
    telegram_admin_allowed,
    telegram_channel_owner_allowed,
)
from .transfer_service import TransferService
from .wallpapers import BingWallpaperService, WallpaperUpstreamError


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT_DIR / "data")


def _resolve_admin_web_dir() -> Path:
    """Locate the admin SPA build.

    Order: explicit env override → PyInstaller-bundled copy (desktop app) →
    repo checkout (dev / server mode).
    """
    env_dir = os.environ.get("CLOUD123_ADMIN_DIR")
    if env_dir:
        return Path(env_dir)
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "adminweb"
        if bundled.exists():
            return bundled
    return ROOT_DIR / "web" / "dist"


ADMIN_WEB_DIR = _resolve_admin_web_dir()

store = SessionStore(DATA_DIR)
pan123 = Pan123Client()
transfer_service = TransferService(store)
wallpaper_service = BingWallpaperService()
logger = logging.getLogger(__name__)
telegram_polling_task: Optional[asyncio.Task[None]] = None
pan115_recycle_cleanup_task: Optional[asyncio.Task[None]] = None
PAN123_COPY_PASSWORD_PENDING_PREFIX = "telegram_pan123_copy_password:"
PAN123_COPY_PASSWORD_TTL_SECONDS = 600
TELEGRAM_BOT_COMMANDS = [
    {"command": "start", "description": "搬运默认 115 本地盘目录"},
    {"command": "help", "description": "查看使用说明"},
    {"command": "myid", "description": "查看我的 Telegram UID"},
    {"command": "recycle", "description": "清空 115 回收站"},
    {"command": "channels", "description": "管理我的投稿频道"},
]

@contextlib.asynccontextmanager
async def app_lifespan(_app: FastAPI):
    await start_background_tasks()
    try:
        yield
    finally:
        await stop_background_tasks()


app = FastAPI(title="123 Cloud Gateway", version="0.1.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def start_background_tasks() -> None:
    global telegram_polling_task, pan115_recycle_cleanup_task
    await start_telegram_client()
    transfer_service.set_queued_notifier(send_telegram_transfer_queued_messages)
    transfer_service.set_notifier(send_telegram_transfer_status_message)
    transfer_service.set_cleanup_notifier(cleanup_telegram_transfer_messages)
    if telegram_polling_task and not telegram_polling_task.done():
        pass
    else:
        telegram_polling_task = asyncio.create_task(telegram_polling_loop())
    if pan115_recycle_cleanup_task and not pan115_recycle_cleanup_task.done():
        pass
    else:
        pan115_recycle_cleanup_task = asyncio.create_task(pan115_recycle_cleanup_loop())
    await transfer_service.init()


async def stop_background_tasks() -> None:
    for task in (telegram_polling_task, pan115_recycle_cleanup_task):
        if not task or task.done():
            continue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await transfer_service.close()
    await pan123.close()
    await close_telegram_client()


async def telegram_polling_loop() -> None:
    active_token = ""
    offset: Optional[int] = None
    async with httpx.AsyncClient(timeout=35.0) as client:
        while True:
            try:
                config = store.read_submission_config()
                bot_token = str(config.get("botToken") or "").strip()
                if not bot_token:
                    active_token = ""
                    offset = None
                    await asyncio.sleep(5)
                    continue

                if bot_token != active_token:
                    active_token = bot_token
                    offset = None
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/deleteWebhook",
                        json={"drop_pending_updates": False},
                        timeout=20.0,
                    )
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/setMyCommands",
                        json={"commands": TELEGRAM_BOT_COMMANDS},
                        timeout=20.0,
                    )

                response = await client.get(
                    f"https://api.telegram.org/bot{bot_token}/getUpdates",
                    params={
                        "timeout": 25,
                        "offset": offset,
                        "allowed_updates": '["message","callback_query"]',
                    },
                )
                data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                if response.status_code >= 400 or not data.get("ok", True):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result") or []:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    if isinstance(update, dict):
                        with contextlib.suppress(Exception):
                            if await handle_transfer_telegram_update(update, bot_token, config):
                                continue
                            await handle_submission_telegram_update(store, update)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)


async def handle_transfer_telegram_update(update: Dict[str, Any], bot_token: str, config: Dict[str, Any]) -> bool:
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    if not message:
        return False
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if str(chat.get("type") or "") != "private":
        return False
    chat_id = safe_int(chat.get("id"))
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    user_id = safe_int(user.get("id"))
    if not telegram_admin_allowed(config, user_id):
        return False
    text = telegram_message_text(message).strip()
    if not text:
        return False
    source_message_id = safe_int(message.get("message_id"))

    if await handle_pending_pan123_copy_password(bot_token, chat_id, user_id, source_message_id, text):
        return True

    if text.startswith("/"):
        command_text, _, payload = text.partition(" ")
        command = command_text.split("@", 1)[0].lower()
        if command == "/help":
            sent = await send_telegram_text(bot_token, chat_id, telegram_pan115_help_text())
            await delete_telegram_messages(bot_token, chat_id, [source_message_id, telegram_message_id(sent)])
            return True
        if command == "/recycle":
            helper = config.get("pan115Helper") if isinstance(config.get("pan115Helper"), dict) else {}
            try:
                result = await empty_115_recycle(helper)
                sent = await send_telegram_text(bot_token, chat_id, format_pan115_action_result("115 回收站清理", result))
                await delete_telegram_messages(bot_token, chat_id, [source_message_id, telegram_message_id(sent)])
            except Exception as error:
                await send_telegram_text(bot_token, chat_id, f"115 回收站清理失败：{error}")
            return True
        if command != "/start":
            return False

        transfer_config = normalize_transfer_config(store.read_config())
        path_115 = payload.strip() or str(transfer_config.get("localPath115") or "").strip()
        if not path_115:
            await send_telegram_text(bot_token, chat_id, "请先在后台“115 搬运”配置默认本地盘路径 / CID，也可以使用 /start 路径或CID。")
            return True
        try:
            await transfer_service.enqueue_local_path(
                path_115,
                "telegram",
                chat_id=chat_id,
                user_id=user_id,
                message_id=source_message_id,
            )
        except Exception as error:
            await send_telegram_text(bot_token, chat_id, f"115 本地盘搬运入队失败：{error}")
        return True

    submission_links = extract_submission_links(text)[:3]
    web_share_links = [link for link in submission_links if str(link.get("provider") or "") == "123pan"]
    if web_share_links:
        return await handle_admin_pan123_share_links(
            bot_token,
            chat_id,
            user_id,
            source_message_id,
            text,
            user,
            submission_links,
        )

    share_links = extract_115_links(text)
    offline_links = extract_pan115_offline_links(text)
    if not share_links and not offline_links:
        return False

    if share_links:
        try:
            await transfer_service.enqueue_from_text(
                text,
                "telegram",
                chat_id=chat_id,
                user_id=user_id,
                message_id=source_message_id,
            )
        except Exception as error:
            await send_telegram_text(bot_token, chat_id, f"115 分享搬运入队失败：{error}")

    if offline_links:
        helper = config.get("pan115Helper") if isinstance(config.get("pan115Helper"), dict) else {}
        try:
            result = await submit_115_offline_from_text(helper, text)
            sent = await send_telegram_text(bot_token, chat_id, format_pan115_action_result("115 离线提交", result))
            result_msg_id = telegram_message_id(sent)
            if result_msg_id and source_message_id:
                await delete_telegram_messages(bot_token, chat_id, [source_message_id, result_msg_id])
        except Exception as error:
            await send_telegram_text(bot_token, chat_id, f"115 离线提交失败：{error}")
    return True


async def handle_admin_pan123_share_links(
    bot_token: str,
    chat_id: int,
    user_id: int,
    source_message_id: int,
    source_text: str,
    submitter: Dict[str, Any],
    links: List[Dict[str, Any]],
) -> bool:
    session = store.read_session()
    if not session or not session.get("token"):
        await send_telegram_text(bot_token, chat_id, "后端未登录 123 云盘，无法判断分享者 UID 或执行转存。请先重新登录。")
        return True
    profile = session.get("profile") if isinstance(session.get("profile"), dict) else {}
    current_uid = safe_int(profile.get("uid"))
    if not current_uid:
        try:
            profile = await pan123.get_user_info(session)
            session["profile"] = profile
            store.write_session(session)
            current_uid = safe_int(profile.get("uid"))
        except Exception as error:
            await send_telegram_text(bot_token, chat_id, f"获取当前 123 账号 UID 失败：{error}。请重新登录后再试。")
            return True
    if not current_uid:
        await send_telegram_text(bot_token, chat_id, "当前 123 登录会话没有 UID，请重新登录后再试。")
        return True

    submission_links: List[Dict[str, Any]] = []
    missing_passwords: List[Dict[str, Any]] = []
    copy_count = 0
    errors: List[str] = []
    web_link_count = sum(1 for link in links if str(link.get("provider") or "") == "123pan")
    for link in links:
        if str(link.get("provider") or "") != "123pan":
            submission_links.append(link)
            continue
        share_url = str(link.get("cleanUrl") or link.get("url") or "")
        try:
            parsed_share = parse_pan123_share_url(share_url)
            canonical_share_url = f"{parsed_share['origin']}/s/{parsed_share['shareKey']}"
            info = await pan123.get_share_info(canonical_share_url)
            if info.get("expired"):
                raise ValueError("分享已过期")
            share_owner_user_id = safe_int(info.get("userId"))
            if not share_owner_user_id:
                raise ValueError("分享详情未返回 UserID")
            if share_owner_user_id == current_uid:
                submission_links.append(link)
                continue
            password = explicit_pan123_share_password(link, allow_source_fallback=web_link_count == 1)
            if info.get("hasPassword") and not password:
                missing_passwords.append({"link": link, "info": info, "shareUrl": canonical_share_url})
                continue
            await transfer_service.enqueue_pan123_share_copy(
                canonical_share_url,
                password,
                info,
                "telegram",
                chat_id=chat_id,
                user_id=user_id,
                message_id=source_message_id,
            )
            copy_count += 1
        except Exception as error:
            errors.append(f"{share_url}：{error}")

    if len(missing_passwords) == 1:
        pending = missing_passwords[0]
        store.write_value(
            pan123_copy_password_key(user_id),
            {
                "chatId": chat_id,
                "userId": user_id,
                "sourceMessageId": source_message_id,
                "link": pending["link"],
                "info": pending["info"],
                "shareUrl": pending["shareUrl"],
                "expiresAt": time.time() + PAN123_COPY_PASSWORD_TTL_SECONDS,
            },
        )
        await send_telegram_text(bot_token, chat_id, "该第三方 123 分享需要提取码，请在 10 分钟内直接回复 4–8 位提取码。")
    elif len(missing_passwords) > 1:
        await send_telegram_text(bot_token, chat_id, "检测到多个缺少提取码的第三方 123 分享。为避免密码错配，请分别发送每条链接和对应提取码。")

    if submission_links:
        try:
            await submit_submission_links(
                store,
                submission_links,
                "Telegram 投稿",
                chat_id,
                source_text=source_text,
                owner_chat_id=chat_id,
                owner_user_id=user_id,
                source_message_id=source_message_id,
                max_links=3,
                submitter=submitter,
            )
        except Exception as error:
            errors.append(f"投稿处理失败：{error}")
    if errors:
        await send_telegram_text(bot_token, chat_id, "123 分享处理异常：\n" + "\n".join(errors[:3]))
    return bool(submission_links or copy_count or missing_passwords or errors)


async def handle_pending_pan123_copy_password(
    bot_token: str,
    chat_id: int,
    user_id: int,
    source_message_id: int,
    text: str,
) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9]{4,8}", str(text or "").strip()):
        return False
    key = pan123_copy_password_key(user_id)
    pending = store.read_value(key)
    if not isinstance(pending, dict):
        return False
    store.delete_value(key)
    if safe_int(pending.get("chatId")) != chat_id or float(pending.get("expiresAt") or 0) < time.time():
        await send_telegram_text(bot_token, chat_id, "待转存分享的提取码输入已过期，请重新发送分享链接和提取码。")
        return True
    link = pending.get("link") if isinstance(pending.get("link"), dict) else {}
    info = pending.get("info") if isinstance(pending.get("info"), dict) else {}
    try:
        await transfer_service.enqueue_pan123_share_copy(
            str(pending.get("shareUrl") or link.get("cleanUrl") or link.get("url") or ""),
            str(text).strip(),
            info,
            "telegram",
            chat_id=chat_id,
            user_id=user_id,
            message_id=source_message_id,
        )
    except Exception as error:
        await send_telegram_text(bot_token, chat_id, f"123 分享转存入队失败：{error}")
    return True


def pan123_copy_password_key(user_id: int) -> str:
    return f"{PAN123_COPY_PASSWORD_PENDING_PREFIX}{int(user_id or 0)}"


def explicit_pan123_share_password(link: Dict[str, Any], allow_source_fallback: bool) -> str:
    share_url = str(link.get("cleanUrl") or link.get("url") or "")
    try:
        query = parse_qs(urlparse(share_url).query)
        password = str((query.get("pwd") or [""])[0]).strip()
        if password:
            return password
    except (TypeError, ValueError):
        pass
    if allow_source_fallback:
        return str(link.get("password") or "").strip()
    return ""


def telegram_pan115_help_text() -> str:
    return "\n".join([
        "115 搬运机器人使用说明：",
        "/start 搬运后台配置的默认 115 本地盘目录",
        "/start 路径或CID 搬运指定的 115 本地盘目录",
        "/help 查看本说明",
        "/recycle 清空 115 回收站",
        "",
        "直接发送 115 分享链接和提取码：搬运到 123 云盘",
        "直接发送 magnet / ed2k：提交到 115 助手离线下载",
    ])


def format_pan115_action_result(title: str, result: Dict[str, Any]) -> str:
    total = int(result.get("total") or 0)
    success = int(result.get("success") or 0)
    failed = int(result.get("failed") or 0)
    message = f"{title}完成：成功 {success} / 总计 {total}"
    return f"{message}，失败 {failed}" if failed else message


async def send_telegram_transfer_queued_messages(tasks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    config = store.read_submission_config()
    bot_token = str(config.get("botToken") or "").strip()
    if not bot_token:
        return None
    refs: List[Dict[str, Any]] = []
    for task in tasks:
        if str(task.get("source") or "") != "telegram":
            continue
        chat_id = safe_int(task.get("chatId"))
        if not chat_id:
            continue
        is_pan123_copy = str(task.get("kind") or "") == "pan123_share_copy"
        is_local = str(task.get("shareCode") or "").lower().startswith("local:")
        title = str(
            task.get("title")
            or (task.get("sourceText") if is_local else task.get("shareUrl"))
            or "115 任务"
        )
        label = "123 分享转存" if is_pan123_copy else ("115 本地盘搬运" if is_local else "115 分享搬运")
        detail = f"\n目标目录 ID：{task.get('targetDirId') or '0'}" if is_pan123_copy else ""
        sent = await send_telegram_text(
            bot_token,
            chat_id,
            f"{label}已加入队列：{title}{detail}\n任务 ID：{str(task.get('id') or '')[:8]}",
        )
        message_id = telegram_message_id(sent)
        if message_id:
            refs.append({"taskId": task.get("id"), "chatId": chat_id, "messageId": message_id})
    return refs or None


async def send_telegram_transfer_status_message(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(task.get("kind") or "") != "pan123_share_copy" or str(task.get("source") or "") != "telegram":
        return None
    if str(task.get("status") or "") not in {"success", "failed", "partial"}:
        return None
    config = store.read_submission_config()
    bot_token = str(config.get("botToken") or "").strip()
    chat_id = safe_int(task.get("chatId"))
    if not bot_token or not chat_id:
        return None
    title = str(task.get("title") or task.get("shareUrl") or "123 分享")
    remote_id = safe_int(task.get("remoteTaskId"))
    if task.get("status") == "success":
        text = f"✅ 123 分享转存成功：{title}\n目标目录 ID：{task.get('targetDirId') or '0'}"
    else:
        text = f"❌ 123 分享转存失败：{title}\n原因：{task.get('error') or '未知错误'}"
    if remote_id:
        text += f"\n远端任务 ID：{remote_id}"
    sent = await send_telegram_text(bot_token, chat_id, text)
    message_id = telegram_message_id(sent)
    return {"chatId": chat_id, "messageId": message_id} if message_id else None


async def cleanup_telegram_transfer_messages(payload: Dict[str, Any]) -> None:
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if str(task.get("source") or "") != "telegram":
        return
    config = store.read_submission_config()
    bot_token = str(config.get("botToken") or "").strip()
    chat_id = safe_int(payload.get("chatId"))
    message_ids = payload.get("messageIds") if isinstance(payload.get("messageIds"), list) else []
    if bot_token and chat_id and message_ids:
        await delete_telegram_messages(bot_token, chat_id, message_ids)


async def pan115_recycle_cleanup_loop() -> None:
    last_run_key = ""
    while True:
        try:
            submission = store.read_submission_config()
            helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
            if not helper.get("enabled") or not helper.get("dailyRecycleCleanupEnabled"):
                await asyncio.sleep(30)
                continue

            hour, minute = parse_hhmm(str(helper.get("dailyRecycleCleanupTime") or "03:30"))
            zone = safe_zoneinfo(str(helper.get("dailyRecycleCleanupTimeZone") or "Asia/Shanghai"))
            now = datetime.now(zone)
            run_key = now.strftime("%Y-%m-%d %H:%M")
            if now.hour == hour and now.minute == minute and run_key != last_run_key:
                last_run_key = run_key
                with contextlib.suppress(Exception):
                    await empty_115_recycle(helper)
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(60)


def parse_hhmm(value: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2}):(\d{1,2})\s*$", value or "")
    if not match:
        return 3, 30
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour, minute


def safe_zoneinfo(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


class LoginRequest(BaseModel):
    user: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    remember: bool = True


class LoginResponse(BaseModel):
    ok: bool
    user: str
    loginUuid: str
    reused: bool = False
    updatedAt: str
    profile: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    backend: bool = True
    authenticated: bool
    user: str = ""
    loginUuid: str = ""
    updatedAt: str = ""
    profile: Optional[Dict[str, Any]] = None


class SubmissionSubmitRequest(BaseModel):
    text: str = ""
    title: str = ""
    shareUrl: str = ""
    targetUserId: Optional[int] = None


class SubmissionDisplayPreviewRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    sample: Dict[str, Any] = Field(default_factory=dict)


class BotTestRequest(BaseModel):
    token: str = ""


class OwnUserChannelConfigRequest(BaseModel):
    """Configuration submitted from the Telegram Web App for the current user only."""

    channels: List[Dict[str, Any]] = Field(default_factory=list)
    routing: Dict[str, Any] = Field(default_factory=dict)
    channelOwnerUserIds: Optional[List[int]] = None


class DraftSubmitRequest(BaseModel):
    targetUserId: Optional[int] = None


class TextActionRequest(BaseModel):
    text: str = ""
    targetUserId: Optional[int] = None


class TransferConfigRequest(BaseModel):
    enabled: bool = False
    pan123ClientId: str = ""
    pan123ClientSecret: str = ""
    pan115Cookie: str = ""
    pan115Cookies: List[str] = Field(default_factory=list)
    targetDirId: str = "0"
    localPath115: str = ""
    excludeSuffix: str = ""
    excludeCid: str = ""
    delete115AfterSuccess: bool = False
    concurrency: int = 5
    pauseEnabled: bool = True
    pauseTimeZone: str = "Asia/Shanghai"
    pauseStartHour: int = 18
    pauseEndHour: int = 1
    downloadMinIntervalMs: int = 2500
    downloadMaxAttempts: int = 5
    downloadRetryBaseMs: int = 8000
    offlinePollMs: int = 15000
    offlineMaxPolls: int = 240
    progressNotifyIntervalMs: int = 60000


class TransferLocalTaskRequest(BaseModel):
    path115: str = ""
    targetUserId: Optional[int] = None


class Pan115QrSessionRequest(BaseModel):
    device: str = "alipaymini"


def ms_to_iso(ms: int) -> str:
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


class AdminConfig(BaseModel):
    gatewayName: str = "123 Cloud Gateway"
    pan123ClientMode: str = "web"
    pan123OpenApiClientId: str = ""
    pan123OpenApiClientSecret: str = ""
    updatedAt: Optional[str] = None


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "name": "123 Cloud Gateway", "version": "0.1.0"}


@app.get("/api/admin/wallpapers")
async def get_admin_wallpapers() -> Dict[str, Any]:
    try:
        return await wallpaper_service.get()
    except WallpaperUpstreamError as exc:
        logger.warning("Wallpaper upstream unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="壁纸服务暂不可用") from exc


@app.get("/api/admin/config")
async def read_admin_config() -> Dict[str, Any]:
    return normalize_admin_config(store.read_config())


@app.put("/api/admin/config")
async def write_admin_config(config: AdminConfig) -> Dict[str, Any]:
    return {"ok": True, "config": normalize_admin_config(store.write_config(config.dict(exclude_none=True)))}


@app.get("/api/admin/status")
async def admin_status() -> Dict[str, Any]:
    session = store.read_session()
    raw_config = store.read_config()
    config = normalize_admin_config(raw_config)
    submission = store.read_submission_config()
    helper_config = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
    transfer_config = raw_config.get("transfer") if isinstance(raw_config.get("transfer"), dict) else {}
    return {
        "ok": True,
        "gateway": config,
        "capabilities": {
            "openapiConfigured": bool(config.get("pan123OpenApiClientId") and config.get("pan123OpenApiClientSecret")),
            "submissionConfigured": bool(str(submission.get("botToken") or "").strip()),
            "pan115HelperConfigured": bool(helper_config.get("enabled")),
            "transferConfigured": bool(transfer_config.get("enabled")),
        },
        "pan123": await session_payload(session),
    }


@app.get("/api/123/session", response_model=SessionResponse)
async def read_pan123_session() -> SessionResponse:
    session = store.read_session()
    payload = await session_payload(session, refresh_profile=True)
    return SessionResponse(**payload)


@app.post("/api/123/login", response_model=LoginResponse)
async def login_pan123(request: LoginRequest) -> LoginResponse:
    user = request.user.strip()
    password = request.password
    if not user or not password:
        raise HTTPException(status_code=400, detail="请输入账号和密码")

    existing = store.read_session()
    if existing and existing.get("token") and store.credentials_match(existing, user, password):
        profile = existing.get("profile")
        if not isinstance(profile, dict) or not profile:
            profile = await load_profile(existing, user)
            existing["profile"] = profile
            store.write_session(existing)
        return LoginResponse(
            ok=True,
            user=str(existing.get("user") or user),
            loginUuid=str(existing.get("loginUuid") or ""),
            reused=True,
            updatedAt=str(existing.get("updatedAt") or ""),
            profile=profile,
        )

    login_uuid = str(existing.get("loginUuid") or "") if existing else ""
    if not login_uuid:
        login_uuid = uuid.uuid4().hex + uuid.uuid4().hex
    try:
        auth = await pan123.login(user, password, request.remember, login_uuid)
    except Pan123Error as error:
        raise HTTPException(status_code=401, detail=str(error))

    profile = await load_profile({"token": auth["token"], "loginUuid": auth["loginUuid"], "user": user}, user)
    session = store.build_session(user, password, auth["token"], auth["loginUuid"], profile=profile)
    store.write_session(session)
    return LoginResponse(
        ok=True,
        user=user,
        loginUuid=str(session.get("loginUuid") or ""),
        reused=False,
        updatedAt=str(session.get("updatedAt") or ""),
        profile=profile,
    )


@app.post("/api/123/logout")
async def logout_pan123() -> Dict[str, bool]:
    store.clear_session()
    return {"ok": True}


@app.get("/api/submission/config")
async def read_submission_config() -> Dict[str, Any]:
    return {"ok": True, "config": store.read_submission_config()}


@app.put("/api/submission/config")
async def write_submission_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "config": store.write_submission_config(config)}


@app.post("/api/submission/display/preview")
async def preview_submission_display(request: SubmissionDisplayPreviewRequest) -> Dict[str, Any]:
    return {"ok": True, "preview": build_submission_display_preview(request.config, request.sample)}


def telegram_web_app_user_id(init_data: str) -> int:
    """Validate Telegram Web App initData and return its signed Telegram UID.

    The UID is deliberately never accepted from the browser request body.  This
    makes the public Mini App endpoints safe even though the normal desktop
    administration API uses the existing local-session model.
    """
    token = str(store.read_submission_config().get("botToken") or "").strip()
    if not token or not init_data:
        raise HTTPException(status_code=401, detail="请从 Telegram Bot 打开频道配置卡片")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    hashes = [value for key, value in pairs if key == "hash"]
    if len(hashes) != 1 or not hashes[0]:
        raise HTTPException(status_code=401, detail="Telegram 身份信息不完整")
    data_pairs = [(key, value) for key, value in pairs if key != "hash"]
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_pairs))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, hashes[0]):
        raise HTTPException(status_code=401, detail="Telegram 身份校验失败")

    values = dict(data_pairs)
    auth_date = safe_int(values.get("auth_date"))
    now = int(time.time())
    if not auth_date or auth_date > now + 300 or now - auth_date > 86_400:
        raise HTTPException(status_code=401, detail="Telegram 身份信息已过期，请关闭后重新打开卡片")
    try:
        user = json.loads(values.get("user") or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=401, detail="Telegram 用户信息无效") from error
    user_id = safe_int(user.get("id") if isinstance(user, dict) else 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Telegram 用户信息无效")
    return user_id


def require_telegram_channel_owner(init_data: str) -> int:
    user_id = telegram_web_app_user_id(init_data)
    config = store.read_submission_config()
    # A Bot administrator is allowed into the card as the bootstrap path for
    # adding the first channel owner.  The card still reads and writes only
    # that administrator's own channel configuration; it never grants access
    # to another user's channels.
    if not (
        telegram_channel_owner_allowed(config, user_id, store)
        or telegram_admin_allowed(config, user_id)
    ):
        raise HTTPException(status_code=403, detail="你没有频道管理权限；获授权用户只能投稿")
    return user_id


@app.get("/api/submission/my-channel-config")
async def read_my_channel_config(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    user_id = require_telegram_channel_owner(x_telegram_init_data)
    config = store.read_submission_config()
    own = store.read_user_channel_config(user_id)
    if telegram_admin_allowed(config, user_id):
        own["canManageChannelOwners"] = True
        own["channelOwnerUserIds"] = config.get("channelOwnerUserIds") or []
    return {"ok": True, "config": own}


@app.put("/api/submission/my-channel-config")
async def write_my_channel_config(
    payload: OwnUserChannelConfigRequest,
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    user_id = require_telegram_channel_owner(x_telegram_init_data)
    config = store.read_submission_config()
    if payload.channelOwnerUserIds is not None:
        if not telegram_admin_allowed(config, user_id):
            raise HTTPException(status_code=403, detail="只有 Bot 管理员可以授权新的频道所有者")
        store.write_submission_config({"channelOwnerUserIds": payload.channelOwnerUserIds})
    try:
        saved = store.write_user_channel_config(user_id, {"channels": payload.channels, "routing": payload.routing})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    current = store.read_submission_config()
    if telegram_admin_allowed(current, user_id):
        saved["canManageChannelOwners"] = True
        saved["channelOwnerUserIds"] = current.get("channelOwnerUserIds") or []
    return {"ok": True, "config": saved}


@app.delete("/api/submission/my-channel-config")
async def delete_my_channel_config(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    user_id = require_telegram_channel_owner(x_telegram_init_data)
    deleted = store.delete_user_channel_config(user_id)
    return {"ok": True, "deleted": deleted}


@app.get("/api/submission/status")
async def read_submission_status() -> Dict[str, Any]:
    config = store.read_submission_config()
    bot_token = str(config.get("botToken") or "").strip()
    allowed_user_ids = [int(value) for value in config.get("telegramAdminUserIds") or [] if str(value).strip().isdigit() and int(value) > 0]
    user_channel_configs = store.list_users_with_channel_configs()
    return {
        "ok": True,
        "botConfigured": bool(bot_token),
        "telegramApiConfigured": bool(str((config.get("telegramApi") or {}).get("apiId") or "").strip() and str((config.get("telegramApi") or {}).get("apiHash") or "").strip() and str((config.get("telegramApi") or {}).get("session") or "").strip()),
        "tmdbConfigured": bool(str(config.get("tmdbToken") or "").strip()),
        "allowedUserCount": len(allowed_user_ids),
        "channelCount": 0,
        "userChannelCount": len(user_channel_configs),
        "draftCount": len(list_submission_drafts(store, 200)),
        "shareName": str((config.get("templates") or {}).get("shareName") or "123"),
        "updatedAt": str(config.get("updatedAt") or ""),
    }


@app.post("/api/submission/test/bot")
async def test_submission_bot(request: BotTestRequest) -> Dict[str, Any]:
    token = str(request.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="请输入 Bot Token")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code >= 400 or not data.get("ok", True):
        raise HTTPException(status_code=502, detail=str(data.get("description") or f"Telegram {response.status_code}"))
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    return {"ok": True, "message": f"Bot 已连接：{result.get('username') or result.get('first_name') or 'unknown'}", "result": result}


@app.post("/api/submission/submit")
async def submit_submission(request: SubmissionSubmitRequest) -> Dict[str, Any]:
    text = str(request.text or "").strip()
    if not text:
        text = "\n".join(
            part
            for part in [
                f"🎬：{request.title.strip()}" if request.title.strip() else "",
                f"🔗：{request.shareUrl.strip()}" if request.shareUrl.strip() else "",
            ]
            if part
        ).strip()
    try:
        result = await submit_submission_text(store, text, request.title or "投稿", request.targetUserId)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    return {"ok": True, **result}


@app.get("/api/submission/drafts")
async def read_submission_drafts(limit: int = Query(100, ge=1, le=200)) -> Dict[str, Any]:
    drafts = list_submission_drafts(store, limit)
    return {"ok": True, "drafts": drafts, "count": len(drafts)}


@app.delete("/api/submission/drafts")
async def remove_submission_drafts() -> Dict[str, Any]:
    clear_submission_drafts(store)
    return {"ok": True}


@app.delete("/api/submission/drafts/{draft_id}")
async def remove_submission_draft(draft_id: str) -> Dict[str, Any]:
    delete_submission_draft(store, draft_id)
    return {"ok": True}


@app.post("/api/submission/drafts/{draft_id}/submit")
async def submit_submission_draft(draft_id: str, request: DraftSubmitRequest) -> Dict[str, Any]:
    try:
        result = await submit_existing_draft(store, draft_id, request.targetUserId)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    return {"ok": True, **result}


@app.get("/api/pan115-cookie/devices")
async def read_pan115_cookie_devices() -> Dict[str, Any]:
    return {"ok": True, "devices": [{"id": key, "label": value} for key, value in PAN115_QR_DEVICES.items()]}


@app.post("/api/pan115-cookie/sessions")
async def create_pan115_cookie_session(request: Pan115QrSessionRequest) -> Dict[str, Any]:
    try:
        session = await create_pan115_qr_session(request.device)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    return {"ok": True, **session}


@app.get("/api/pan115-cookie/sessions/{session_id}/status")
async def read_pan115_cookie_session_status(session_id: str) -> Dict[str, Any]:
    session = get_pan115_qr_session(session_id)
    if not session:
        raise HTTPException(status_code=410, detail="二维码会话已过期，请重新生成")
    try:
        status = await get_pan115_qr_status(session)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    if status in (-1, -2):
        pan115_qr_sessions.pop(session_id, None)
    return {
        "ok": True,
        "status": status,
        "statusText": pan115_status_text(status),
        "expiresAt": ms_to_iso(int(session.get("expiresAt") or 0)),
        "expiresInMs": max(0, int(session.get("expiresAt") or 0) - int(time.time() * 1000)),
    }


@app.post("/api/pan115-cookie/sessions/{session_id}/confirm")
async def confirm_pan115_cookie_session(session_id: str) -> Dict[str, Any]:
    session = get_pan115_qr_session(session_id)
    if not session:
        raise HTTPException(status_code=410, detail="二维码会话已过期，请重新生成")
    try:
        confirmed = await confirm_pan115_qr_login(session)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    pan115_qr_sessions.pop(session_id, None)
    return {"ok": True, **confirmed}


@app.get("/api/pan115-helper/status")
async def read_pan115_helper_status() -> Dict[str, Any]:
    submission = store.read_submission_config()
    helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
    if not helper.get("enabled"):
        return {"ok": True, "enabled": False, "message": "115 助手未启用"}
    try:
        status = await helper_status(helper)
        return {"enabled": True, **status}
    except Exception as error:
        return {"ok": True, "enabled": True, "message": str(error)}


@app.post("/api/pan115-helper/offline")
async def submit_pan115_helper_offline(request: TextActionRequest) -> Dict[str, Any]:
    submission = store.read_submission_config()
    helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
    try:
        result = await submit_115_offline_from_text(helper, request.text)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {**result, "actionOk": bool(result.get("ok")), "ok": True}


@app.post("/api/pan115-helper/recycle/empty")
async def empty_pan115_helper_recycle() -> Dict[str, Any]:
    submission = store.read_submission_config()
    helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
    try:
        result = await empty_115_recycle(helper)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {**result, "actionOk": bool(result.get("ok")), "ok": True}


@app.get("/api/transfer/config")
async def read_transfer_config() -> Dict[str, Any]:
    return normalize_transfer_config(store.read_config())


@app.put("/api/transfer/config")
async def write_transfer_config(config: TransferConfigRequest) -> Dict[str, Any]:
    payload = normalize_transfer_config(config.dict())
    admin_update: Dict[str, Any] = {"transfer": payload}
    if payload.get("pan123ClientId"):
        admin_update["pan123OpenApiClientId"] = payload["pan123ClientId"]
    if payload.get("pan123ClientSecret"):
        admin_update["pan123OpenApiClientSecret"] = payload["pan123ClientSecret"]
    saved = store.write_config(admin_update)
    transfer_service._remember_config(saved.get("transfer") if isinstance(saved.get("transfer"), dict) else {})
    transfer_service.kick()
    return {"ok": True, "config": normalize_transfer_config(saved)}


@app.get("/api/transfer/tasks")
async def read_transfer_tasks(limit: int = Query(100, ge=1, le=500)) -> List[Dict[str, Any]]:
    return store.list_transfer_tasks(limit)


@app.post("/api/transfer/tasks")
async def create_transfer_tasks(request: TextActionRequest) -> Dict[str, Any]:
    try:
        tasks = await transfer_service.enqueue_from_text(request.text, "admin")
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"ok": True, "tasks": tasks}


@app.post("/api/transfer/local-tasks")
async def create_local_transfer_task(request: TransferLocalTaskRequest) -> Dict[str, Any]:
    try:
        config = normalize_transfer_config(store.read_config())
        path_115 = str(request.path115 or config.get("localPath115") or "").strip()
        task = await transfer_service.enqueue_local_path(path_115, "admin-local")
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"ok": True, "task": task}


@app.post("/api/transfer/kick")
async def kick_transfer_queue() -> Dict[str, Any]:
    transfer_service.kick()
    return {"ok": True}


@app.post("/api/transfer/tasks/{task_id}/requeue")
async def requeue_transfer_task(task_id: str) -> Dict[str, Any]:
    try:
        task = await transfer_service.requeue_task(task_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"ok": True, "task": task}


@app.delete("/api/transfer/tasks/{task_id}")
async def delete_transfer_task(task_id: str) -> Dict[str, Any]:
    try:
        await transfer_service.delete_task(task_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"ok": True}


@app.get("/api/transfer/offline")
async def read_transfer_offline_tasks() -> Dict[str, Any]:
    try:
        client = await transfer_service.create_status_pan123_client()
        return {"ok": True, "tasks": await client.list_offline_tasks(), "canDelete": False}
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.delete("/api/transfer/offline/{task_id}")
async def delete_transfer_offline_task(task_id: int) -> Dict[str, Any]:
    try:
        client = await transfer_service.create_status_pan123_client()
        await client.delete_offline_tasks([task_id])
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"ok": True}


@app.delete("/api/transfer/offline/completed")
async def delete_completed_transfer_offline_tasks() -> Dict[str, Any]:
    return {"ok": True, "deleted": 0, "message": "123 OpenAPI 暂不支持列出并删除已完成离线任务"}


async def session_payload(session: Optional[Dict[str, Any]], refresh_profile: bool = False) -> Dict[str, Any]:
    if not session or not session.get("token"):
        return {"backend": True, "authenticated": False, "user": "", "loginUuid": "", "updatedAt": "", "profile": None}
    profile = session.get("profile")
    if refresh_profile and (not isinstance(profile, dict) or not profile):
        profile = await load_profile(session, str(session.get("user") or ""))
        session = dict(session)
        session["profile"] = profile
        store.write_session(session)
    return {
        "backend": True,
        "authenticated": True,
        "user": str(session.get("user") or ""),
        "loginUuid": str(session.get("loginUuid") or ""),
        "updatedAt": str(session.get("updatedAt") or ""),
        "profile": profile if isinstance(profile, dict) else None,
    }


async def load_profile(session: Dict[str, Any], fallback_user: str) -> Dict[str, Any]:
    try:
        return await pan123.get_user_info(session)
    except Exception:
        return {
            "uid": None,
            "nickname": fallback_user or str(session.get("user") or ""),
            "headImage": "",
            "passport": fallback_user or str(session.get("user") or ""),
            "mail": "",
            "spaceUsed": None,
            "spacePermanent": None,
            "spaceTemp": None,
            "spaceTempExpr": "",
            "vip": None,
            "directTraffic": None,
            "isHideUID": None,
            "httpsCount": None,
        }


def normalize_admin_config(config: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(config.get("pan123ClientMode") or "web").strip().lower()
    if mode not in {"web", "openapi"}:
        mode = "web"
    return {
        "gatewayName": str(config.get("gatewayName") or "123 Cloud Gateway").strip() or "123 Cloud Gateway",
        "pan123ClientMode": mode,
        "pan123OpenApiClientId": str(config.get("pan123OpenApiClientId") or "").strip(),
        "pan123OpenApiClientSecret": str(config.get("pan123OpenApiClientSecret") or "").strip(),
        "updatedAt": str(config.get("updatedAt") or ""),
    }


def normalize_transfer_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("transfer") if isinstance(config.get("transfer"), dict) else config
    pan115_cookies = raw.get("pan115Cookies") if isinstance(raw.get("pan115Cookies"), list) else []
    pan115_cookies = [str(item or "").strip() for item in pan115_cookies if str(item or "").strip()]
    pan115_cookie = str(raw.get("pan115Cookie") or "\n".join(pan115_cookies) or "").strip()
    if pan115_cookie and not pan115_cookies:
        pan115_cookies = [line.strip() for line in re.split(r"\n\s*\n|[\r\n]+", pan115_cookie) if line.strip()]
    return {
        "enabled": bool(raw.get("enabled")),
        "pan123ClientId": str(raw.get("pan123ClientId") or config.get("pan123OpenApiClientId") or "").strip(),
        "pan123ClientSecret": str(raw.get("pan123ClientSecret") or config.get("pan123OpenApiClientSecret") or "").strip(),
        "pan115Cookie": "\n".join(pan115_cookies) if pan115_cookies else pan115_cookie,
        "pan115Cookies": pan115_cookies,
        "targetDirId": str(raw.get("targetDirId") or "0").strip() or "0",
        "localPath115": str(raw.get("localPath115") or raw.get("path115") or raw.get("path_115") or "").strip(),
        "excludeSuffix": str(raw.get("excludeSuffix") or raw.get("exclude_suffix") or "").strip(),
        "excludeCid": str(raw.get("excludeCid") or raw.get("exclude_cid") or "").strip(),
        "delete115AfterSuccess": bool(raw.get("delete115AfterSuccess") or raw.get("delete_115")),
        "concurrency": clamp_int(raw.get("concurrency"), 1, 5, 5),
        "pauseEnabled": raw.get("pauseEnabled") is not False,
        "pauseTimeZone": str(raw.get("pauseTimeZone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
        "pauseStartHour": clamp_int(raw.get("pauseStartHour"), 0, 23, 18),
        "pauseEndHour": clamp_int(raw.get("pauseEndHour"), 0, 23, 1),
        "downloadMinIntervalMs": clamp_int(raw.get("downloadMinIntervalMs"), 0, 60000, 2500),
        "downloadMaxAttempts": clamp_int(raw.get("downloadMaxAttempts"), 1, 20, 5),
        "downloadRetryBaseMs": clamp_int(raw.get("downloadRetryBaseMs"), 1000, 120000, 8000),
        "offlinePollMs": clamp_int(raw.get("offlinePollMs"), 3000, 120000, 15000),
        "offlineMaxPolls": clamp_int(raw.get("offlineMaxPolls"), 1, 1000, 240),
        "progressNotifyIntervalMs": clamp_int(raw.get("progressNotifyIntervalMs"), 0, 600000, 60000),
    }


def safe_int(value: Any) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


if ADMIN_WEB_DIR.exists():
    app.mount("/admin/assets", StaticFiles(directory=str(ADMIN_WEB_DIR / "assets")), name="admin-assets")

    def admin_index_response() -> FileResponse:
        # The HTML shell keeps the Vite asset manifest.  Telegram's in-app
        # browser otherwise holds on to an old shell and keeps loading old
        # JavaScript after a deployment, which looks like a successful save
        # disappearing after a refresh.  Assets themselves remain hashed and
        # may be cached normally.
        return FileResponse(
            str(ADMIN_WEB_DIR / "index.html"),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/")
    async def admin_index() -> FileResponse:
        return admin_index_response()

    @app.get("/admin")
    async def admin_page() -> FileResponse:
        return admin_index_response()

    @app.get("/admin/{page_path:path}")
    async def admin_nested_page(page_path: str) -> FileResponse:
        return admin_index_response()
