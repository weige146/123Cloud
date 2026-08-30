from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
import uuid
import weakref
import zlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


LOGIN_URL = "https://login.123pan.com/api/user/sign_in"
API_BASE = "https://api.123278.com"
OPEN_API_BASE = "https://open-api.123pan.com"
PAN_DEVICE_NAME = "Windows 版网页登录"
PAN_LOGIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 123XiaoZhuShou/1.0"
)
PAN_SHARE_APP_VERSION = "139"
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
    def __init__(self, timeout_seconds: float = 20.0):
        self.timeout = timeout_seconds
        self._http = _HttpClientPool(self.timeout)

    async def close(self) -> None:
        await self._http.close()

    async def login(self, user: str, password: str, remember: bool, login_uuid: Optional[str] = None) -> Dict[str, str]:
        login_uuid = login_uuid or uuid.uuid4().hex + uuid.uuid4().hex
        payload: Dict[str, Any]
        if "@" in user:
            payload = {"mail": user, "password": password, "type": 2, "deviceName": PAN_DEVICE_NAME}
        else:
            payload = {"passport": user, "password": password, "remember": bool(remember), "deviceName": PAN_DEVICE_NAME}

        client = self._http.write()
        response = await client.post(
                LOGIN_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": API_BASE,
                    "Referer": API_BASE + "/",
                    "User-Agent": PAN_LOGIN_USER_AGENT,
                    "platform": "web",
                    "app-version": "3",
                    "loginuuid": login_uuid,
                },
        )
        data = safe_json(response)
        if response.status_code >= 400:
            raise Pan123Error(str(data.get("message") or "123 登录接口请求失败"))
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        token = str(body.get("token") or "")
        if data.get("code") == 200 and token:
            return {
                "token": token,
                "loginUuid": normalize_login_uuid(body, login_uuid),
            }
        raise Pan123Error(str(data.get("message") or "登录失败"))

    async def get_user_info(self, session: Dict[str, Any]) -> Dict[str, Any]:
        token = str(session.get("token") or "")
        login_uuid = str(session.get("loginUuid") or "")
        if not token:
            raise Pan123Error("后端未登录 123 云盘")

        path = "/b/api/user/info"
        query = signed_query(path)
        client = self._http.read()
        response = await client.get(
            API_BASE + path,
            params=query,
            headers=auth_headers(token, login_uuid, has_body=False),
        )
        data = safe_json(response)
        code = data.get("code")
        if response.status_code >= 400 or code not in (0, 200, "0", None):
            raise Pan123Error(str(data.get("message") or "123 用户信息请求失败"))
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        raw = first_record(body, ("userInfo", "user", "info")) or body
        return normalize_user_info(raw, fallback_user=str(session.get("user") or ""))

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
                "ParentFileId": "0",
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
            items.extend(page_items)
            response_next = str(body.get("Next") or body.get("next") or body.get("LastFileId") or body.get("lastFileId") or "").strip()
            last_file_id = str((page_items[-1] if page_items else {}).get("FileId") or (page_items[-1] if page_items else {}).get("fileId") or "")
            if response_next in {"0", "-1"}:
                response_next = ""
            next_candidate = response_next or (last_file_id if len(page_items) >= 100 else "-1")
            if next_candidate == "-1" or next_candidate == next_token:
                break
            next_token = next_candidate
            page += 1
        return items

    async def create_share_copy_task(
        self,
        session: Dict[str, Any],
        share_url: str,
        share_password: str,
        target_dir_id: str,
        items: List[Dict[str, Any]],
    ) -> int:
        parsed = parse_pan123_share_url(share_url)
        path = "/b/api/restful/goapi/v1/file/copy/save"
        file_list = [share_copy_file(item, target_dir_id) for item in items]
        file_list = [item for item in file_list if item]
        if not file_list:
            raise Pan123Error("123 分享根目录中没有可转存的文件")
        data = await self._request_share_copy_api(
            session,
            parsed["origin"],
            path,
            "POST",
            {
                "fileList": file_list,
                "shareKey": parsed["shareKey"],
                "sharePwd": str(share_password or ""),
                "currentLevel": 0,
                "superAdmin": None,
            },
            referer=share_url,
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        task_id = number_value(body.get("taskID"), body.get("taskId")) or 0
        if task_id <= 0:
            raise Pan123Error("123 转存接口未返回任务 ID")
        return task_id

    async def get_share_copy_task(self, session: Dict[str, Any], share_url: str, task_id: int) -> Dict[str, Any]:
        parsed = parse_pan123_share_url(share_url)
        path = "/b/api/restful/goapi/v1/file/copy/save/get"
        data = await self._request_share_copy_api(
            session,
            parsed["origin"],
            path,
            "GET",
            {"taskID": str(task_id)},
            referer=share_url,
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        return {
            "taskId": number_value(body.get("taskID"), body.get("taskId")) or int(task_id),
            "status": number_value(body.get("status")) or 0,
            "errorCode": number_value(body.get("errorCode")) or 0,
            "reason": str(body.get("reason") or ""),
            "progress": str(body.get("progress") or ""),
        }

    async def _request_share_copy_api(
        self,
        session: Dict[str, Any],
        origin: str,
        path: str,
        method: str,
        payload: Dict[str, Any],
        referer: str,
    ) -> Dict[str, Any]:
        token = str(session.get("token") or "")
        login_uuid = str(session.get("loginUuid") or "")
        if not token:
            raise Pan123Error("后端未登录 123 云盘")
        request_method = method.upper()
        query = dict(payload) if request_method == "GET" else {}
        signed_path = path
        if request_method == "GET" and query:
            signed_path = f"{path}?{urlencode(query)}"
        query.update(signed_query(signed_path, app_version=PAN_SHARE_APP_VERSION))
        headers = auth_headers(token, login_uuid, has_body=request_method != "GET", app_version=PAN_SHARE_APP_VERSION)
        headers["referer"] = referer
        client = self._http.read() if request_method == "GET" else self._http.write()
        response = await client.request(
            request_method,
            f"{origin}{path}",
            params=query,
            json=payload if request_method != "GET" else None,
            headers=headers,
        )
        data = safe_json(response)
        code = number_value(data.get("code"))
        if response.status_code >= 400 or code not in (0, 200, None):
            raise Pan123Error(str(data.get("message") or "123 转存接口请求失败"), code=code or response.status_code)
        return data

    async def _request_web_api(
        self,
        session: Dict[str, Any],
        path: str,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        allowed_codes: Iterable[int] = (),
    ) -> Dict[str, Any]:
        token = str(session.get("token") or "")
        login_uuid = str(session.get("loginUuid") or "")
        if not token:
            raise Pan123Error("后端未登录 123 云盘")
        client = self._http.read() if method.upper() == "GET" else self._http.write()
        request_method = method.upper()
        params: Dict[str, Any] = signed_query(path)
        request_kwargs: Dict[str, Any] = {}
        if request_method == "GET":
            params.update(payload or {})
        else:
            request_kwargs["json"] = payload or {}
        response = await client.request(
            request_method,
            API_BASE + path,
            params=params,
            headers=auth_headers(token, login_uuid, has_body=request_method != "GET"),
            **request_kwargs,
        )
        data = safe_json(response)
        code = number_value(data.get("code"))
        accepted = {int(value) for value in allowed_codes}
        if response.status_code >= 400 or (code not in (0, 200, None) and code not in accepted):
            raise Pan123Error(
                str(data.get("message") or f"123 接口请求失败（HTTP {response.status_code}）"),
                code=code or response.status_code,
                data=data.get("data") if isinstance(data.get("data"), dict) else {},
            )
        return data

class Pan123OpenTokenStore:
    """Pan123OpenAPIClient 的 token 持久化适配器接口。

    适配 SessionStore 的 get/save/delete_pan123_open_token 三个方法，
    使 Pan123OpenAPIClient 不直接依赖 SessionStore。
    """

    async def load(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def save(self, access_token: str, expires_at: float) -> None:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


class Pan123OpenAPIClient:
    """Small 123 OpenAPI client kept ready for the gateway mode switch.

    Web login remains the default because 123 OpenAPI copy cannot cover folders
    consistently; folder copy continues to use the Web API path.
    """

    clientKind = "openapi"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 20.0,
        token_store: Optional["Pan123OpenTokenStore"] = None,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.timeout = timeout_seconds
        self.token_store = token_store
        self._http = _HttpClientPool(self.timeout)
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._access_token_lock: Optional[asyncio.Lock] = None
        # 进程重启后只懒加载一次外部 token_store
        self._cached_token_load: Optional[asyncio.Future] = None
        self._cached_token_loaded = False

    async def close(self) -> None:
        await self._http.close()

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
        # 进程重启后只懒加载一次外部 token_store
        cached = await self._load_cached_token()
        if cached:
            return cached
        if self._access_token_lock is None:
            self._access_token_lock = asyncio.Lock()
        async with self._access_token_lock:
            if self._access_token and self._access_token_expires_at > time.time() + 60:
                return self._access_token
            if not self.client_id or not self.client_secret:
                raise Pan123Error("请先配置 123 OpenAPI ClientID 和 ClientSecret")
            client = self._http.write()
            response = await client.post(
                OPEN_API_BASE + "/api/v1/access_token",
                json={"clientID": self.client_id, "clientSecret": self.client_secret},
                headers={"platform": "open_platform", "Content-Type": "application/json"},
            )
            payload = safe_json(response)
            code = payload.get("code")
            if response.status_code >= 400 or code not in (0, 200, "0", None):
                raise Pan123Error(str(payload.get("message") or f"123 OpenAPI Token {response.status_code}"))
            body = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            token = string_value(body.get("accessToken"), body.get("access_token"))
            if not token:
                raise Pan123Error("123 OpenAPI Token 响应缺少 accessToken")
            self._access_token = token
            self._access_token_expires_at = pan123_open_token_expires_at(token, body)
            # 写盘复用，下次启动可直接命中
            if self.token_store is not None:
                try:
                    await self.token_store.save(self._access_token, self._access_token_expires_at)
                except Exception:
                    pass
            return token

    async def _load_cached_token(self) -> Optional[str]:
        if self._cached_token_loaded or self.token_store is None:
            return None
        if self._cached_token_load is None:
            self._cached_token_load = asyncio.get_event_loop().create_future()
            try:
                cached = await self.token_store.load()
                if cached and cached.get("accessToken"):
                    self._access_token = str(cached["accessToken"])
                    stored_expires_at = float(cached["expiresAt"])
                    jwt_expires_at = pan123_open_jwt_exp(self._access_token)
                    self._access_token_expires_at = jwt_expires_at or stored_expires_at
                    if self._access_token_expires_at <= time.time() + 60:
                        self._access_token = ""
                        self._access_token_expires_at = 0.0
                        self._cached_token_load.set_result(None)
                        return None
                    if jwt_expires_at and abs(jwt_expires_at - stored_expires_at) >= 1:
                        try:
                            await self.token_store.save(self._access_token, self._access_token_expires_at)
                        except Exception:
                            pass
                    self._cached_token_load.set_result(self._access_token)
                    return self._access_token
                self._cached_token_load.set_result(None)
                return None
            except Exception:
                self._cached_token_load.set_result(None)
                return None
            finally:
                self._cached_token_loaded = True
        return await self._cached_token_load

    async def _invalidate_token(self, token: str) -> None:
        # 迟到的旧响应不能清掉新 token
        if self._access_token != token:
            return
        if self._access_token_lock is None:
            self._access_token_lock = asyncio.Lock()
        async with self._access_token_lock:
            if self._access_token != token:
                return
            self._access_token = ""
            self._access_token_expires_at = 0.0
            self._cached_token_loaded = True  # 失效后不再懒加载
            if self.token_store is not None:
                try:
                    await self.token_store.clear()
                except Exception:
                    pass


def safe_json(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_login_uuid(data: Dict[str, Any], fallback: str) -> str:
    for key in ("loginuuid", "loginUuid", "loginUUID", "uuid", "login_uuid"):
        value = data.get(key)
        if value:
            return str(value)
    return fallback


def auth_headers(token: str, login_uuid: str, has_body: bool, app_version: str = "3") -> Dict[str, str]:
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN",
        "app-version": str(app_version or "3"),
        "authorization": "Bearer " + token,
        "loginuuid": login_uuid,
        "platform": "web",
        "user-agent": "Mozilla/5.0",
    }
    if has_body:
        headers["content-type"] = "application/json;charset=UTF-8"
    return headers


def signed_query(
    path: str,
    now_ms: Optional[int] = None,
    random_value: Optional[int] = None,
    app_version: str = "3",
) -> Dict[str, str]:
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    random_value = random_value if random_value is not None else random.randint(0, 10_000_000)
    cst = datetime.fromtimestamp(now_ms / 1000, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    ds = cst.strftime("%Y%m%d%H%M")
    mapper = "adefghlmyijnopkqrstubcvwsz" if str(app_version) == PAN_SHARE_APP_VERSION else "adefghlimjnoopkqrstubcvwsz"
    mapped = "".join(mapper[int(ch)] if ch.isdigit() else ch for ch in ds)
    tsgn = str(crc32_text(mapped))
    ts = timestamp_text(now_ms)
    rnd = str(random_value)
    dat = "|".join([ts, rnd, path, "web", str(app_version or "3"), tsgn])
    return {tsgn: "{}-{}-{}".format(ts, rnd, crc32_text(dat))}


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
    password = str((query.get("pwd") or [""])[0])
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


def share_copy_file(item: Dict[str, Any], target_dir_id: str) -> Optional[Dict[str, Any]]:
    file_id = number_value(item.get("FileId"), item.get("fileId"), item.get("FileID"), item.get("id")) or 0
    if file_id <= 0:
        return None
    return {
        "fileID": file_id,
        "size": number_value(item.get("Size"), item.get("BaseSize"), item.get("size")) or 0,
        "etag": str(item.get("Etag") or item.get("etag") or ""),
        "type": number_value(item.get("Type"), item.get("type")) or 0,
        "parentFileID": number_value(target_dir_id) or 0,
        "fileName": str(item.get("FileName") or item.get("filename") or item.get("name") or file_id),
        "driveID": number_value(item.get("DriveId"), item.get("driveId"), item.get("driveID")) or 0,
    }


def timestamp_text(now_ms: int) -> str:
    seconds = now_ms // 1000
    millis = now_ms % 1000
    if millis == 0:
        return str(seconds)
    return "{}.{}".format(seconds, str(millis).zfill(3).rstrip("0"))


def crc32_text(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


def normalize_file(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    file_id = number_value(value.get("FileId"), value.get("fileId"), value.get("fileID"), value.get("id"))
    name = string_value(value.get("FileName"), value.get("fileName"), value.get("filename"), value.get("name"))
    if file_id is None or not name:
        return None
    file_type = number_value(value.get("Type"), value.get("type"), value.get("file_type")) or 0
    parent_file_id = number_value(value.get("ParentFileId"), value.get("ParentFileID"), value.get("parentFileId"))
    update_at = string_value(value.get("UpdateAt"), value.get("updateAt"), value.get("updatedAt"), value.get("updated_at"))
    create_at = string_value(value.get("CreateAt"), value.get("createAt"), value.get("createdAt"), value.get("created_at"))
    etag = string_value(value.get("Etag"), value.get("etag"))
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
        "s3KeyFlag": string_value(value.get("S3KeyFlag"), value.get("s3KeyFlag")),
        "category": number_value(value.get("Category"), value.get("category")),
        "status": number_value(value.get("Status"), value.get("status")),
        "createAt": create_at,
        "updateAt": update_at,
        "raw": value,
    }


RELEASE_GROUP_EXT_RE = re.compile(r"(\.(?:mkv|mp4|avi|mov|wmv|flv|webm|m4v|mpeg|mpg|3gp|ts|m2ts|mts|ass|srt|ssa|zip|rar|7z))$", re.I)
RELEASE_GROUP_BRACKET_RE = re.compile(r"\s*\[[\u4e00-\u9fa5A-Za-z0-9][\u4e00-\u9fa5A-Za-z0-9._@-]{1,49}\]\s*$")
RELEASE_GROUP_SUFFIX_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9][\u4e00-\u9fa5A-Za-z0-9._@]{1,49}$")


def normalize_user_info(value: Any, fallback_user: str = "") -> Dict[str, Any]:
    record = value if isinstance(value, dict) else {}
    nickname = string_value(
        record.get("nickname"),
        record.get("Nickname"),
        record.get("nickName"),
        record.get("NickName"),
        record.get("name"),
    )
    passport = string_value(record.get("passport"), record.get("Passport"), record.get("phone"), fallback_user)
    mail = string_value(record.get("mail"), record.get("Mail"), record.get("email"))
    return {
        "uid": number_value(record.get("uid"), record.get("UID"), record.get("userId"), record.get("userID")),
        "nickname": nickname or passport or mail or fallback_user,
        "headImage": string_value(
            record.get("headImage"),
            record.get("HeadImage"),
            record.get("headImg"),
            record.get("HeadImg"),
            record.get("avatar"),
        ),
        "passport": passport or fallback_user,
        "mail": mail,
        "spaceUsed": number_value(record.get("spaceUsed"), record.get("SpaceUsed")),
        "spacePermanent": number_value(record.get("spacePermanent"), record.get("SpacePermanent")),
        "spaceTemp": number_value(record.get("spaceTemp"), record.get("SpaceTemp")),
        "spaceTempExpr": string_value(record.get("spaceTempExpr"), record.get("SpaceTempExpr")),
        "vip": bool_value(record.get("vip"), record.get("Vip")),
        "directTraffic": number_value(record.get("directTraffic"), record.get("DirectTraffic")),
        "isHideUID": bool_value(record.get("isHideUID"), record.get("isHideUid"), record.get("hideUID")),
        "httpsCount": number_value(record.get("httpsCount"), record.get("HttpsCount")),
    }


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


def first_record(record: Dict[str, Any], keys: tuple[str, ...]) -> Optional[Dict[str, Any]]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return None


def chunks(values: List[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
