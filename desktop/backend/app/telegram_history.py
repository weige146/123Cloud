from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# telethon 是可选依赖（原代码在函数内 try/except 导入）。这里在模块层做一次软导入，
# 未安装时退化为"永不匹配"的占位异常，保证模块本身仍能正常导入。
try:
    from telethon.errors import AuthKeyNotFound, FloodWaitError, RPCError, SessionRevokedError
except ImportError:  # pragma: no cover - 仅当未安装 telethon 时命中

    class _TelethonUnavailableError(Exception):
        pass

    AuthKeyNotFound = SessionRevokedError = FloodWaitError = RPCError = _TelethonUnavailableError


# ---- 清理用 Telethon client 单例（只服务于 cleanup，不影响 test() 与投稿海报回退路径）----
_tg_client = None
_tg_client_lock: "asyncio.Lock | None" = None
# session 被服务端吊销等"永久性"故障：置位后 cleanup 直接静默跳过，不再刷日志
_tg_client_bad = False
is_session_permanently_invalid = False
_invalid_logged = False  # 去重：永久失效那条 ERROR 只记一次

FLOODWAIT_MAX_SECONDS = 60  # 超过该等待时长则放弃本次清理
TRANSIENT_RETRIES = 2  # 瞬时故障重试次数
TRANSIENT_BACKOFF_SECONDS = 1


def _empty_cleanup_result() -> Dict[str, List[int]]:
    """清理被跳过/失败时的统一返回值。

    必须是 dict 而不是 None：submission.cleanup_published_submission_history 会对结果做
    isinstance(result, dict) 校验，返回 None 会让它走进 except 分支并继续刷 WARNING。
    """
    return {"deletedMessageIds": [], "failedMessageIds": []}


def _get_client_lock() -> "asyncio.Lock":
    global _tg_client_lock
    if _tg_client_lock is None:
        _tg_client_lock = asyncio.Lock()
    return _tg_client_lock


class TelegramHistoryCleaner:
    async def test(self, config: Dict[str, Any]) -> str:
        client = await connect_telegram_client(config)
        try:
            me = await client.get_me()
            username = getattr(me, "username", "") or ""
            name = getattr(me, "first_name", "") or getattr(me, "firstName", "") or ""
            return f"TG API 正常：{('@' + username) if username else (name or '已授权用户')}"
        finally:
            await client.disconnect()

    async def cleanup(self, config: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, List[int]]:
        # session 已被判定为永久失效：静默跳过，不连服务端、不打日志、不惊动上层。
        if is_session_permanently_invalid or _tg_client_bad:
            return _empty_cleanup_result()

        attempt = 0
        while True:
            try:
                client = await get_telegram_client(config)
                if client is None:
                    return _empty_cleanup_result()
                return await _do_cleanup(client, post)
            except (AuthKeyNotFound, SessionRevokedError) as error:
                await _mark_session_invalid(
                    f"session 已失效（{type(error).__name__}），"
                    f"请到管理后台『投稿设置 → TG API』重新填入新的用户 Session 字符串"
                )
                return _empty_cleanup_result()
            except FloodWaitError as error:
                # 限流：等待可接受则重试一次，否则放弃本次（避免每次投稿都刷一条警告）
                if attempt >= 1 or safe_int(getattr(error, "seconds", 0)) > FLOODWAIT_MAX_SECONDS:
                    return _empty_cleanup_result()
                await asyncio.sleep(safe_int(getattr(error, "seconds", 0)))
                attempt += 1
            except (ConnectionError, TimeoutError, RPCError):
                if attempt >= TRANSIENT_RETRIES:
                    return _empty_cleanup_result()
                await asyncio.sleep(TRANSIENT_BACKOFF_SECONDS * (attempt + 1))
                attempt += 1
            except Exception:
                # 兜底：保持"清理失败不影响投稿"的既有语义
                return _empty_cleanup_result()


async def _do_cleanup(client: Any, post: Dict[str, Any]) -> Dict[str, List[int]]:
    """原来的清理主体，独立成函数以便外层做异常分类与重试。"""
    peer = await resolve_input_peer(client, str(post.get("chatId") or ""))
    current_message_id = safe_int(post.get("messageId"))
    candidates = set()
    marker = tmdb_post_marker(str(post.get("mediaType") or "unknown"), post.get("tmdbId"))

    if marker:
        messages = await client.get_messages(peer, search=marker, max_id=current_message_id, limit=None)
        for message in messages:
            matched_id = message_id(message)
            if is_older_message(matched_id, current_message_id) and tmdb_publication_matches(message, post):
                candidates.add(matched_id)

    async for message in client.iter_messages(peer, max_id=current_message_id, limit=None):
        current_id = message_id(message)
        if not is_older_message(current_id, current_message_id):
            continue
        if has_tmdb_post_marker(message_text(message)):
            continue
        if publication_button_matches(publication_button_value(message_reply_markup(message), bool(post.get("fastLink"))), post):
            candidates.add(current_id)

    deleted: List[int] = []
    failed: List[int] = []
    for batch in chunks(sorted(candidates), 100):
        try:
            await client.delete_messages(peer, batch, revoke=True)
            deleted.extend(batch)
        except Exception:
            failed.extend(batch)
    return {"deletedMessageIds": deleted, "failedMessageIds": failed}


async def get_telegram_client(config: Dict[str, Any]) -> Any:
    """懒加载单例 client。session 永久失效时返回 None，调用方应跳过本次清理。

    单例只给 cleanup 复用；test() 与投稿海报回退路径仍走一次性独立连接。
    """
    global _tg_client
    if is_session_permanently_invalid or _tg_client_bad:
        return None
    async with _get_client_lock():
        if is_session_permanently_invalid or _tg_client_bad:
            return None
        if _tg_client is not None:
            if _tg_client.is_connected():
                return _tg_client
            # 旧 client 已断线：先释放再重建，避免连接泄漏
            stale, _tg_client = _tg_client, None
            try:
                await stale.disconnect()
            except Exception:
                pass
        _tg_client = await connect_telegram_client(config)
        return _tg_client


async def _mark_session_invalid(reason: str) -> None:
    """标记 session 永久失效：只在这一刻记一条 ERROR，之后清理全部静默。"""
    global _tg_client_bad, is_session_permanently_invalid, _invalid_logged, _tg_client
    _tg_client_bad = True
    is_session_permanently_invalid = True
    if not _invalid_logged:
        _invalid_logged = True
        logger.error("TG session 永久失效，已停止频道旧帖清理：%s", reason)
    if _tg_client is not None:
        try:
            await _tg_client.disconnect()
        except Exception:
            pass
        _tg_client = None


def reset_telegram_client_state() -> None:
    """重置单例与失效标记（供测试与 session 更新后复用）。

    连同 lock 一起丢弃：Python 3.9 的 asyncio.Lock 会绑定首次 await 它的 event loop，
    跨 loop 复用会报错。
    """
    global _tg_client, _tg_client_lock, _tg_client_bad, is_session_permanently_invalid, _invalid_logged
    _tg_client = None
    _tg_client_lock = None
    _tg_client_bad = False
    is_session_permanently_invalid = False
    _invalid_logged = False


async def connect_telegram_client(config: Dict[str, Any]) -> Any:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as error:
        raise RuntimeError("请先安装后端依赖 telethon 后再启用 TG API 旧帖清理") from error

    api_id = safe_int(config.get("apiId"))
    api_hash = str(config.get("apiHash") or "").strip()
    session = str(config.get("session") or "").strip()
    if api_id <= 0 or not api_hash or not session:
        raise ValueError("请先配置 TG API ID、API Hash 和用户 Session")

    client = TelegramClient(StringSession(session), api_id, api_hash, connection_retries=3, retry_delay=1)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("TG API Session 未授权或已失效")
    except Exception:
        # connect()/授权校验失败时不能把半死的 client 留在手上
        try:
            await client.disconnect()
        except Exception:
            pass
        raise
    return client


async def resolve_input_peer(client: Any, chat_id: str) -> Any:
    try:
        return await client.get_input_entity(int(chat_id))
    except Exception:
        dialogs = await client.get_dialogs(limit=None)
        wanted = int(chat_id)
        for dialog in dialogs:
            if safe_int(getattr(dialog, "id", 0)) == wanted:
                return getattr(dialog, "input_entity")
    raise ValueError(f"TG API 用户无法访问目标频道：{chat_id}")


def tmdb_post_marker(media_type: str, tmdb_id: Any) -> str:
    value = safe_int(tmdb_id)
    if value <= 0 or media_type not in {"movie", "tv"}:
        return ""
    return f"{'📺' if media_type == 'tv' else '🎬'} TMDB: {value}"


def has_tmdb_post_marker(text: str) -> bool:
    return re.search(r"(?:🎬|📺)\s*TMDB:\s*\d+", str(text or "")) is not None


def message_id(message: Any) -> int:
    return safe_int(message.get("id") if isinstance(message, dict) else getattr(message, "id", 0))


def message_text(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("message") or message.get("text") or message.get("caption") or "")
    return str(getattr(message, "message", "") or getattr(message, "text", "") or getattr(message, "caption", "") or "")


def message_reply_markup(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("reply_markup") or message.get("replyMarkup")
    return getattr(message, "reply_markup", None) or getattr(message, "replyMarkup", None)


def tmdb_publication_matches(message: Any, post: Dict[str, Any]) -> bool:
    marker = tmdb_post_marker(str(post.get("mediaType") or "unknown"), post.get("tmdbId"))
    if not marker or marker not in message_text(message):
        return False
    if publication_button_matches(publication_button_value(message_reply_markup(message), bool(post.get("fastLink"))), post):
        return True
    return publication_resource_matches(message_text(message), post)


def publication_resource_matches(text: str, post: Dict[str, Any]) -> bool:
    resource_key = normalize_publication_identity_text(str(post.get("resourceName") or ""))
    if not resource_key:
        return False
    return resource_key in normalize_publication_identity_text(text)


def publication_button_matches(button_value: str, post: Dict[str, Any]) -> bool:
    share_url = str(post.get("shareUrl") or "")
    if not button_value or not share_url:
        return False
    if button_value == share_url:
        return True
    if bool(post.get("fastLink")):
        return button_value.strip() == share_url.strip()
    return web_share_links_equivalent(button_value, share_url)


def web_share_links_equivalent(left: str, right: str) -> bool:
    left_key = web_share_identity(left)
    right_key = web_share_identity(right)
    if not left_key or not right_key:
        return False
    left_share_key, left_pwd = left_key
    right_share_key, right_pwd = right_key
    if left_share_key.lower() != right_share_key.lower():
        return False
    return not left_pwd or not right_pwd or left_pwd.lower() == right_pwd.lower()


def web_share_identity(value: str) -> tuple[str, str]:
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return ("", "")
    if not parsed.scheme or not parsed.netloc:
        return ("", "")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ("", "")
    if parts[0] in {"s", "ps", "123pan"} and len(parts) > 1:
        share_key = parts[1]
    else:
        share_key = parts[0]
    share_key = re.sub(r"\.html$", "", share_key, flags=re.I)
    query = parse_qs(parsed.query)
    pwd = str((query.get("pwd") or query.get("password") or [""])[0] or "")
    return (share_key, pwd)


def publication_button_value(reply_markup: Any, fast_link: bool) -> str:
    for button in iter_reply_buttons(reply_markup):
        class_name = button.__class__.__name__
        copy_text = button_copy_text(button)
        url = button_url(button)
        if fast_link and ("Copy" in class_name or copy_text) and copy_text:
            return copy_text
        if not fast_link and ("Url" in class_name or url) and url:
            return url
    return ""


def iter_reply_buttons(reply_markup: Any) -> Iterable[Any]:
    if isinstance(reply_markup, dict):
        if isinstance(reply_markup.get("inline_keyboard"), list):
            return [button for row in reply_markup.get("inline_keyboard") or [] if isinstance(row, list) for button in row]
        rows = reply_markup.get("rows")
    else:
        rows = getattr(reply_markup, "rows", None)
    if not isinstance(rows, list):
        return []
    buttons = []
    for row in rows:
        row_buttons = row.get("buttons") if isinstance(row, dict) else getattr(row, "buttons", None)
        if isinstance(row_buttons, list):
            buttons.extend(row_buttons)
    return buttons


def button_copy_text(button: Any) -> str:
    if isinstance(button, dict):
        value = button.get("copy_text") or button.get("copyText")
    else:
        value = getattr(button, "copy_text", None) or getattr(button, "copyText", None)
    if isinstance(value, dict):
        value = value.get("text") or value.get("copy_text") or value.get("copyText")
    elif not isinstance(value, str) and hasattr(value, "text"):
        value = getattr(value, "text", "")
    return str(value or "")


def button_url(button: Any) -> str:
    value = button.get("url") if isinstance(button, dict) else getattr(button, "url", None)
    return str(value or "")


def normalize_publication_identity_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\{?\b(?:tmdbid|tmdb)[=\-_: ]?\d{2,10}\}?", " ", text, flags=re.I)
    text = re.sub(r"[\s._\-:/\\|()[\]{}<>《》【】（）]+", " ", text)
    return text.strip().casefold()


def is_older_message(message_id: Any, current_message_id: int) -> bool:
    value = safe_int(message_id)
    return value > 0 and current_message_id > 0 and value < current_message_id


def chunks(values: List[int], size: int) -> List[List[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
