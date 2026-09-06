"""Telegram 用户 Session 获取（手机号 → 验证码 → 两步验证密码 → StringSession）。

为什么需要它：桌面端原本只给了一个 StringSession 输入框，用户得先在别处跑 telethon
脚本登录、把 session 字符串抠出来再粘回来，客户端自己拿不到 session。这个模块把登录
流程搬进客户端：

    填 API ID / API Hash + 手机号 → 点「获取验证码」→ Telegram 收到验证码 →
    填验证码（开了两步验证再补一次云密码）→ 拿到 StringSession

登录成功后 session 字符串由 main.py 直接写回投稿配置的 telegramApi，用户不用手动粘贴。

登录过程中的 client 只活在内存里（不落盘），登录会话 10 分钟不用自动丢弃并断开连接。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# telethon 是可选依赖：未安装时模块仍可导入，调用时才报"请先安装依赖"。
try:
    from telethon import TelegramClient
    from telethon.errors import (
        FloodWaitError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        SessionPasswordNeededError,
    )
    from telethon.sessions import StringSession

    _TELETHON_IMPORT_ERROR: Optional[Exception] = None
except ImportError as error:  # pragma: no cover - 仅当未安装 telethon 时命中
    TelegramClient = None  # type: ignore[assignment]
    StringSession = None  # type: ignore[assignment]
    _TELETHON_IMPORT_ERROR = error

    class _TelethonUnavailableError(Exception):
        pass

    FloodWaitError = PhoneCodeExpiredError = PhoneCodeInvalidError = _TelethonUnavailableError
    PhoneNumberInvalidError = SessionPasswordNeededError = _TelethonUnavailableError


class TelegramLoginError(RuntimeError):
    """登录流程中的用户可见错误（文案可直接展示在界面上）。"""


LOGIN_TTL_SECONDS = 600  # 验证码登录会话的有效期
_PHONE_RE = re.compile(r"^\+\d{6,15}$")


@dataclass
class _PendingLogin:
    api_id: int
    api_hash: str
    phone: str
    client: Any
    phone_code_hash: str = ""
    expires_at: float = 0.0


# loginId -> 登录会话；进程内存活，重启即失效（本来就是一次性流程）
_pending_logins: Dict[str, _PendingLogin] = {}


def safe_int(value: Any) -> int:
    try:
        return int(str(value or "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def normalize_phone(value: Any) -> str:
    """去掉分隔符，保留国际区号；不带 + 的一律要求用户补全。"""
    text = re.sub(r"[\s\-()]", "", str(value or "")).strip()
    return text


def mask_phone(value: str) -> str:
    if len(value) <= 7:
        return value
    return f"{value[:4]}****{value[-2:]}"


def _delivery_hint(sent: Any) -> str:
    """验证码投递方式：官方客户端内消息 / 短信 / 来电。"""
    name = sent.__class__.__name__.lower() if sent is not None else ""
    if "sms" in name:
        return "短信"
    if "call" in name:
        return "来电"
    if "app" in name:
        return "Telegram 客户端内"
    if "fragment" in name:
        return "Fragment"
    return "Telegram"


async def _disconnect(client: Any) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


async def _discard(login_id: str) -> None:
    entry = _pending_logins.pop(login_id, None)
    if entry is not None:
        await _disconnect(entry.client)


async def _drop_expired() -> None:
    now = time.time()
    for login_id in [key for key, item in _pending_logins.items() if item.expires_at <= now]:
        await _discard(login_id)


def _require_telethon() -> Any:
    if TelegramClient is None or StringSession is None:
        raise TelegramLoginError(
            f"后端缺少 telethon 依赖（{_TELETHON_IMPORT_ERROR}），请重新安装客户端后再获取 Session"
        )
    return TelegramClient


def describe_error(error: Exception) -> TelegramLoginError:
    """把 telethon 的异常翻成能直接显示给用户看的大白话。"""
    if isinstance(error, TelegramLoginError):
        return error
    if isinstance(error, PhoneCodeInvalidError):
        return TelegramLoginError("验证码不正确，请重新填写")
    if isinstance(error, PhoneCodeExpiredError):
        return TelegramLoginError("验证码已过期，请重新获取验证码")
    if isinstance(error, PhoneNumberInvalidError):
        return TelegramLoginError("手机号不正确，请带国际区号填写，例如 +8613800000000")
    if isinstance(error, FloodWaitError):
        seconds = max(1, safe_int(getattr(error, "seconds", 0)))
        return TelegramLoginError(f"Telegram 限流中，请 {seconds} 秒后再试")
    if isinstance(error, (ConnectionError, TimeoutError)):
        return TelegramLoginError("连接 Telegram 失败，请检查网络后重试")
    if "PasswordHashInvalid" in error.__class__.__name__:
        return TelegramLoginError("两步验证密码不正确，请重新填写")
    if "ApiIdInvalid" in error.__class__.__name__ or "api_id" in str(error).lower():
        return TelegramLoginError("TG API ID / API Hash 无效，请到 my.telegram.org 核对后重新填写")
    return TelegramLoginError(f"TG 登录失败：{error}")


async def start_login(api_id: Any, api_hash: Any, phone: Any) -> Dict[str, Any]:
    """发送登录验证码，返回 loginId 供下一步校验使用。"""
    api_id_value = safe_int(api_id)
    api_hash_value = str(api_hash or "").strip()
    phone_value = normalize_phone(phone)
    if api_id_value <= 0 or not api_hash_value:
        raise TelegramLoginError("请先填写 TG API ID 和 API Hash")
    if not phone_value:
        raise TelegramLoginError("请填写手机号")
    if not _PHONE_RE.match(phone_value):
        raise TelegramLoginError("手机号请带国际区号填写，例如 +8613800000000")

    client_class = _require_telethon()
    await _drop_expired()
    client = client_class(StringSession(), api_id_value, api_hash_value)
    try:
        await client.connect()
        sent = await client.send_code_request(phone_value)
    except Exception as error:
        await _disconnect(client)
        raise describe_error(error) from error

    login_id = uuid.uuid4().hex
    _pending_logins[login_id] = _PendingLogin(
        api_id=api_id_value,
        api_hash=api_hash_value,
        phone=phone_value,
        client=client,
        phone_code_hash=str(getattr(sent, "phone_code_hash", "") or ""),
        expires_at=time.time() + LOGIN_TTL_SECONDS,
    )
    delivery = _delivery_hint(getattr(sent, "type", None))
    logger.info("已向 %s 发送 Telegram 登录验证码（%s）", mask_phone(phone_value), delivery)
    return {
        "ok": True,
        "loginId": login_id,
        "phone": phone_value,
        "delivery": delivery,
        "expiresInSeconds": LOGIN_TTL_SECONDS,
        "message": f"验证码已通过{delivery}发送到 {mask_phone(phone_value)}，请在 10 分钟内填写",
    }


async def verify_code(login_id: Any, code: Any, password: Any = "") -> Dict[str, Any]:
    """校验验证码；开了两步验证且没给密码时返回 needPassword=True。"""
    await _drop_expired()
    key = str(login_id or "").strip()
    entry = _pending_logins.get(key)
    if entry is None:
        raise TelegramLoginError("登录会话已过期或不存在，请重新获取验证码")
    code_value = re.sub(r"\D", "", str(code or ""))
    if not code_value:
        raise TelegramLoginError("请填写 Telegram 发来的验证码")

    sign_in_kwargs: Dict[str, Any] = {"phone": entry.phone, "code": code_value}
    if entry.phone_code_hash:
        sign_in_kwargs["phone_code_hash"] = entry.phone_code_hash
    try:
        await entry.client.sign_in(**sign_in_kwargs)
    except SessionPasswordNeededError:
        password_value = str(password or "").strip()
        if not password_value:
            return {
                "ok": True,
                "loginId": key,
                "needPassword": True,
                "message": "该账号开启了两步验证，请填写 Telegram 的云密码后再次提交",
            }
        try:
            await entry.client.sign_in(password=password_value)
        except Exception as error:
            raise describe_error(error) from error
    except Exception as error:
        raise describe_error(error) from error
    return await _finish_login(key, entry)


async def _finish_login(login_id: str, entry: _PendingLogin) -> Dict[str, Any]:
    try:
        session_string = str(entry.client.session.save() or "")
    except Exception as error:
        raise TelegramLoginError(f"读取登录 Session 失败：{error}") from error
    if not session_string:
        await _discard(login_id)
        raise TelegramLoginError("登录成功但没有拿到 Session 字符串，请重新获取验证码")

    account: Dict[str, Any] = {}
    try:
        me = await entry.client.get_me()
        first_name = str(getattr(me, "first_name", "") or "")
        last_name = str(getattr(me, "last_name", "") or "")
        account = {
            "userId": safe_int(getattr(me, "id", 0)),
            "username": str(getattr(me, "username", "") or ""),
            "displayName": " ".join(part for part in (first_name, last_name) if part).strip(),
            "phone": str(getattr(me, "phone", "") or "") or entry.phone,
        }
    except Exception as error:
        logger.warning("TG 登录成功但读取账号资料失败：%s", error)
    await _discard(login_id)

    label = account.get("username") or account.get("displayName") or account.get("phone") or "已授权用户"
    if account.get("username"):
        label = f"@{label}"
    logger.info("TG 用户登录成功：%s（Session 已写入投稿配置）", label)
    return {
        "ok": True,
        "loginId": login_id,
        "needPassword": False,
        "session": session_string,
        "apiId": str(entry.api_id),
        "apiHash": entry.api_hash,
        "account": account or None,
        "message": f"登录成功：{label}，Session 已填入并保存",
    }


async def cancel_login(login_id: Any) -> Dict[str, Any]:
    """放弃这次登录（断开连接、丢弃会话）。"""
    key = str(login_id or "").strip()
    if key:
        await _discard(key)
    return {"ok": True}


def pending_login_ids() -> list:
    return list(_pending_logins.keys())


async def reset_login_state() -> None:
    """丢弃全部登录会话并断开连接（测试与重置用）。"""
    for login_id in list(_pending_logins.keys()):
        await _discard(login_id)
