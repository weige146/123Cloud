from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 统一日志：控制台 + 落盘轮转 + 内存环形缓冲（GET /api/logs 读取）
# 必须在导入业务模块前初始化，保证第三方库降噪与应用日志格式一致
from .logsetup import recent_logs, setup_logging

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT_DIR / "data")
setup_logging(DATA_DIR)

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
from .session_store import SessionStore, positive_user_ids
from .submission import (
    build_submission_display_preview,
    clear_submission_drafts,
    close_telegram_client,
    delete_submission_draft,
    delete_telegram_messages,
    extract_submission_links,
    FASTLINK_RE,
    find_pending_submission_draft,
    handle_pending_submission_input,
    handle_submission_callback,
    list_submission_drafts,
    parse_fastlink,
    send_telegram_text,
    start_telegram_client,
    strip_fastlink_seed_ext,
    submit_existing_draft,
    submit_submission_links,
    telegram_admin_allowed,
    telegram_message_id,
    telegram_message_text,
    telegram_user_allowed,
)
from .transfer_service import PAN115_ACCOUNT_COOLDOWN_MS, TransferService


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
logger = logging.getLogger(__name__)
pan115_recycle_cleanup_task: Optional[asyncio.Task[None]] = None
telegram_callback_polling_task: Optional[asyncio.Task[None]] = None
PAN123_COPY_PASSWORD_PENDING_PREFIX = "telegram_pan123_copy_password:"
PAN123_COPY_PASSWORD_TTL_SECONDS = 600
TELEGRAM_BOT_COMMANDS = [
    {"command": "start", "description": "启动本地盘搬运"},
    {"command": "recycle", "description": "删除回收站"},
]


@contextlib.asynccontextmanager
async def app_lifespan(_app: FastAPI):
    await start_background_tasks()
    try:
        yield
    finally:
        await stop_background_tasks()


app = FastAPI(title="123 Cloud Gateway", version="1.0.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def start_background_tasks() -> None:
    global pan115_recycle_cleanup_task, telegram_callback_polling_task
    await start_telegram_client()
    transfer_service.set_queued_notifier(send_telegram_transfer_queued_messages)
    transfer_service.set_notifier(send_telegram_transfer_status_message)
    transfer_service.set_cookie_notifier(send_telegram_account_expired_message)
    transfer_service.set_cleanup_notifier(cleanup_telegram_transfer_messages)
    if pan115_recycle_cleanup_task and not pan115_recycle_cleanup_task.done():
        pass
    else:
        pan115_recycle_cleanup_task = asyncio.create_task(pan115_recycle_cleanup_loop())
    if telegram_callback_polling_task and not telegram_callback_polling_task.done():
        pass
    else:
        telegram_callback_polling_task = asyncio.create_task(telegram_callback_polling_loop())
    await transfer_service.init()


async def stop_background_tasks() -> None:
    for task in (pan115_recycle_cleanup_task, telegram_callback_polling_task):
        if not task or task.done():
            continue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await transfer_service.close()
    await pan123.close()
    await close_telegram_client()


async def telegram_callback_polling_loop() -> None:
    """最小化 Telegram 轮询：只服务草稿预览按钮。

    - 处理预览消息上的按钮回调（发布到频道 / 修改大小 / 修改备注 /
      更改识别 / 指定频道），以及按钮触发的"下一条消息"输入
    - 不接收任何投稿文本：投稿统一走油猴脚本 / 后台接口提交
    """
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
                    with contextlib.suppress(Exception):
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
                            await handle_telegram_button_update(update, bot_token, config)
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
    source_message_id = safe_int(message.get("message_id"))

    # 秒传 JSON / .123fastlink 文件：下载后提取秒传链接生成投稿草稿。
    document = message.get("document") if isinstance(message.get("document"), dict) else None
    if document:
        return await handle_admin_fastlink_document(
            bot_token,
            chat_id,
            user_id,
            source_message_id,
            document,
            user,
            text,
        )

    if not text:
        return False

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

    # 秒传链接（123FLCPV2…）没有网页分享地址可判断归属，直接走投稿草稿。
    fastlink_links = [link for link in submission_links if str(link.get("provider") or "") == "123fastlink"]
    if fastlink_links:
        return await handle_admin_fastlink_submission(bot_token, chat_id, user_id, source_message_id, text, user)

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


FASTLINK_DOCUMENT_SUFFIX_RE = re.compile(r"\.(?:json|123fastlink|123share|txt)$", re.I)
FASTLINK_DOCUMENT_MAX_BYTES = 20 * 1024 * 1024


def build_fastlink_links_from_json(content: str) -> Tuple[List[str], List[str]]:
    """项目标准 JSON（files[].etag/size/path）转 123FLCPV2 文本链接。

    与油猴脚本 buildFastlinkText 同构：`123FLCPV2$<commonPath>%<etag#size#path>$…`。
    返回 (链接列表, 文件名列表)；内容不是秒传 JSON 时返回空。
    """
    try:
        data = json.loads(str(content or ""))
    except (ValueError, TypeError):
        return [], []
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        return [], []
    common_path = str(data.get("commonPath") or "")
    entries: List[str] = []
    names: List[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        etag = str(item.get("etag") or "").strip()
        size = safe_int(item.get("size"))
        path = str(item.get("path") or item.get("fileName") or "").strip()
        if not etag or not path or size < 0:
            continue
        entries.append(f"{etag}#{size}#{path}")
        names.append(path.rsplit("/", 1)[-1] or path)
    if not entries:
        return [], []
    return [f"123FLCPV2${common_path}%{'$'.join(entries)}"], names


async def handle_admin_fastlink_submission(
    bot_token: str,
    chat_id: int,
    user_id: int,
    source_message_id: int,
    text: str,
    submitter: Dict[str, Any],
    links: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """秒传文本/秒传 JSON 统一走投稿草稿。

    与分享链接一样把 sourceMessageId 记进草稿：发布成功后
    cleanup_submission_draft_messages 会删掉发来的消息和预览。
    """
    if not links:
        links = [
            link
            for link in extract_submission_links(text)
            if str(link.get("provider") or "") == "123fastlink"
        ]
    if not links:
        await send_telegram_text(bot_token, chat_id, "秒传投稿处理失败：没有可以处理的秒传链接")
        return True
    try:
        await submit_submission_links(
            store,
            links,
            "Telegram 投稿",
            chat_id,
            source_text=text,
            owner_chat_id=chat_id,
            owner_user_id=user_id,
            source_message_id=source_message_id,
            max_links=10,
            submitter=submitter,
        )
    except Exception as error:
        await send_telegram_text(bot_token, chat_id, f"秒传投稿处理失败：{error}")
    return True


async def handle_admin_fastlink_document(
    bot_token: str,
    chat_id: int,
    user_id: int,
    source_message_id: int,
    document: Dict[str, Any],
    submitter: Dict[str, Any],
    caption: str = "",
) -> bool:
    file_name = str(document.get("file_name") or "")
    if not FASTLINK_DOCUMENT_SUFFIX_RE.search(file_name):
        return False
    try:
        content = await download_telegram_document_text(bot_token, document)
    except Exception as error:
        await send_telegram_text(bot_token, chat_id, f"读取秒传文件失败：{error}")
        return True
    links, file_names = build_fastlink_links_from_json(content)
    if not links:
        links = [match.group(0) for match in FASTLINK_RE.finditer(content)]
    if not links and caption:
        links = [match.group(0) for match in FASTLINK_RE.finditer(caption)]
    if not links:
        await send_telegram_text(bot_token, chat_id, f"秒传文件 {file_name or '(未命名)'} 里没有识别到秒传链接或秒传 JSON 文件记录。")
        return True
    if not file_names:
        for link in links:
            parsed = parse_fastlink(link)
            name = str(parsed.get("fileName") or "").strip()
            if name and name.lower() not in {existing.lower() for existing in file_names}:
                file_names.append(name)
    title = strip_fastlink_seed_ext(file_name) or "秒传投稿"
    context_lines = [f"🎬：{title}", f"🔗：{links[0]}"]
    context_lines.extend(f"📄：{name}" for name in file_names[:100])
    if len(links) > 1:
        context_lines.append(f"还有 {len(links) - 1} 个秒传链接")
    text = "\n".join(context_lines)
    if caption.strip():
        text = f"{caption.strip()}\n{text}"
    # 原始 JSON 挂到草稿：预览不带文件，发布到频道时随消息附上秒传 JSON。
    link_dicts = [
        {
            "url": link,
            "cleanUrl": link,
            "provider": "123fastlink",
            "title": title if index == 0 else "",
            "sourceText": text,
            "documents": [
                {
                    "type": "fastlink_json",
                    "fileName": file_name or "123FastLink_Export.123fastlink.json",
                    "mimeType": "application/json",
                    "content": content,
                }
            ],
        }
        for index, link in enumerate(links)
    ]
    return await handle_admin_fastlink_submission(
        bot_token, chat_id, user_id, source_message_id, text, submitter, links=link_dicts
    )


async def download_telegram_document_text(bot_token: str, document: Dict[str, Any]) -> str:
    file_id = str(document.get("file_id") or "")
    file_size = safe_int(document.get("file_size"))
    if file_size > FASTLINK_DOCUMENT_MAX_BYTES:
        raise ValueError("文件超过 20MB，请先拆分秒传 JSON")
    async with httpx.AsyncClient(timeout=60.0) as client:
        info_response = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id},
        )
        info = info_response.json() if info_response.headers.get("content-type", "").startswith("application/json") else {}
        if info_response.status_code >= 400 or not info.get("ok", True):
            raise ValueError(str(info.get("description") or f"getFile 失败（HTTP {info_response.status_code}）"))
        file_path = str((info.get("result") or {}).get("file_path") or "")
        if not file_path:
            raise ValueError("getFile 未返回文件路径")
        content_response = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
        if content_response.status_code >= 400:
            raise ValueError(f"下载秒传文件失败（HTTP {content_response.status_code}）")
        return content_response.content.decode("utf-8", errors="replace")


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

def telegram_pan115_help_text() -> str:
    return "\n".join([
        "115 搬运机器人使用说明：",
        "/start 启动本地盘搬运（后台配置的默认 115 本地盘目录）",
        "/start 路径或CID 搬运指定的 115 本地盘目录",
        "/recycle 删除回收站（清空 115 回收站）",
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


async def handle_telegram_button_update(update: Dict[str, Any], bot_token: str, config: Dict[str, Any]) -> None:
    callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
    if callback:
        await handle_submission_callback(store, bot_token, config, callback)
        return
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    if not message:
        return
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if str(chat.get("type") or "") != "private":
        return
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    user_id = safe_int(user.get("id"))
    chat_id = safe_int(chat.get("id"))
    if not user_id:
        return

    # 1) 点了"修改"按钮后的下一条消息：作为草稿编辑输入接收
    draft = find_pending_submission_draft(store, chat_id, user_id)
    if draft:
        text = telegram_message_text(message).strip()
        if text:
            await handle_pending_submission_input(store, bot_token, config, draft, text, safe_int(message.get("message_id")))
        return

    # 2) 授权管理员发文本：115 链接→搬运、磁力→115 离线、123 链接→分流（与旧版一致）
    if telegram_admin_allowed(config, user_id):
        await handle_transfer_telegram_update(update, bot_token, config)


async def _transfer_admin_chat_ids() -> List[int]:
    """搬运通知的接收人：telegramAdminUserIds（兼容旧的 allowedUserIds）。"""
    config = store.read_submission_config()
    admin_ids = config.get("telegramAdminUserIds")
    admin_list = [safe_int(value) for value in admin_ids] if isinstance(admin_ids, list) else []
    legacy = config.get("allowedUserIds")
    if legacy and isinstance(legacy, list):
        admin_list.extend(safe_int(value) for value in legacy)
    return list(dict.fromkeys([chat_id for chat_id in admin_list if chat_id]))


def _transfer_bot_token() -> str:
    return str(store.read_submission_config().get("botToken") or "").strip()


async def send_telegram_transfer_queued_messages(tasks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """入队通知广播给所有管理员；返回第一个管理员聊天的消息引用供成功后清理。"""
    bot_token = _transfer_bot_token()
    chat_ids = await _transfer_admin_chat_ids()
    if not bot_token or not chat_ids:
        return None
    track_chat_id = chat_ids[0]
    refs: List[Dict[str, Any]] = []
    for task in tasks:
        is_pan123_copy = str(task.get("kind") or "") == "pan123_share_copy"
        is_local = str(task.get("shareCode") or "").lower().startswith("local:")
        title = str(
            task.get("title")
            or (task.get("sourceText") if is_local else task.get("shareUrl"))
            or "115 任务"
        )
        label = "123 分享转存" if is_pan123_copy else ("115 本地盘搬运" if is_local else "115 分享搬运")
        detail = f"\n目标目录 ID：{task.get('targetDirId') or '0'}" if is_pan123_copy else ""
        text = f"📥 {label}已加入队列：{title}{detail}\n任务 ID：{str(task.get('id') or '')[:8]}"
        for chat_id in chat_ids:
            with contextlib.suppress(Exception):
                sent = await send_telegram_text(bot_token, chat_id, text)
                message_id = telegram_message_id(sent)
                if chat_id == track_chat_id and message_id:
                    refs.append({"taskId": task.get("id"), "chatId": chat_id, "messageId": message_id})
    return refs or None


async def send_telegram_transfer_status_message(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    kind = str(task.get("kind") or "")
    if kind not in {"pan123_share_copy", "pan115_share"}:
        return None
    status = str(task.get("status") or "")
    if status not in {"success", "failed", "partial"}:
        return None
    bot_token = _transfer_bot_token()
    chat_ids = await _transfer_admin_chat_ids()
    if not bot_token or not chat_ids:
        return None
    is_pan123_copy = kind == "pan123_share_copy"
    is_local = str(task.get("shareCode") or "").lower().startswith("local:")
    if is_pan123_copy:
        title = str(task.get("title") or task.get("shareUrl") or "123 分享")
        label = "123 分享转存"
    else:
        title = str(task.get("title") or task.get("shareUrl") or ("115 本地盘" if is_local else "115 分享"))
        label = "115 本地盘搬运" if is_local else "115 分享搬运"
    files = task.get("files") or []
    success = len([f for f in files if str(f.get("status")) in {"success", "skipped"}])
    failed_files = [f for f in files if str(f.get("status")) == "failed"]
    failed_count = len(failed_files)
    if status == "success":
        icon = "✅"
        summary = f"成功 {success}，跳过/失败 0"
    elif status == "partial":
        icon = "⚠️"
        summary = f"成功 {success}，失败 {failed_count}"
    else:
        icon = "❌"
        summary = f"失败 {max(1, failed_count)} 个文件"
    text = f"{icon} {label}{'部分失败' if status == 'partial' else '完成' if status == 'success' else '失败'}：{title}\n{summary}"
    if failed_files:
        reason = str(failed_files[0].get("error") or task.get("error") or "未知错误")[:200]
        text += f"\n失败原因：{reason}"
    if is_pan123_copy and safe_int(task.get("remoteTaskId")):
        text += f"\n远端任务 ID：{safe_int(task.get('remoteTaskId'))}"
    first_ref: Optional[Dict[str, Any]] = None
    for chat_id in chat_ids:
        with contextlib.suppress(Exception):
            sent = await send_telegram_text(bot_token, chat_id, text)
            message_id = telegram_message_id(sent)
            if first_ref is None and message_id:
                first_ref = {"chatId": chat_id, "messageId": message_id}
    return first_ref


async def cleanup_telegram_transfer_messages(payload: Dict[str, Any]) -> None:
    """搬运结束后删除 Telegram 里的排队/进度消息和用户的链接消息。"""
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    bot_token = _transfer_bot_token()
    chat_id = safe_int(payload.get("chatId"))
    message_ids = payload.get("messageIds") if isinstance(payload.get("messageIds"), list) else []
    if bot_token and chat_id and message_ids:
        await delete_telegram_messages(bot_token, chat_id, message_ids)


async def send_telegram_account_expired_message(payload: Dict[str, Any]) -> None:
    """115 Cookie 失效告警：发给 telegramAdminUserIds 管理员。"""
    if not isinstance(payload, dict):
        return
    bot_token = _transfer_bot_token()
    if not bot_token:
        return
    chat_ids = await _transfer_admin_chat_ids()
    if not chat_ids:
        return
    account = str(payload.get("account") or "未命名账号")
    reason = str(payload.get("reason") or "未知原因")[:200]
    minutes = safe_int(payload.get("cooldownMinutes")) or 30
    text = (
        f"⚠️ 115 Cookie 已失效：{account}\n"
        f"原因：{reason}\n"
        f"已暂时停用 {minutes} 分钟并改用其他账号，请尽快更新 Cookie。"
    )
    for chat_id in chat_ids:
        with contextlib.suppress(Exception):
            await send_telegram_text(bot_token, chat_id, text)


async def route_submission_text(
    text: str,
    source_label: str,
    submitter: Optional[Dict[str, Any]] = None,
    max_links: int = 10,
) -> Dict[str, Any]:
    """统一分流入口（油猴脚本、后台提交共用）：拿到分享文本后自动分类处理。

    - 第三方 123 分享 → 创建 123 转存任务（搬运到自己网盘）
    - 自己的 123 分享 / 秒传链接 → 生成投稿草稿（后台"投稿草稿"里发布）
    - 115 分享链接（带提取码）→ 创建 115 搬运任务
    - magnet / ed2k → 提交到 115 助手离线下载

    返回的 accepted 是受理条数；失败原因在 failures 列表里，详情看后台日志。
    """
    failures: List[str] = []
    transfers = 0
    offline_count = 0
    drafts = 0

    links = extract_submission_links(text)[:max_links]
    web_link_count = sum(1 for link in links if str(link.get("provider") or "") == "123pan")

    submission_links: List[Dict[str, Any]] = []
    if links:
        session = store.read_session()
        current_uid = 0
        if session and session.get("token"):
            profile = session.get("profile") if isinstance(session.get("profile"), dict) else {}
            current_uid = safe_int(profile.get("uid"))
            if not current_uid:
                try:
                    profile = await pan123.get_user_info(session)
                    session["profile"] = profile
                    store.write_session(session)
                    current_uid = safe_int(profile.get("uid"))
                except Exception as error:
                    logger.warning(f"获取 123 账号 UID 失败：{error}")
        for link in links:
            provider = str(link.get("provider") or "")
            if provider != "123pan":
                submission_links.append(link)
                continue
            share_url = str(link.get("cleanUrl") or link.get("url") or "")
            try:
                if not current_uid:
                    raise RuntimeError("后端未登录 123 云盘，无法判断分享归属；请先登录")
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
                    raise ValueError("分享需要提取码，链接里没有找到")
                await transfer_service.enqueue_pan123_share_copy(canonical_share_url, password, info, source_label)
                transfers += 1
            except Exception as error:
                failures.append(f"{share_url}：{error}")

    share_links = extract_115_links(text)
    offline_links = extract_pan115_offline_links(text)
    if share_links:
        try:
            tasks = await transfer_service.enqueue_from_text(text, source_label)
            transfers += len(tasks)
        except Exception as error:
            failures.append(f"115 分享搬运：{error}")
    if offline_links:
        submission = store.read_submission_config()
        helper = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
        try:
            offline_result = await submit_115_offline_from_text(helper, text)
            offline_count = int(offline_result.get("success") or 0)
            if offline_count <= 0:
                raise RuntimeError("115 助手离线提交了 0 条")
        except Exception as error:
            failures.append(f"115 离线提交：{error}")

    if submission_links:
        try:
            draft_result = await submit_submission_links(
                store,
                submission_links,
                source_label,
                source_text=text,
                max_links=max_links,
                submitter=submitter,
            )
            drafts = int(draft_result.get("draftCount") or 0)
            draft_error = str(draft_result.get("error") or "").strip()
            if drafts and draft_error:
                logger.warning(f"投稿处理有提示：{draft_error}")
        except Exception as error:
            failures.append(f"投稿处理：{error}")

    accepted = transfers + drafts + offline_count
    if accepted:
        logger.info(
            f"收到投稿：受理 {accepted} 条（转存 {transfers}、投稿草稿 {drafts}、115 离线 {offline_count}）"
            + (f"；失败 {len(failures)} 条" if failures else "")
        )
    else:
        failures.insert(0, "没有可以处理的链接")
    return {
        "accepted": accepted,
        "transfers": transfers,
        "drafts": drafts,
        "offline": offline_count,
        "failures": failures,
    }


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
    loginExpired: bool = False


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


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "name": "123 Cloud Gateway", "version": "1.0.0"}


@app.get("/api/logs")
async def read_backend_logs(limit: int = Query(1000, ge=1, le=10000)) -> Dict[str, Any]:
    """最近的后端日志（内存环形缓冲），管理后台"运行日志"页面轮询读取。"""
    return {"ok": True, "logs": recent_logs(limit)}


@app.get("/api/admin/status")
async def admin_status() -> Dict[str, Any]:
    session = store.read_session()
    raw_config = store.read_config()
    submission = store.read_submission_config()
    helper_config = submission.get("pan115Helper") if isinstance(submission.get("pan115Helper"), dict) else {}
    transfer_config = raw_config.get("transfer") if isinstance(raw_config.get("transfer"), dict) else {}
    return {
        "ok": True,
        "capabilities": {
            "submissionConfigured": bool(str(submission.get("botToken") or "").strip()),
            "pan115HelperConfigured": bool(helper_config.get("enabled")),
            "transferConfigured": bool(transfer_config.get("enabled")),
        },
        "pan123": await session_payload(session, refresh_profile=True),
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


def _channel_owner_candidates() -> List[int]:
    """频道主候选 = Bot 管理员 ∪ 已授权频道主（与投稿归属判断共用同一份配置）。"""
    config = store.read_submission_config()
    candidates = positive_user_ids(config.get("telegramAdminUserIds"))
    for user_id in positive_user_ids(config.get("channelOwnerUserIds")):
        if user_id not in candidates:
            candidates.append(user_id)
    return candidates


def _require_channel_owner_user_id(user_id: int) -> int:
    if user_id <= 0:
        raise HTTPException(status_code=404, detail="账号不存在")
    if user_id not in _channel_owner_candidates() and not store.has_user_channel_config(user_id):
        raise HTTPException(status_code=403, detail="该账号不是 Bot 管理员或已授权的频道主")
    return user_id


@app.get("/api/submission/channel-owners")
async def list_channel_owners() -> Dict[str, Any]:
    config = store.read_submission_config()
    candidates = _channel_owner_candidates()
    existing = {int(item.get("ownerUserId") or 0) for item in store.list_users_with_channel_configs()}
    for user_id in sorted(existing):
        if user_id > 0 and user_id not in candidates:
            candidates.append(user_id)
    owners = positive_user_ids(config.get("channelOwnerUserIds"))
    admins = positive_user_ids(config.get("telegramAdminUserIds"))
    default_owner = owners[0] if owners else (admins[0] if admins else (candidates[0] if candidates else 0))
    return {"ok": True, "owners": candidates, "defaultOwnerUserId": default_owner}


@app.get("/api/submission/channel-owners/{user_id}")
async def read_channel_owner_config(user_id: int) -> Dict[str, Any]:
    _require_channel_owner_user_id(user_id)
    return {"ok": True, "config": store.read_user_channel_config(user_id)}


@app.put("/api/submission/channel-owners/{user_id}")
async def write_channel_owner_config(user_id: int, payload: OwnUserChannelConfigRequest) -> Dict[str, Any]:
    _require_channel_owner_user_id(user_id)
    try:
        saved = store.write_user_channel_config(user_id, {"channels": payload.channels, "routing": payload.routing})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True, "config": saved}


@app.delete("/api/submission/channel-owners/{user_id}")
async def delete_channel_owner_config(user_id: int) -> Dict[str, Any]:
    _require_channel_owner_user_id(user_id)
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
        routed = await route_submission_text(text, "油猴投稿", max_links=10)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error))
    accepted = int(routed.get("accepted") or 0)
    failures = routed.get("failures") or []
    if not accepted:
        raise HTTPException(status_code=400, detail="；".join(str(item) for item in failures) or "没有可以处理的链接")
    payload: Dict[str, Any] = {"ok": True, "draftCount": accepted, "sentCount": accepted, **routed}
    if failures:
        payload["error"] = f"成功受理 {accepted} 条，失败 {len(failures)} 条：" + "；".join(str(item) for item in failures[:3])
    return payload


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
    saved = store.write_config({"transfer": payload})
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


@app.get("/api/transfer/account-cooldowns")
async def read_transfer_account_cooldowns() -> Dict[str, Any]:
    return {
        "ok": True,
        "accounts": transfer_service.account_cooldown_snapshot(),
        "cooldownMinutes": round(PAN115_ACCOUNT_COOLDOWN_MS / 60000),
    }


@app.delete("/api/transfer/account-cooldowns")
async def clear_transfer_account_cooldowns() -> Dict[str, Any]:
    names = transfer_service.clear_account_cooldowns()
    return {"ok": True, "cleared": len(names), "accounts": names}


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


PROFILE_TTL_SECONDS = 12 * 3600


def _looks_like_login_expired(message: str) -> bool:
    lowered = message.lower()
    return "expired" in lowered or "过期" in message or "请登录" in message or "unauthorized" in lowered


async def session_payload(session: Optional[Dict[str, Any]], refresh_profile: bool = False) -> Dict[str, Any]:
    if not session or not session.get("token"):
        return {"backend": True, "authenticated": False, "user": "", "loginUuid": "", "updatedAt": "", "profile": None, "loginExpired": False}
    profile = session.get("profile")
    login_expired = False
    if not isinstance(profile, dict) or not profile:
        profile = await load_profile(session, str(session.get("user") or ""))
        login_expired = _looks_like_login_expired(str(profile.get("fetchError") or ""))
        session = dict(session)
        session["profile"] = profile
        store.write_session(session)
    elif refresh_profile:
        # 缓存的 profile 里头像是登录时的 CDN 签名链接，过期就成死链；到期自动重取
        fetched_at = profile.get("fetchedAt") if isinstance(profile.get("fetchedAt"), (int, float)) else 0.0
        if time.time() - float(fetched_at) > PROFILE_TTL_SECONDS:
            fresh = await load_profile(session, str(session.get("user") or ""))
            if fresh.get("uid") or fresh.get("headImage"):
                fresh["fetchedAt"] = time.time()
                profile = fresh
                session = dict(session)
                session["profile"] = profile
                store.write_session(session)
            else:
                # 刷新失败（多为登录 token 过期）：保留旧缓存，但把状态暴露给前端提示重新登录
                login_expired = _looks_like_login_expired(str(fresh.get("fetchError") or ""))
    return {
        "backend": True,
        "authenticated": True,
        "user": str(session.get("user") or ""),
        "loginUuid": str(session.get("loginUuid") or ""),
        "updatedAt": str(session.get("updatedAt") or ""),
        "profile": profile if isinstance(profile, dict) else None,
        "loginExpired": login_expired,
    }


async def load_profile(session: Dict[str, Any], fallback_user: str) -> Dict[str, Any]:
    try:
        profile = await pan123.get_user_info(session)
        # 123 官方接口返回的头像等 CDN 链接会轮换过期，记录抓取时间供缓存 TTL 判断
        profile["fetchedAt"] = time.time()
        return profile
    except Exception as error:
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
            "fetchError": str(error),
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
        "pan123ClientId": str(raw.get("pan123ClientId") or "").strip(),
        "pan123ClientSecret": str(raw.get("pan123ClientSecret") or "").strip(),
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
