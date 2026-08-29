from __future__ import annotations

import base64
import os
import re
import time
from typing import Any, Dict, Optional

import httpx


PAN115_QR_SESSION_TTL_MS = max(60_000, int(os.environ.get("PAN115_QR_SESSION_TTL_MS") or 120_000))
PAN115_REQUEST_TIMEOUT_MS = max(5_000, int(os.environ.get("PAN115_REQUEST_TIMEOUT_MS") or 15_000))
PAN115_USER_AGENT = os.environ.get("PAN115_USER_AGENT") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

PAN115_QR_DEVICES = {
    "alipaymini": "115生活(支付宝小程序)",
    "wechatmini": "115生活(微信小程序)",
    "web": "网页版",
    "android": "115生活(Android端)",
    "115android": "115(Android端)",
    "ios": "115生活(iOS端)",
    "115ios": "115(iOS端)",
    "115ipad": "115(iPad端)",
    "tv": "115网盘(Android电视端)",
    "qandroid": "115管理(Android端)",
}

PAN115_QR_STATUS_TEXT = {
    0: "等待扫码",
    1: "已扫码",
    2: "登录成功",
    -1: "已失效",
    -2: "已取消",
}

pan115_qr_sessions: Dict[str, Dict[str, Any]] = {}


class Pan115CookieError(RuntimeError):
    pass


def normalize_pan115_qr_device(input_value: Any) -> str:
    device = str(input_value or "alipaymini")
    return device if device in PAN115_QR_DEVICES else "alipaymini"


def pan115_status_text(status: int) -> str:
    return PAN115_QR_STATUS_TEXT.get(status, "未知状态")


def cleanup_pan115_qr_sessions() -> None:
    now_ms = int(time.time() * 1000)
    for session_id in list(pan115_qr_sessions.keys()):
        if int(pan115_qr_sessions[session_id].get("expiresAt") or 0) <= now_ms:
            pan115_qr_sessions.pop(session_id, None)


def get_pan115_qr_session(session_id: Any) -> Optional[Dict[str, Any]]:
    cleanup_pan115_qr_sessions()
    return pan115_qr_sessions.get(str(session_id or ""))


async def create_pan115_qr_session(device: str) -> Dict[str, Any]:
    device = normalize_pan115_qr_device(device)
    token_url = f"https://qrcodeapi.115.com/api/1.0/{url_quote(device)}/1.0/token/"
    token_data = await read_pan115_json(await fetch_pan115(token_url), "获取 115 二维码 token")
    raw_token = token_data.get("data") if isinstance(token_data.get("data"), dict) else {}
    token = {
        "uid": str(raw_token.get("uid") or ""),
        "time": str(raw_token.get("time") or ""),
        "sign": str(raw_token.get("sign") or ""),
        **({"scanUrl": str(raw_token.get("qrcode"))} if isinstance(raw_token.get("qrcode"), str) else {}),
    }
    if not token["uid"] or not token["time"] or not token["sign"]:
        raise Pan115CookieError(str(token_data.get("error") or "115 未返回有效二维码 token"))

    qrcode_url = f"https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode?uid={url_quote(token['uid'])}"
    qrcode_response = await fetch_pan115(qrcode_url)
    if qrcode_response.status_code >= 400:
        raise Pan115CookieError(f"获取 115 二维码图片失败：HTTP {qrcode_response.status_code}")
    qrcode_type = qrcode_response.headers.get("content-type") or "image/png"
    qrcode_data_url = f"data:{qrcode_type};base64,{base64.b64encode(qrcode_response.content).decode('ascii')}"
    session_id = os.urandom(18).hex()
    expires_at = int(time.time() * 1000) + PAN115_QR_SESSION_TTL_MS
    pan115_qr_sessions[session_id] = {"device": device, "token": token, "expiresAt": expires_at}
    return {
        "sessionId": session_id,
        "qrcodeDataUrl": qrcode_data_url,
        **({"scanUrl": token.get("scanUrl")} if token.get("scanUrl") else {}),
        "device": device,
        "deviceLabel": PAN115_QR_DEVICES[device],
        "expiresAt": ms_to_iso(expires_at),
    }


async def get_pan115_qr_status(session: Dict[str, Any]) -> int:
    token = session.get("token") if isinstance(session.get("token"), dict) else {}
    try:
        data = await read_pan115_json(
            await fetch_pan115(
                "https://qrcodeapi.115.com/get/status/",
                params={"uid": str(token.get("uid") or ""), "time": str(token.get("time") or ""), "sign": str(token.get("sign") or "")},
            ),
            "获取 115 扫码状态",
        )
    except httpx.TimeoutException:
        return 0
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    raw_status = payload.get("status", data.get("status"))
    try:
        return int(raw_status)
    except (TypeError, ValueError):
        if data.get("state") == 1 and data.get("code") == 0:
            return 0
        raise Pan115CookieError(str(data.get("error") or data.get("message") or "115 未返回扫码状态"))


async def confirm_pan115_qr_login(session: Dict[str, Any]) -> Dict[str, Any]:
    device = str(session.get("device") or "alipaymini")
    token = session.get("token") if isinstance(session.get("token"), dict) else {}
    response = await fetch_pan115(
        f"https://passportapi.115.com/app/1.0/{url_quote(device)}/1.0/login/qrcode/",
        method="POST",
        data={"app": device, "account": str(token.get("uid") or "")},
    )
    data = await read_pan115_json(response, "获取 115 Cookie")
    raw_cookie = normalize_pan115_cookie_payload(data, response)
    if not raw_cookie:
        raise Pan115CookieError(str(data.get("error") or "115 未返回 Cookie"))
    cookie_text = pan115_cookie_text(raw_cookie)
    if not cookie_text:
        raise Pan115CookieError("115 返回的 Cookie 内容为空")
    return {
        "ok": True,
        "device": device,
        "deviceLabel": PAN115_QR_DEVICES.get(device, device),
        "cookie": raw_cookie,
        "cookieText": cookie_text,
        "cookieJson": pan115_browser_cookies(raw_cookie),
    }


async def fetch_pan115(url: str, method: str = "GET", params: Optional[Dict[str, str]] = None, data: Optional[Dict[str, str]] = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=PAN115_REQUEST_TIMEOUT_MS / 1000, follow_redirects=True) as client:
        return await client.request(method, url, params=params, data=data, headers={"user-agent": PAN115_USER_AGENT})


async def read_pan115_json(response: httpx.Response, label: str) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise Pan115CookieError(f"{label} 返回非 JSON") from error
    if response.status_code >= 400:
        raise Pan115CookieError(f"{label} 失败：HTTP {response.status_code}")
    return data if isinstance(data, dict) else {}


def parse_pan115_cookie_string(raw: str) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    for part in str(raw or "").split(";"):
        index = part.find("=")
        if index <= 0:
            continue
        key = part[:index].strip()
        value = part[index + 1 :].strip()
        if key and value:
            payload[key] = value
    return payload


def pan115_cookie_entries(cookie: Dict[str, Any]) -> ListTuple:
    by_key: Dict[str, str] = {}
    for key, value in cookie.items():
        if re.match(r"^[A-Za-z0-9_]+$", str(key)) and isinstance(value, str) and value:
            by_key[str(key)] = value
    preferred = ["UID", "CID", "SEID", "KID"]
    result = [(key, by_key[key]) for key in preferred if key in by_key]
    result.extend((key, value) for key, value in by_key.items() if key not in preferred)
    return result


def normalize_pan115_cookie_payload(data: Dict[str, Any], response: httpx.Response) -> Optional[Dict[str, str]]:
    body = data.get("data") if isinstance(data.get("data"), dict) else None
    candidates = [
        body.get("cookie") if body else None,
        data.get("cookie"),
        body,
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            parsed = parse_pan115_cookie_string(candidate)
            if pan115_cookie_entries(parsed):
                return parsed
        if isinstance(candidate, dict) and pan115_cookie_entries(candidate):
            return dict(pan115_cookie_entries(candidate))
    set_cookie = response.headers.get("set-cookie")
    if set_cookie:
        parsed = parse_pan115_cookie_string(set_cookie)
        if pan115_cookie_entries(parsed):
            return parsed
    return None


def pan115_cookie_text(cookie: Dict[str, Any]) -> str:
    return "; ".join(f"{key}={value}" for key, value in pan115_cookie_entries(cookie))


def pan115_browser_cookies(cookie: Dict[str, Any]) -> ListDict:
    return [
        {
            "domain": "115.com",
            "hostOnly": False,
            "httpOnly": True,
            "name": name,
            "path": "/",
            "sameSite": "unspecified",
            "secure": False,
            "session": False,
            "storeId": "0",
            "value": value,
            "id": index + 1,
        }
        for index, (name, value) in enumerate(pan115_cookie_entries(cookie))
    ]


def ms_to_iso(value: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


ListTuple = list[tuple[str, str]]
ListDict = list[dict[str, Any]]
