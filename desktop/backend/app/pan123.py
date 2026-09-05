from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import weakref
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx


OPEN_API_BASE = "https://open-api.123pan.com"
PAN_LOGIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 123XiaoZhuShou/1.0"
)
# OAuth 授权换 token 的社区中转服务（OpenList APIPages，源码 AGPLv3 开源）。
# 用户在 123 官方授权页输入账号密码完成授权，本服务代持 clientSecret 负责
# code 换 token 与后续 refresh_token 刷新，本程序全程不需要自己的开放平台密钥。
PAN123_OAUTH_BROKER_URL = "https://api.oplist.org"
PAN123_OAUTH_DRIVER = "123cloud"
PAN_SHARE_HOST_SUFFIXES = (
    ".share.123pan.cn",
    ".share.123pan.com",
    ".share.123912.com",
    ".share.123635.com",
    ".share.123865.com",
    ".share.123684.com",
)
PAN_SHARE_PUBLIC_HOSTS = {
    "123pan.cn",
    "123pan.com",
    "www.123pan.cn",
    "www.123pan.com",
    "yun.123pan.cn",
    "yun.123pan.com",
    "123912.com",
    "www.123912.com",
    "123635.com",
    "www.123635.com",
    "123865.com",
    "www.123865.com",
    "123684.com",
    "www.123684.com",
}


class Pan123Error(RuntimeError):
    def __init__(self, message: str, code: int = 0, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


class _ConcurrencyGate:
    def __init__(self, limit: int):
        self.limit = limit
        self._semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()

    def current(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.limit)
            self._semaphores[loop] = semaphore
        return semaphore


_READ_GATE = _ConcurrencyGate(6)
_WRITE_GATE = _ConcurrencyGate(3)


class _PooledAsyncClient(httpx.AsyncClient):
    def __init__(self, *args: Any, gate: _ConcurrencyGate, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._gate = gate

    async def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                async with self._gate.current():
                    response = await super().request(method, url, **kwargs)
                if response.status_code not in {408, 425, 429, 502, 503, 504} or attempt >= 2:
                    return response
                retry_after = response.headers.get("retry-after", "")
                try:
                    delay = min(5.0, max(0.2, float(retry_after)))
                except (TypeError, ValueError):
                    delay = 0.4 * (2**attempt)
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                last_error = error
                if attempt >= 2:
                    raise
                await asyncio.sleep(0.4 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise Pan123Error("123 请求重试失败")


class _HttpClientPool:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self._read_client: Optional[httpx.AsyncClient] = None
        self._write_client: Optional[httpx.AsyncClient] = None

    def read(self) -> httpx.AsyncClient:
        if self._read_client is None or self._read_client.is_closed:
            self._read_client = _PooledAsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=6, max_keepalive_connections=6, keepalive_expiry=30.0),
                gate=_READ_GATE,
            )
        return self._read_client

    def write(self) -> httpx.AsyncClient:
        if self._write_client is None or self._write_client.is_closed:
            self._write_client = _PooledAsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=3, max_keepalive_connections=3, keepalive_expiry=30.0),
                gate=_WRITE_GATE,
            )
        return self._write_client

    async def close(self) -> None:
        clients = [client for client in (self._read_client, self._write_client) if client is not None and not client.is_closed]
        self._read_client = None
        self._write_client = None
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


class Pan123Client:
    """123 分享公开接口客户端（列分享详情/分享目录，无需登录态）。"""

    def __init__(self, timeout_seconds: float = 20.0):
        self.timeout = timeout_seconds
        self._http = _HttpClientPool(self.timeout)

    async def close(self) -> None:
        await self._http.close()

    async def get_share_info(self, share_url: str) -> Dict[str, Any]:
        parsed = parse_pan123_share_url(share_url)
        client = self._http.read()
        response = await client.get(
            f"{parsed['origin']}/b/api/share/info",
            params={"shareKey": parsed["shareKey"]},
            headers=share_request_headers(share_url),
        )
        data = safe_json(response)
        payload = data.get("info") if isinstance(data.get("info"), dict) else data
        body = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        code = number_value(payload.get("code"))
        if response.status_code >= 400 or code not in (0, 200, None):
            raise Pan123Error(str(payload.get("message") or "123 分享详情获取失败"), code=code or response.status_code)
        return {
            "shareKey": str(body.get("ShareKey") or parsed["shareKey"]),
            "shareName": str(body.get("ShareName") or ""),
            "userId": number_value(body.get("UserID"), body.get("userId")) or 0,
            "hasPassword": bool_value(body.get("HasPwd"), body.get("hasPwd")) or False,
            "expired": bool_value(body.get("Expired"), body.get("expired")) or False,
            "origin": parsed["origin"],
        }

    async def list_share_root_items(self, share_url: str, share_password: str = "") -> List[Dict[str, Any]]:
        return await self.list_share_items(share_url, share_password, "0")

    async def list_share_items(self, share_url: str, share_password: str, parent_file_id: str) -> List[Dict[str, Any]]:
        """列出分享中某个目录（ParentFileId）下的全部条目，自动翻页。"""
        parsed = parse_pan123_share_url(share_url)
        items: List[Dict[str, Any]] = []
        page = 1
        next_token = "0"
        client = self._http.read()
        while True:
            params = {
                "limit": "100",
                "next": next_token,
                "orderBy": "file_name",
                "orderDirection": "asc",
                "shareKey": parsed["shareKey"],
                "ParentFileId": str(parent_file_id or "0"),
                "Page": str(page),
                "event": "homeListFile",
                "operateType": "1",
                "SharePwd": str(share_password or ""),
            }
            response = await client.get(
                f"{parsed['origin']}/b/api/share/get",
                params=params,
                headers=share_request_headers(share_url),
            )
            data = safe_json(response)
            body = data.get("data") if isinstance(data.get("data"), dict) else {}
            code = number_value(data.get("code"))
            if response.status_code >= 400 or code not in (0, 200, None):
                raise Pan123Error(str(data.get("message") or "123 分享文件列表获取失败"), code=code or response.status_code)
            page_items = first_record_list(body, ("InfoList", "infoList", "fileList", "FileList", "list", "items"))
            normalized = [item for item in (normalize_file(entry) for entry in page_items) if item is not None]
            items.extend(normalized)
            response_next = str(body.get("Next") or body.get("next") or body.get("LastFileId") or body.get("lastFileId") or "").strip()
            last_file_id = str((normalized[-1] if normalized else {}).get("FileId") or (normalized[-1] if normalized else {}).get("fileId") or "")
            if response_next in {"0", "-1"}:
                response_next = ""
            next_candidate = response_next or (last_file_id if len(page_items) >= 100 else "-1")
            if next_candidate == "-1" or next_candidate == next_token:
                break
            next_token = next_candidate
            page += 1
        return items


class Pan123OauthStore:
    """Pan123OpenAPIClient 的授权数据持久化接口（refresh/access token + 账号资料）。"""

    async def load(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def save(self, data: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


class SyncPan123OauthStore(Pan123OauthStore):
    """把同步的 kv 读写函数适配成异步接口（避免 pan123 依赖 session_store）。"""

    def __init__(self, load: Callable[[], Optional[Dict[str, Any]]], save: Callable[[Dict[str, Any]], None], clear: Callable[[], None]):
        self._load = load
        self._save = save
        self._clear = clear

    async def load(self) -> Optional[Dict[str, Any]]:
        return self._load()

    async def save(self, data: Dict[str, Any]) -> None:
        self._save(data)

    async def clear(self) -> None:
        self._clear()


class Pan123OauthBroker:
    """123 OpenAPI OAuth 授权的社区中转客户端（OpenList APIPages 协议）。

    - authorize_url：向中转站拿 123 官方授权页跳转地址（授权页输入账号密码）
    - exchange_code：授权回调的 code 交给中转站换取 access/refresh token
    - refresh：用 refresh_token 换新 access_token（clientSecret 由中转站保管）
    """

    def __init__(self, base_url: str = PAN123_OAUTH_BROKER_URL, timeout_seconds: float = 25.0):
        self.base_url = str(base_url or PAN123_OAUTH_BROKER_URL).strip().rstrip("/")
        self.timeout = timeout_seconds
        self._http = _HttpClientPool(self.timeout)

    async def close(self) -> None:
        await self._http.close()

    def _driver_root(self) -> str:
        return f"{self.base_url}/{PAN123_OAUTH_DRIVER}"

    async def authorize_url(self) -> Dict[str, str]:
        client = self._http.read()
        response = await client.get(
            f"{self._driver_root()}/requests",
            params={"server_use": "true", "driver_txt": f"{PAN123_OAUTH_DRIVER}_oa"},
            headers=_broker_headers(),
        )
        payload = safe_json(response)
        authorize_url = string_value(payload.get("text"))
        if response.status_code >= 400 or not authorize_url.startswith("http"):
            raise Pan123Error(
                str(payload.get("text") or payload.get("message") or f"获取 123 授权地址失败（HTTP {response.status_code}）")
            )
        parsed = urlparse(authorize_url)
        query = parse_qs(parsed.query)
        redirect_uri = string_value(*query.get("redirect_uri", []))
        if not redirect_uri:
            raise Pan123Error("123 授权地址缺少回调回调参数（redirect_uri）")
        return {"authorizeUrl": authorize_url, "redirectUri": redirect_uri}

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        client = self._http.read()
        response = await client.get(
            f"{self._driver_root()}/callback",
            params={"code": str(code or "").strip()},
            headers=_broker_headers(),
            follow_redirects=False,
        )
        location = response.headers.get("location", "")
        if response.status_code in (301, 302, 303, 307, 308) and "#" in location:
            data = decode_oplist_callback_fragment(location)
        else:
            payload = safe_json(response)
            message = string_value(payload.get("text") or payload.get("message"))
            raise Pan123Error(message or f"123 授权码换取 token 失败（HTTP {response.status_code}）")
        access_token = string_value(data.get("access_token"))
        refresh_token = string_value(data.get("refresh_token"))
        if data.get("message_err"):
            raise Pan123Error(str(data["message_err"]))
        if not access_token or not refresh_token:
            raise Pan123Error("123 授权回调未返回完整的 token 信息")
        return {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresIn": number_value(data.get("expires_in")),
        }

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        token = str(refresh_token or "").strip()
        if not token:
            raise Pan123Error("缺少 123 刷新令牌（refresh token）")
        client = self._http.read()
        response = await client.get(
            f"{self._driver_root()}/renewapi",
            params={"refresh_ui": token},
            headers=_broker_headers(),
        )
        payload = safe_json(response)
        access_token = string_value(payload.get("access_token"))
        if response.status_code >= 400 or not access_token:
            message = string_value(payload.get("text") or payload.get("message"))
            if _looks_like_invalid_refresh_grant(message) or not message:
                raise Pan123Error("123 授权已失效，请到设置里重新授权登录（刷新令牌无效）")
            raise Pan123Error(message)
        return {
            "accessToken": access_token,
            "refreshToken": string_value(payload.get("refresh_token")) or token,
            "expiresIn": number_value(payload.get("expires_in")),
        }


def _broker_headers() -> Dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "user-agent": PAN_LOGIN_USER_AGENT,
        "referer": f"{PAN123_OAUTH_BROKER_URL}/",
    }


def _looks_like_invalid_refresh_grant(message: str) -> bool:
    return bool(re.search(
        r"invalid|expired|revoked|refresh\s*token|授权|刷新令牌|失效|过期",
        str(message or ""),
        re.I,
    ))


def decode_oplist_callback_fragment(location: str) -> Dict[str, Any]:
    """解析中转站回调 302 Location 里 /#<base64(json)> 携带的 token 数据。"""
    fragment = str(location or "").split("#", 1)[-1]
    fragment = fragment.strip()
    if not fragment:
        return {}
    try:
        padded = fragment + "=" * (-len(fragment) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class Pan123OpenAPIClient:
    """123 OpenAPI 客户端：access_token 由 OAuth 授权得来并自动刷新。

    授权入口在设置页：用户在 123 官方授权页输入账号密码完成授权后，
    refresh_token 持久化在本机，access_token 过期即用 refresh_token 静默换取。
    """

    clientKind = "openapi"

    def __init__(
        self,
        broker: Optional[Pan123OauthBroker] = None,
        oauth_store: Optional[Pan123OauthStore] = None,
        timeout_seconds: float = 20.0,
    ):
        self.broker = broker or Pan123OauthBroker(timeout_seconds=timeout_seconds)
        self.oauth_store = oauth_store
        self.timeout = timeout_seconds
        self._http = _HttpClientPool(self.timeout)
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._refresh_token = ""
        self._store_loaded = False
        self._access_token_lock: Optional[asyncio.Lock] = None

    async def close(self) -> None:
        await self._http.close()
        await self.broker.close()

    # ------------------------------------------------------------------
    # 授权数据
    # ------------------------------------------------------------------
    @property
    def authorized(self) -> bool:
        return bool(self._refresh_token)

    async def load_authorization(self) -> Dict[str, Any]:
        """从持久层读授权数据（refresh token + 缓存的 access token），进程启动后调用一次。"""
        if self._store_loaded or self.oauth_store is None:
            return self._snapshot()
        self._store_loaded = True
        stored = await self.oauth_store.load()
        if isinstance(stored, dict):
            self._refresh_token = str(stored.get("refreshToken") or "")
            cached_token = str(stored.get("accessToken") or "")
            cached_expires_at = float(stored.get("expiresAt") or 0)
            if cached_token and cached_expires_at > time.time() + 60:
                self._access_token = cached_token
                self._access_token_expires_at = cached_expires_at
        return self._snapshot()

    def bind_tokens(self, access_token: str, refresh_token: str, expires_in: Optional[int] = None) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._access_token_expires_at = pan123_open_token_expires_at(
            access_token, {"expiresIn": expires_in} if expires_in else None
        )
        self._store_loaded = True

    async def persist(self, clear: bool = False) -> None:
        if self.oauth_store is None:
            return
        if clear:
            self._refresh_token = ""
            self._access_token = ""
            self._access_token_expires_at = 0.0
            await self.oauth_store.clear()
            return
        await self.oauth_store.save({
            "refreshToken": self._refresh_token,
            "accessToken": self._access_token,
            "expiresAt": self._access_token_expires_at,
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })

    def _snapshot(self) -> Dict[str, Any]:
        return {
            "refreshToken": self._refresh_token,
            "accessToken": self._access_token,
            "expiresAt": self._access_token_expires_at,
        }

    # ------------------------------------------------------------------
    # OpenAPI 接口
    # ------------------------------------------------------------------
    async def get_open_user_info(self) -> Dict[str, Any]:
        data = await self.request("GET", "/api/v1/user/info")
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        return normalize_open_user_info(body)

    async def list_files(self, parent_file_id: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        last_file_id = 0
        for _ in range(100):
            data = await self.request(
                "GET",
                "/api/v2/file/list",
                query={"parentFileId": str(parent_file_id or "0"), "limit": "100", "lastFileId": str(last_file_id)},
            )
            body = data.get("data") if isinstance(data.get("data"), dict) else {}
            batch = body.get("fileList") if isinstance(body.get("fileList"), list) else []
            for item in batch:
                if is_trashed(item):
                    continue
                normalized = normalize_file(item)
                if normalized is not None:
                    files.append(normalized)
            has_response_cursor = "lastFileId" in body or "lastFileID" in body
            response_next = number_value(body.get("lastFileId"), body.get("lastFileID"))
            fallback_next = number_value((batch[-1] if batch else {}).get("fileId"), (batch[-1] if batch else {}).get("fileID"))
            # OpenAPI explicitly returns 0 for the final page. Do not replace it
            # with the last file ID, otherwise the first page is requested again.
            next_file_id = response_next if has_response_cursor else (fallback_next or 0)
            if not batch or next_file_id == -1 or not next_file_id or next_file_id == last_file_id:
                break
            last_file_id = next_file_id
        return files

    async def rename_file(self, file_id: int, filename: str) -> None:
        await self.request("POST", "/api/v1/file/rename", json={"renameList": [f"{int(file_id)}|{filename}"]})

    async def move_files(self, file_ids: Iterable[int], target_parent_file_id: str) -> None:
        ids = sorted({int(file_id) for file_id in file_ids if int(file_id) > 0})
        for batch in chunks(ids, 100):
            await self.request(
                "POST",
                "/api/v1/file/move",
                json={"fileIDs": batch, "toParentFileID": int(target_parent_file_id or "0")},
            )

    async def trash_files(self, file_ids: Iterable[int]) -> None:
        ids = sorted({int(file_id) for file_id in file_ids if int(file_id) > 0})
        for batch in chunks(ids, 100):
            await self.request("POST", "/api/v1/file/trash", json={"fileIDs": batch})

    async def ensure_path(self, root_dir_id: str, path: Iterable[str]) -> str:
        current = str(root_dir_id or "0")
        for raw_part in path:
            part = str(raw_part or "").strip()
            if not part:
                continue
            existing = next((file for file in await self.list_files(current) if int(file.get("type") or 0) == 1 and file.get("name") == part), None)
            if existing:
                current = str(existing.get("fileId") or existing.get("id"))
                continue
            try:
                current = await self.create_folder(current, part)
            except Exception:
                raced = next((file for file in await self.list_files(current) if int(file.get("type") or 0) == 1 and file.get("name") == part), None)
                if not raced:
                    raise
                current = str(raced.get("fileId") or raced.get("id"))
        return current

    async def create_folder(self, parent_file_id: str, name: str) -> str:
        data = await self.request(
            "POST",
            "/upload/v1/file/mkdir",
            json={"name": str(name), "parentID": int(parent_file_id or "0")},
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        dir_id = number_value(body.get("dirID"), body.get("dirId"), body.get("fileID"), body.get("fileId"), body.get("FileId"))
        if not dir_id or dir_id <= 0:
            raise Pan123Error(f"123 OpenAPI 创建目录失败：{name}")
        return str(dir_id)

    async def find_same_file(self, parent_file_id: str, name: str, size: int) -> Optional[Dict[str, Any]]:
        for file in await self.list_files(parent_file_id):
            if int(file.get("type") or 0) != 1 and file.get("name") == name and int(file.get("size") or 0) == int(size or 0):
                return file
        return None

    async def find_file_by_size(
        self,
        parent_file_id: str,
        size: int,
        exclude_name: Optional[str] = None,
        exclude_file_ids: Optional[Iterable[int]] = None,
    ) -> Optional[Dict[str, Any]]:
        excluded = {int(file_id) for file_id in (exclude_file_ids or []) if int(file_id) > 0}
        for file in await self.list_files(parent_file_id):
            file_id = int(file.get("fileId") or file.get("id") or 0)
            if int(file.get("type") or 0) == 1:
                continue
            if int(file.get("size") or 0) != int(size or 0):
                continue
            if exclude_name and file.get("name") == exclude_name:
                continue
            if file_id in excluded:
                continue
            return file
        return None

    async def sha1_reuse(self, parent_file_id: str, filename: str, sha1: str, size: int) -> Optional[int]:
        data = await self.request(
            "POST",
            "/upload/v2/file/sha1_reuse",
            json={"parentFileID": int(parent_file_id or "0"), "filename": filename, "sha1": str(sha1).lower(), "size": int(size or 0)},
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        if bool_value(body.get("reuse"), body.get("Reuse")):
            return number_value(body.get("fileID"), body.get("FileId"), body.get("fileId"))
        return None

    async def md5_reuse(self, parent_file_id: str, filename: str, etag: str, size: int) -> Optional[int]:
        data = await self.request(
            "POST",
            "/upload/v2/file/create",
            json={"parentFileID": int(parent_file_id or "0"), "filename": filename, "etag": str(etag).lower(), "size": int(size or 0)},
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        if bool_value(body.get("reuse"), body.get("Reuse")):
            return number_value(body.get("fileID"), body.get("FileId"), body.get("fileId"))
        return None

    async def create_offline_download(self, url: str, dir_id: str, filename: str = "") -> int:
        last_error = ""
        # 撞"同时下载的任务超出最大限制"时按 5/15/30 秒退避重试；参照成熟工具的做法，
        # 大分享集中提交离线时这几乎必然发生，100ms 级重试等于直接放弃
        backoff_seconds = (5, 15, 30)
        for attempt in range(1, 5):
            try:
                data = await self.request(
                    "POST",
                    "/api/v1/offline/download",
                    json={"url": url, "fileName": filename or "", "dirId": int(dir_id or "0")},
                )
                body = data.get("data") if isinstance(data.get("data"), dict) else {}
                task_id = number_value(body.get("taskID"), body.get("taskId"), body.get("id"))
                if not task_id:
                    raise Pan123Error("123 OpenAPI 离线创建未返回任务 ID")
                return task_id
            except Exception as error:
                last_error = str(error)
                if attempt >= 4 or not re.search(r"同时下载|最大限制|maximum|limit", last_error, re.I):
                    break
                await sleep_seconds(backoff_seconds[min(attempt, len(backoff_seconds)) - 1] * 1000)
        raise Pan123Error(f"123 OpenAPI 离线创建失败（目录 ID {dir_id}）：{last_error or '未知错误'}")

    async def get_offline_process(self, task_id: int) -> Dict[str, Any]:
        data = await self.request("GET", "/api/v1/offline/download/process", query={"taskId": str(task_id)})
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        process = float_value(body.get("process"), body.get("progress"), body.get("percent"), body.get("downloadProgress")) or 0.0
        status = normalize_offline_status(body.get("status", body.get("state", body.get("taskStatus"))), process)
        return {"process": max(0.0, min(100.0, process)), "status": status, "raw": body}

    async def list_offline_tasks(self) -> List[Dict[str, Any]]:
        return []

    async def delete_offline_tasks(self, task_ids: Iterable[int]) -> None:
        del task_ids
        raise Pan123Error("123 OpenAPI 未提供离线任务删除接口")

    async def download_info(self, file_id: int) -> str:
        """获取 123 文件下载直链（OpenAPI）。"""
        data = await self.request("GET", "/api/v1/file/download_info", query={"fileId": str(int(file_id or 0))})
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        url = string_value(body.get("downloadUrl"), body.get("DownloadUrl"), body.get("url"), body.get("Url"))
        if not url:
            raise Pan123Error("123 OpenAPI 未返回下载直链")
        return url

    async def request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        retried: bool = False,
    ) -> Dict[str, Any]:
        # 123 OpenAPI 限流（"操作频繁"）做自动重试 + 指数退避，避免并发 5+ worker 撞限流
        max_attempts = 3
        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._request_once(method, path, query, json, retried)
            except Pan123Error as error:
                last_error = error
                if not is_openapi_rate_limited(error.code or 0, error.data or {}):
                    raise
                if attempt >= max_attempts:
                    raise
                wait_seconds = 1.5 * attempt
                await sleep_seconds(wait_seconds)
                continue
        # 不应该到这里，但保险起见抛最后一次
        raise last_error or Pan123Error("123 OpenAPI 限流重试失败")

    async def _request_once(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, str]],
        json: Optional[Dict[str, Any]],
        retried: bool,
    ) -> Dict[str, Any]:
        token = await self.get_token()
        headers = {"Authorization": "Bearer " + token, "platform": "open_platform"}
        method = method.upper()
        request_kwargs: Dict[str, Any] = {"params": query, "headers": headers}
        if method == "POST":
            headers["Content-Type"] = "application/json"
            request_kwargs["json"] = json or {}
        client = self._http.read() if method == "GET" else self._http.write()
        response = await client.request(method, OPEN_API_BASE + path, **request_kwargs)
        payload = safe_json(response)
        if is_openapi_token_invalid(response.status_code, payload) and not retried:
            await self._invalidate_token(token)
            return await self._request_once(method, path, query, json, retried=True)
        code = payload.get("code")
        if response.status_code >= 400 or code not in (0, 200, "0", None):
            # 把 status 和 payload 附在异常上，便于 request() 识别限流
            error_code = number_value(code) or response.status_code or 0
            raise Pan123Error(
                str(payload.get("message") or f"123 OpenAPI {response.status_code}"),
                code=error_code,
                data=payload,
            )
        return payload

    async def get_token(self) -> str:
        if self._access_token and self._access_token_expires_at > time.time() + 60:
            return self._access_token
        await self.load_authorization()
        if self._access_token and self._access_token_expires_at > time.time() + 60:
            return self._access_token
        if self._access_token_lock is None:
            self._access_token_lock = asyncio.Lock()
        async with self._access_token_lock:
            if self._access_token and self._access_token_expires_at > time.time() + 60:
                return self._access_token
            await self._refresh_tokens()
            return self._access_token

    async def _refresh_tokens(self) -> None:
        """用 refresh_token 换新 access_token（中转站持有 clientSecret）。"""
        if not self._refresh_token:
            raise Pan123Error("123 云盘尚未完成授权登录，请先到设置里绑定 123 账号")
        result = await self.broker.refresh(self._refresh_token)
        self._access_token = str(result.get("accessToken") or "")
        self._refresh_token = str(result.get("refreshToken") or self._refresh_token)
        if not self._access_token:
            raise Pan123Error("123 授权刷新未返回 accessToken")
        self._access_token_expires_at = pan123_open_token_expires_at(
            self._access_token, {"expiresIn": result.get("expiresIn")} if result.get("expiresIn") else None
        )
        # 新 token（以及可能轮换的 refresh_token）落盘，下次启动直接命中
        await self.persist()

    async def _invalidate_token(self, token: str) -> None:
        # 迟到的旧响应不能清掉新 token
        if self._access_token != token:
            return
        if self._access_token_lock is None:
            self._access_token_lock = asyncio.Lock()
        async with self._access_token_lock:
            if self._access_token != token:
                return
            # 只清内存里的 access_token；下次请求会自动用 refresh_token 换新
            self._access_token = ""
            self._access_token_expires_at = 0.0


def safe_json(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_open_user_info(value: Any) -> Dict[str, Any]:
    """把 123 OpenAPI /api/v1/user/info 的响应对齐到网页版 profile 字段。"""
    record = value if isinstance(value, dict) else {}
    uid = number_value(record.get("uid"), record.get("UID"), record.get("userId"), record.get("userID"))
    nickname = string_value(record.get("nickname"), record.get("Nickname"), record.get("username"), record.get("Nickname"))
    passport = string_value(record.get("passport"), record.get("phone"), record.get("mail"), record.get("email"))
    space_used = number_value(record.get("spaceUsed"), record.get("space_used"), record.get("usedSpace"))
    space_total = number_value(record.get("spacePermanent"), record.get("space_total"), record.get("totalSpace"))
    return {
        "uid": uid,
        "nickname": nickname or passport or (f"123-{uid}" if uid else "123 账号"),
        "headImage": string_value(record.get("headImage"), record.get("avatar"), record.get("headImg")),
        "passport": passport,
        "mail": string_value(record.get("mail"), record.get("email")),
        "spaceUsed": space_used,
        "spacePermanent": space_total,
        "spaceTemp": number_value(record.get("spaceTemp"), record.get("space_temp")),
        "spaceTempExpr": string_value(record.get("spaceTempExpr")),
        "vip": bool_value(record.get("vip"), record.get("isvip")),
        "directTraffic": number_value(record.get("directTraffic")),
        "isHideUID": None,
        "httpsCount": None,
    }


def parse_pan123_share_url(value: str) -> Dict[str, str]:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError as error:
        raise Pan123Error("123 分享链接格式无效") from error
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname or not pan123_share_hostname_allowed(hostname):
        raise Pan123Error("仅支持官方 123 网盘分享域名")
    parts = [part for part in parsed.path.split("/") if part]
    share_key = ""
    if len(parts) >= 3 and parts[0].lower() == "gsb" and parts[1].lower() == "s":
        share_key = parts[2]
    elif len(parts) >= 2 and parts[0].lower() in {"s", "ps", "123pan"}:
        share_key = parts[1]
    if not share_key:
        raise Pan123Error("123 分享链接中缺少分享 Key")
    share_key = re.sub(r"\.html$", "", share_key, flags=re.I).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", share_key):
        raise Pan123Error("123 分享 Key 格式无效")
    port = f":{parsed.port}" if parsed.port else ""
    origin = f"{parsed.scheme}://{hostname}{port}"
    query = parse_qs(parsed.query)
    password = str((query.get("pwd") or query.get("password") or query.get("code") or [""])[0])
    return {"origin": origin, "shareKey": share_key, "password": password}


def pan123_share_hostname_allowed(hostname: str) -> bool:
    host = str(hostname or "").lower().rstrip(".")
    return host in PAN_SHARE_PUBLIC_HOSTS or any(host.endswith(suffix) and host != suffix.lstrip(".") for suffix in PAN_SHARE_HOST_SUFFIXES)


def share_request_headers(referer: str) -> Dict[str, str]:
    return {
        "accept": "application/json",
        "accept-language": "zh-CN",
        "referer": str(referer or ""),
        "user-agent": PAN_LOGIN_USER_AGENT,
    }


def first_record_list(record: Dict[str, Any], keys: tuple[str, ...]) -> List[Dict[str, Any]]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def normalize_file(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    file_id = number_value(
        value.get("FileId"), value.get("fileId"), value.get("fileID"), value.get("FileID"), value.get("id")
    )
    name = string_value(
        value.get("FileName"), value.get("fileName"), value.get("filename"), value.get("Filename"), value.get("name")
    )
    if file_id is None or not name:
        return None
    file_type = number_value(value.get("Type"), value.get("type"), value.get("file_type")) or 0
    parent_file_id = number_value(value.get("ParentFileId"), value.get("ParentFileID"), value.get("parentFileId"))
    update_at = string_value(value.get("UpdateAt"), value.get("updateAt"), value.get("updatedAt"), value.get("updated_at"))
    create_at = string_value(value.get("CreateAt"), value.get("createAt"), value.get("createdAt"), value.get("created_at"))
    etag = string_value(value.get("Etag"), value.get("etag"), value.get("md5"))
    size = number_value(value.get("Size"), value.get("size"), value.get("BaseSize"), value.get("baseSize"), value.get("LiveSize"), value.get("liveSize"))
    parent_name = string_value(value.get("ParentName"), value.get("parentName"), value.get("NewParentName"), value.get("newParentName"))
    new_parent_name = string_value(value.get("NewParentName"), value.get("newParentName"))
    abs_path = string_value(value.get("AbsPath"), value.get("absPath"), value.get("path"))
    return {
        "id": str(file_id),
        "fileId": file_id,
        "name": name,
        "filename": name,
        "type": file_type,
        "size": size,
        "baseSize": number_value(value.get("BaseSize"), value.get("baseSize")),
        "liveSize": number_value(value.get("LiveSize"), value.get("liveSize")),
        "etag": etag,
        "parentFileId": parent_file_id,
        "parentName": parent_name,
        "newParentName": new_parent_name,
        "absPath": abs_path,
        "s3KeyFlag": string_value(value.get("S3KeyFlag"), value.get("s3KeyFlag"), value.get("s3keyFlag")),
        "driveId": number_value(value.get("DriveId"), value.get("driveId"), value.get("driveID")),
        "category": number_value(value.get("Category"), value.get("category")),
        "status": number_value(value.get("Status"), value.get("status")),
        "createAt": create_at,
        "updateAt": update_at,
        "raw": value,
    }


RELEASE_GROUP_EXT_RE = re.compile(r"(\.(?:mkv|mp4|avi|mov|wmv|flv|webm|m4v|mpeg|mpg|3gp|ts|m2ts|mts|ass|srt|ssa|zip|rar|7z))$", re.I)
RELEASE_GROUP_BRACKET_RE = re.compile(r"\s*\[[\u4e00-\u9fa5A-Za-z0-9][\u4e00-\u9fa5A-Za-z0-9._@-]{1,49}\]\s*$")
RELEASE_GROUP_SUFFIX_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9][\u4e00-\u9fa5A-Za-z0-9._@]{1,49}$")


def string_value(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def number_value(*values: Any) -> Optional[int]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def float_value(*values: Any) -> Optional[float]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


async def sleep_seconds(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def is_trashed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    trashed = number_value(value.get("trashed"), value.get("Trashed"))
    return bool(trashed)


def is_openapi_token_invalid(status: int, payload: Dict[str, Any]) -> bool:
    if status in (401, 403):
        return True
    if number_value(payload.get("code")) in (401, 403):
        return True
    message = str(payload.get("message") or "").strip()
    return bool(re.search(r"(token.*(expired|invalid|expire|过期|失效|无效)|(expired|invalid|expire|过期|失效|无效).*token)", message, re.I))


def pan123_open_jwt_exp(access_token: str) -> Optional[float]:
    parts = str(access_token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        payload_bytes = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        payload = json.loads(payload_bytes.decode("utf-8"))
        expiration = float(payload.get("exp")) if isinstance(payload, dict) and payload.get("exp") is not None else 0.0
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return expiration if expiration > 0 else None


def pan123_open_token_expires_at(
    access_token: str,
    response_body: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> float:
    current = float(now if now is not None else time.time())
    jwt_expiration = pan123_open_jwt_exp(access_token)
    if jwt_expiration is not None:
        return jwt_expiration

    body = response_body if isinstance(response_body, dict) else {}
    absolute = float_value(body.get("expiresAt"), body.get("expires_at"), body.get("expiration"))
    if absolute is not None:
        if absolute > 10_000_000_000:
            absolute /= 1000.0
        if absolute > 0:
            return absolute

    absolute_text = string_value(body.get("expiresAt"), body.get("expires_at"), body.get("expiration"))
    if absolute_text and not re.fullmatch(r"\d+(?:\.\d+)?", absolute_text):
        try:
            parsed = datetime.fromisoformat(absolute_text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass

    expires_in = float_value(body.get("expiresIn"), body.get("expires_in"))
    return current + max(60.0, expires_in if expires_in is not None else 86400.0)


def is_openapi_rate_limited(status: int, payload: Dict[str, Any]) -> bool:
    """识别 123 OpenAPI 的限流/操作频繁错误。"""
    if status in (429,):
        return True
    code = number_value(payload.get("code"))
    # 123 OpenAPI 部分接口用 42902/42903 等业务码表示限流
    if code in (429, 42902, 42903):
        return True
    message = str(payload.get("message") or "").strip()
    return bool(
        re.search(r"操作频繁|请稍后|访问频繁|请求过快|rate\s*limit|too\s*many", message, re.I)
    )


def bool_value(*values: Any) -> Optional[bool]:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
    return None


def normalize_offline_status(value: Any, process: float) -> int:
    raw = str(value or "").lower()
    if "fail" in raw or "error" in raw:
        return -1
    if "success" in raw or "complete" in raw or raw == "2" or process >= 100:
        return 2
    return 1


def chunks(values: List[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
