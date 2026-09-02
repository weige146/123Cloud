import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Set, TypedDict

import httpx
from urllib.parse import parse_qs, quote, urlparse, urlencode


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_SHARE_RE = re.compile(
    r"https?://(?:www\.)?(?:115cdn\.com|115\.com)/s/[^\s<>\"']+", re.IGNORECASE
)
_CODE_RE = re.compile(
    r"(?:提取码|访问码|密码|password|receive[_\s-]?code|code|pwd)[=：:\s]*([A-Za-z0-9]{4,8})",
    re.IGNORECASE,
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Safari/537.36 Chrome/142.0.0.0 OpenList/425.6.30"
)
PAN123_OFFLINE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_SHARE_SNAP_URL = "https://webapi.115.com/share/snap"
_SHARE_DOWN_WEB_URL = "https://webapi.115.com/share/downurl"
_SHARE_DOWN_APP_URL = "https://proapi.115.com/app/share/downurl"
_SHARE_DOWN_CHROME_URL = "https://proapi.115.com/2.0/share/downurl"
_LOCAL_FILES_URL = "https://webapi.115.com/files"
_LOCAL_GETID_URL = "https://webapi.115.com/files/getid"
_LOCAL_DOWN_CHROME_URL = "https://proapi.115.com/app/chrome/downurl"
_LOCAL_DELETE_URL = "https://webapi.115.com/rb/delete"
_USER_INFO_URL = "https://my.115.com/?ct=ajax&ac=get_user_aq"
# 分享目录展开的页间节流与瞬时错误重试参数；目录多的分享连发请求会触发 115 风控
PAN115_SHARE_LIST_INTERVAL_MS = int(os.environ.get("PAN115_SHARE_LIST_INTERVAL_MS", "300"))
PAN115_SHARE_LIST_MAX_ATTEMPTS = int(os.environ.get("PAN115_SHARE_LIST_MAX_ATTEMPTS", "3"))
PAN115_SHARE_LIST_RETRY_BASE_MS = int(os.environ.get("PAN115_SHARE_LIST_RETRY_BASE_MS", "2000"))

_G_KEY_L = bytes([0x78, 0x06, 0xAD, 0x4C, 0x33, 0x86, 0x5D, 0x18, 0x4C, 0x01, 0x3F, 0x46])
_RSA_RAND_KEY = bytes(16)
_RSA_KEY = bytes([0x8D, 0xA5, 0xA5, 0x8D])
_G_KTS = bytes(
    [
        0xF0, 0xE5, 0x69, 0xAE, 0xBF, 0xDC, 0xBF, 0x8A, 0x1A, 0x45, 0xE8, 0xBE, 0x7D, 0xA6, 0x73, 0xB8,
        0xDE, 0x8F, 0xE7, 0xC4, 0x45, 0xDA, 0x86, 0xC4, 0x9B, 0x64, 0x8B, 0x14, 0x6A, 0xB4, 0xF1, 0xAA,
        0x38, 0x01, 0x35, 0x9E, 0x26, 0x69, 0x2C, 0x86, 0x00, 0x6B, 0x4F, 0xA5, 0x36, 0x34, 0x62, 0xA6,
        0x2A, 0x96, 0x68, 0x18, 0xF2, 0x4A, 0xFD, 0xBD, 0x6B, 0x97, 0x8F, 0x4D, 0x8F, 0x89, 0x13, 0xB7,
        0x6C, 0x8E, 0x93, 0xED, 0x0E, 0x0D, 0x48, 0x3E, 0xD7, 0x2F, 0x88, 0xD8, 0xFE, 0xFE, 0x7E, 0x86,
        0x50, 0x95, 0x4F, 0xD1, 0xEB, 0x83, 0x26, 0x34, 0xDB, 0x66, 0x7B, 0x9C, 0x7E, 0x9D, 0x7A, 0x81,
        0x32, 0xEA, 0xB6, 0x33, 0xDE, 0x3A, 0xA9, 0x59, 0x34, 0x66, 0x3B, 0xAA, 0xBA, 0x81, 0x60, 0x48,
        0xB9, 0xD5, 0x81, 0x9C, 0xF8, 0x6C, 0x84, 0x77, 0xFF, 0x54, 0x78, 0x26, 0x5F, 0xBE, 0xE8, 0x1E,
        0x36, 0x9F, 0x34, 0x80, 0x5C, 0x45, 0x2C, 0x9B, 0x76, 0xD5, 0x1B, 0x8F, 0xCC, 0xC3, 0xB8, 0xF5,
    ]
)
_RSA_N = int(
    "0x8686980c0f5a24c4b9d43020cd2c22703ff3f450756529058b1cf88f09b8602136477198a6e2683149659bd122c33592fdb5ad47944ad1ea4d36c6b172aad6338c3bb6ac6227502d010993ac967d1aef00f0c8e038de2e4d3bc2ec368af2e9f10a6f1eda4f7262f136420c07c331b871bf139f74f3010e3c4fe57df3afb71683",
    16,
)
_RSA_E = 0x10001


# ---------------------------------------------------------------------------
# 类型定义
# ---------------------------------------------------------------------------
class TransferFile(TypedDict):
    id: str
    name: str
    size: int
    sha1: Optional[str]
    path: List[str]
    status: str
    parentDirId: Optional[str]


class Pan115ShareLink(TypedDict):
    url: str
    clean_url: str
    share_code: str
    receive_code: Optional[str]


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------
def extract_115_links(text: str) -> List[Pan115ShareLink]:
    links: List[Pan115ShareLink] = []
    seen: set = set()

    for match in _SHARE_RE.finditer(text):
        raw = _trim_link(match.group(0))
        share_code = _extract_share_code(raw)
        if not share_code or share_code in seen:
            continue
        seen.add(share_code)

        receive_code = None
        try:
            url = urlparse(raw)
            params = parse_qs(url.query)
            receive_code = (
                params.get("password", [None])[0]
                or params.get("pwd", [None])[0]
                or params.get("code", [None])[0]
            )
        except Exception:
            m = _CODE_RE.search(raw)
            receive_code = m.group(1) if m else None

        if receive_code is None:
            m = _CODE_RE.search(text)
            receive_code = m.group(1) if m else None

        clean_url = f"https://115cdn.com/s/{share_code}"
        if receive_code:
            clean_url += f"?password={quote(receive_code)}"

        links.append(
            {
                "url": raw,
                "clean_url": clean_url,
                "share_code": share_code,
                "receive_code": receive_code,
            }
        )

    return links


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class Pan115TransferClient:
    def __init__(self, cookie: str, list_interval_ms: int = PAN115_SHARE_LIST_INTERVAL_MS):
        self._cookie = cookie
        # 115 大分享单页响应大且慢，httpx 默认 5 秒读超时会直接掐断解析阶段
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        self._list_interval_ms = max(0, int(list_interval_ms))
        self._last_list_at = 0.0
        self._cached_user_id = ""

    async def close(self) -> None:
        await self._client.aclose()

    async def inspect_and_flatten(
        self, link: Pan115ShareLink
    ) -> Dict[str, Any]:
        if not self._cookie.strip():
            raise ValueError("请先配置 115 Cookie")
        if not link.get("receive_code"):
            raise ValueError("115 分享缺少提取码；请在同一条消息里带上提取码")

        files: List[TransferFile] = []
        share_code = link["share_code"]
        receive_code = link["receive_code"]
        root = await self._list(share_code, receive_code, "", 1000, 0)

        root_data = root.get("data") or {}
        title = (root_data.get("shareinfo") or {}).get("share_title")
        actual_receive_code = (root_data.get("shareinfo") or {}).get("receive_code") or receive_code

        # 根目录与子目录统一走翻页遍历，避免根条目超过 1000 时被静默截断
        async def visit(dir_id: str, path: List[str], first_page: Optional[Dict[str, Any]] = None) -> None:
            nonlocal files
            offset = 0
            page = first_page
            while True:
                if page is None:
                    page = await self._list(share_code, actual_receive_code, dir_id, 1000, offset)
                page_data = page.get("data") or {}
                page_list = page_data.get("list") or []
                for item in page_list:
                    name = str(item.get("n") or "").strip()
                    if not name:
                        continue
                    is_dir = int(item.get("fc", 1)) == 0
                    if is_dir:
                        child_dir_id = str(item.get("cid") or item.get("fid") or "")
                        if child_dir_id:
                            await visit(child_dir_id, [*path, name])
                        continue
                    file_id = str(item.get("fid") or "")
                    if not file_id:
                        continue
                    files.append(
                        {
                            "id": file_id,
                            "name": name,
                            "size": int(item.get("s") or 0),
                            "sha1": str(item.get("sha")).lower() if item.get("sha") else None,
                            "path": path,
                            "status": "pending",
                        }
                    )
                offset += len(page_list)
                total_count = int(page_data.get("count") or len(page_list))
                if not page_list or offset >= total_count:
                    break
                page = None

        await visit("", [], root)
        return {"title": title, "receive_code": actual_receive_code, "files": files}

    async def inspect_local_path(
        self,
        path_115: str,
        exclude_suffixes: Optional[Iterable[str]] = None,
        exclude_cids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not self._cookie.strip():
            raise ValueError("请先配置 115 Cookie")

        normalized_path = _normalize_115_local_path(path_115)
        root_cid = await self.get_local_dir_id(normalized_path)
        suffixes = _normalize_suffixes(exclude_suffixes or [])
        skipped_cids = {str(value).strip() for value in (exclude_cids or []) if str(value).strip()}
        files: List[TransferFile] = []
        dir_map: Dict[str, Dict[str, Any]] = {}

        async def visit(dir_id: str, path: List[str], parent_dir_id: str) -> None:
            if str(dir_id) in skipped_cids:
                return
            dir_map[str(dir_id)] = {
                "cid": str(dir_id),
                "parentCid": str(parent_dir_id),
                "name": path[-1] if path else "",
                "path": path,
            }
            offset = 0
            while True:
                page = await self.list_local_dir(dir_id, 1000, offset)
                items = _local_list_items(page)
                for item in items:
                    name = _local_item_name(item)
                    if not name:
                        continue
                    if _local_item_is_dir(item):
                        child_dir_id = _local_item_dir_id(item)
                        if child_dir_id:
                            await visit(child_dir_id, [*path, name], dir_id)
                        continue
                    if suffixes and _file_suffix(name) in suffixes:
                        continue
                    file_id = _local_item_file_id(item)
                    if not file_id:
                        continue
                    file: TransferFile = {
                        "id": file_id,
                        "name": name,
                        "size": _int_value(
                            item.get("s")
                            or item.get("fs")
                            or item.get("file_size")
                            or item.get("size")
                            or 0
                        ),
                        "sha1": _local_item_sha1(item) or None,
                        "path": path,
                        "status": "pending",
                        "parentDirId": dir_id,
                    }
                    pick_code = _local_item_pickcode(item)
                    if pick_code:
                        file["pickCode"] = pick_code  # type: ignore[typeddict-unknown-key]
                    file["sourceType"] = "115_local"  # type: ignore[typeddict-unknown-key]
                    file["localFileId"] = file_id  # type: ignore[typeddict-unknown-key]
                    files.append(file)
                offset += len(items)
                count = _local_list_count(page, len(items))
                if not items or offset >= count:
                    break

        await visit(root_cid, [], "")
        return {
            "title": _local_path_title(normalized_path),
            "local_path": normalized_path,
            "root_cid": root_cid,
            "files": files,
            "dir_map": dir_map,
        }

    async def get_local_dir_id(self, path_115: str) -> str:
        normalized_path = _normalize_115_local_path(path_115)
        if normalized_path.startswith("cid:"):
            cid = normalized_path[4:].strip()
            if not cid:
                raise ValueError("115 本地盘目录不存在：cid:")
            return cid
        if normalized_path in {"", "/"}:
            return "0"
        data = await self._request_json(_LOCAL_GETID_URL, "115 本地盘目录解析", params={"path": normalized_path.strip("/")})
        file_id = data.get("id")
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        file_id = file_id or payload.get("file_id") or payload.get("id") or payload.get("cid")
        if not str(file_id or "").strip():
            raise ValueError(f"115 本地盘目录不存在：{normalized_path}")
        return str(file_id)

    async def list_local_dir(self, dir_id: str, limit: int, offset: int) -> Dict[str, Any]:
        return await self._request_json(
            _LOCAL_FILES_URL,
            "115 本地盘列表",
            params={
                "aid": "1",
                "cid": str(dir_id or "0"),
                "limit": str(limit),
                "offset": str(offset),
                "show_dir": "1",
                "count_folders": "1",
                "record_open_time": "1",
                "format": "json",
            },
        )

    async def get_local_download_url(self, pick_code: str) -> str:
        pick_code = str(pick_code or "").strip()
        if not pick_code:
            raise ValueError("115 本地盘文件缺少 pickcode，无法取直链")
        payload = {
            "pickcode": pick_code,
            "user_id": await self.get_user_id(),
        }
        response = await self._client.post(
            _LOCAL_DOWN_CHROME_URL,
            data={"data": pan115_rsa_encrypt(json.dumps(payload, ensure_ascii=False))},
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "cookie": self._cookie,
                "user-agent": PAN123_OFFLINE_USER_AGENT,
            },
        )
        raw = await _read_json_response(response, "115 本地盘取直链")
        _assert_pan115_ok(response, raw, "115 本地盘取直链")
        data = raw.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(pan115_rsa_decrypt(data))
            except Exception:
                pass
        download_url = _extract_download_url(data)
        if not download_url:
            raise ValueError("115 本地盘未返回文件直链")
        if _is_private_download_url(download_url):
            raise ValueError("115 返回了局域网直链，已跳过")
        return download_url

    async def delete_local_files(self, file_ids: Iterable[str]) -> None:
        ids = [str(value).strip() for value in file_ids if str(value).strip()]
        if not ids:
            return
        form: Dict[str, str] = {"ignore_warn": "1"}
        for index, file_id in enumerate(ids):
            form[f"fid[{index}]"] = file_id
        data = await self._request_json(
            _LOCAL_DELETE_URL,
            "115 本地盘删除源文件",
            method="POST",
            data=form,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        if data.get("state") is False:
            raise ValueError(data.get("error") or data.get("message") or "115 本地盘删除源文件失败")

    async def get_user_id(self) -> str:
        if self._cached_user_id:
            return self._cached_user_id
        match = re.search(r"(?:^|;\s*)UID=([^;]+)", self._cookie, re.IGNORECASE)
        if match:
            self._cached_user_id = match.group(1)
            return self._cached_user_id
        data = await self._request_json(_USER_INFO_URL, "115 用户信息")
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        user_id = data.get("uid") or data.get("user_id") or payload.get("uid") or payload.get("user_id") or payload.get("id")
        if not str(user_id or "").strip():
            raise ValueError("115 Cookie 中缺少 UID，且用户信息接口未返回用户 ID")
        self._cached_user_id = str(user_id)
        return self._cached_user_id

    async def get_download_url(
        self, share_code: str, receive_code: str, file_id: str
    ) -> str:
        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": file_id,
            "dl": "1",
        }

        errors: List[str] = []
        fetchers = [
            lambda: self._fetch_app_download_url(payload, share_code, receive_code),
            lambda: self._fetch_chrome_download_url(payload, share_code, receive_code),
            lambda: self._fetch_web_download_url(payload, share_code, receive_code),
        ]

        for fetcher in fetchers:
            try:
                download_url = await fetcher()
                if not download_url:
                    continue
                if _is_private_download_url(download_url):
                    errors.append("115 返回了局域网直链，已跳过")
                    continue
                return download_url
            except Exception as error:
                errors.append(str(error))

        raise ValueError("；".join(errors) or "115 未返回文件直链")

    # -----------------------------------------------------------------------
    # 私有方法
    # -----------------------------------------------------------------------
    async def _fetch_app_download_url(
        self, payload: Dict[str, str], share_code: str, receive_code: str
    ) -> str:
        response = await self._client.post(
            _SHARE_DOWN_APP_URL,
            data={"data": pan115_rsa_encrypt(json.dumps(payload))},
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "cookie": self._cookie,
                "referer": f"https://115.com/s/{share_code}?password={receive_code}&",
                "user-agent": _USER_AGENT,
            },
        )
        raw = await _read_json_response(response, "115 app downurl")
        if (
            not response.is_success
            or raw.get("state") is False
            or (raw.get("errno") is not None and raw.get("errno") != 0)
        ):
            errno = raw.get("errno")
            message = raw.get("error") or raw.get("message") or f"115 app downurl {response.status_code}"
            if errno is not None and str(errno) != "0":
                message = f"[errno {errno}] {message}"
            raise ValueError(message)

        data = raw.get("data")
        if isinstance(data, str):
            data = json.loads(pan115_rsa_decrypt(data))
        return _extract_download_url(data)

    async def _fetch_chrome_download_url(
        self, payload: Dict[str, str], share_code: str, receive_code: str
    ) -> str:
        url = f"{_SHARE_DOWN_CHROME_URL}?{urlencode(payload)}"
        data = await self._fetch_json(url, share_code, receive_code)
        return _extract_download_url(data.get("data"))

    async def _fetch_web_download_url(
        self, payload: Dict[str, str], share_code: str, receive_code: str
    ) -> str:
        url = f"{_SHARE_DOWN_WEB_URL}?{urlencode(payload)}"
        data = await self._fetch_json(url, share_code, receive_code)
        return _extract_download_url(data.get("data"))

    async def _list(
        self, share_code: str, receive_code: str, dir_id: str, limit: int, offset: int
    ) -> Dict[str, Any]:
        params = {
            "share_code": share_code,
            "receive_code": receive_code,
            "cid": dir_id,
            "limit": str(limit),
            "asc": "0",
            "offset": str(offset),
            "format": "json",
        }
        url = f"{_SHARE_SNAP_URL}?{urlencode(params)}"
        # 页间节流 + 瞬时错误（超时/风控网页/频繁）指数退避重试；超时与风控在 5 秒默认
        # 超时时代是"大分享发完就失败"的直接原因
        await self._throttle_share_list()
        last_error: Exception = ValueError("115 分享列表未请求")
        for attempt in range(1, PAN115_SHARE_LIST_MAX_ATTEMPTS + 1):
            try:
                data = await self._fetch_json(url, share_code, receive_code)
                if not data.get("data"):
                    raise ValueError(data.get("error") or data.get("message") or "115 分享列表为空或无权限访问")
                return data
            except Exception as error:
                last_error = error
                if attempt >= PAN115_SHARE_LIST_MAX_ATTEMPTS or not _is_transient_share_list_error(error):
                    raise
                await asyncio.sleep(PAN115_SHARE_LIST_RETRY_BASE_MS * attempt / 1000)
                await self._throttle_share_list()
        raise last_error

    async def _throttle_share_list(self) -> None:
        if self._list_interval_ms <= 0:
            return
        elapsed_ms = (time.monotonic() - self._last_list_at) * 1000
        wait_ms = self._list_interval_ms - elapsed_ms
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)
        self._last_list_at = time.monotonic()

    async def _fetch_json(self, url: str, share_code: str, receive_code: str) -> Dict[str, Any]:
        response = await self._client.get(
            url,
            headers={
                "accept": "application/json",
                "cookie": self._cookie,
                "referer": f"https://115.com/s/{share_code}?password={receive_code}&",
                "user-agent": _USER_AGENT,
            },
        )
        data = await _read_json_response(response, _pan115_api_label(url))
        if (
            not response.is_success
            or data.get("state") is False
            or (data.get("errno") is not None and data.get("errno") != 0)
        ):
            errno = data.get("errno")
            message = data.get("error") or data.get("message") or f"115 API {response.status_code}"
            if errno is not None and str(errno) != "0":
                message = f"[errno {errno}] {message}"
            raise ValueError(message)
        return data

    async def _request_json(
        self,
        url: str,
        label: str,
        method: str = "GET",
        params: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        response = await self._client.request(
            method,
            url,
            params=params,
            data=data,
            headers={
                "accept": "application/json, text/plain, */*",
                "cookie": self._cookie,
                "user-agent": _USER_AGENT,
                **(headers or {}),
            },
        )
        payload = await _read_json_response(response, label)
        _assert_pan115_ok(response, payload, label)
        return payload


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def _read_json_response(response: httpx.Response, label: str) -> Any:
    text = response.text
    trimmed = text.strip()
    if not trimmed:
        raise ValueError(f"{label} 返回空响应（HTTP {response.status_code}）")
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        if re.match(r"<!doctype|<html", trimmed, re.IGNORECASE):
            raise ValueError(
                f"{label} 返回了网页页面（HTTP {response.status_code}），可能是 115 Cookie 失效、需要验证或接口临时风控"
            )
        raise ValueError(f"{label} 返回的不是 JSON（HTTP {response.status_code}）")


def _assert_pan115_ok(response: httpx.Response, payload: Dict[str, Any], label: str) -> None:
    errno = payload.get("errno")
    if response.is_success and payload.get("state") is not False and (errno is None or str(errno) == "0"):
        return
    message = payload.get("error") or payload.get("message") or f"{label} {response.status_code}"
    if errno is not None and str(errno) != "0":
        message = f"[errno {errno}] {message}"
    raise ValueError(message)


# 115 账号（Cookie）失效的典型特征：未登录错误码 990001、"需要登录"类文案
_PAN115_EXPIRED_ERROR_RE = re.compile(
    r"\[errno\s*990001?\]|需要登录|请重新登录|未登录|登录已失效|登录失效|登录状态失效|账号未登录|请登录",
    re.IGNORECASE,
)
# 瞬时错误：限流/风控网页/超时，可重试；网页页面也可能是临时风控，先重试再换号
_PAN115_TRANSIENT_ERROR_RE = re.compile(
    r"操作频繁|请稍后|频繁|too many|rate|429|错误页|返回了网页页面|ReadTimeout|timed? ?out|timeout",
    re.IGNORECASE,
)
# 网页页面（登录页/验证页）：对该账号应冷却停用，换其他账号
_PAN115_PAGE_ERROR_RE = re.compile(r"返回了网页页面|需要验证|登录页", re.IGNORECASE)
# 分享本身已失效（取消/不存在/过期）：换号、冷却账号都无意义，与账号健康无关；
# 例如 [errno 4100010] 分享已取消，可能混在多接口拼接的复合报错里
_PAN115_SHARE_GONE_ERROR_RE = re.compile(
    r"\[errno\s*4100010\]|分享已取消|分享已被取消|分享不存在|分享已过期",
    re.IGNORECASE,
)


def _is_transient_share_list_error(error: Exception) -> bool:
    message = str(error)
    if _PAN115_EXPIRED_ERROR_RE.search(message):
        return False
    return bool(_PAN115_TRANSIENT_ERROR_RE.search(message))


def classify_pan115_account_error(error: Exception) -> str:
    """分类 115 账号错误：expired（Cookie 失效/账号不可用）/ transient（限流风控）/ share_gone（分享失效）/ other。"""
    message = str(error)
    if _PAN115_SHARE_GONE_ERROR_RE.search(message):
        # 分享取消/不存在时复合报错里可能同时出现"返回了网页页面"，须先判分享失效，避免误冷却账号
        return "share_gone"
    if _PAN115_EXPIRED_ERROR_RE.search(message) or _PAN115_PAGE_ERROR_RE.search(message):
        return "expired"
    if _PAN115_TRANSIENT_ERROR_RE.search(message):
        return "transient"
    return "other"


def _pan115_api_label(url_str: str) -> str:
    parsed = urlparse(url_str)
    if "/share/snap" in parsed.path:
        return "115 分享列表"
    if "/share/downurl" in parsed.path:
        return "115 取直链"
    return "115 API"


def _trim_link(value: str) -> str:
    return re.sub(r"[)，。；;,\])]+$", "", value)


def _extract_share_code(value: str) -> Optional[str]:
    try:
        url = urlparse(value)
        parts = [p for p in url.path.split("/") if p]
        if not parts or parts[0] != "s":
            return None
        return parts[1] if len(parts) > 1 else None
    except Exception:
        m = re.search(r"/s/([A-Za-z0-9]+)", value, re.IGNORECASE)
        return m.group(1) if m else None


def _extract_download_url(data: Any) -> str:
    return _find_http_url(data, 0)


def _normalize_115_local_path(path: str) -> str:
    value = str(path or "").strip()
    if not value or value == "0":
        return "/"
    explicit_cid = re.match(r"^(?:cid|id):\s*(\d+)$", value, re.IGNORECASE)
    if explicit_cid:
        return f"cid:{explicit_cid.group(1)}"
    if value.isdigit():
        return f"cid:{value}"
    return "/" + value.strip("/")


def _local_path_title(path: str) -> str:
    normalized = _normalize_115_local_path(path)
    if normalized.startswith("cid:"):
        return f"115 本地盘 CID {normalized[4:]}"
    if normalized == "/":
        return "115 本地盘"
    return normalized.rstrip("/").rsplit("/", 1)[-1] or "115 本地盘"


def _local_list_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("list", "data", "items", "files"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    for key in ("list", "items", "files"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _local_list_count(payload: Dict[str, Any], fallback: int) -> int:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (payload.get("count"), payload.get("file_count"), data.get("count"), data.get("file_count"), data.get("total")):
        number = _int_value(value, -1)
        if number >= 0:
            return number
    return fallback


def _local_item_name(item: Dict[str, Any]) -> str:
    return str(item.get("n") or item.get("fn") or item.get("file_name") or item.get("name") or "").strip()


def _local_item_is_dir(item: Dict[str, Any]) -> bool:
    if item.get("fc") is not None:
        return _int_value(item.get("fc"), 1) == 0
    if item.get("sha") is not None:
        return not str(item.get("sha") or "").strip()
    if item.get("sha1") is not None:
        return not str(item.get("sha1") or "").strip()
    if item.get("file_sha1") is not None:
        return not str(item.get("file_sha1") or "").strip()
    return not any(item.get(key) for key in ("fid", "file_id", "s", "fs", "file_size", "size"))


def _local_item_dir_id(item: Dict[str, Any]) -> str:
    return str(item.get("cid") or item.get("category_id") or item.get("fid") or item.get("file_id") or item.get("id") or "").strip()


def _local_item_file_id(item: Dict[str, Any]) -> str:
    return str(item.get("fid") or item.get("file_id") or item.get("id") or "").strip()


def _local_item_sha1(item: Dict[str, Any]) -> str:
    return str(item.get("sha") or item.get("sha1") or item.get("file_sha1") or "").strip().lower()


def _local_item_pickcode(item: Dict[str, Any]) -> str:
    return str(item.get("pc") or item.get("pick_code") or item.get("pickcode") or "").strip()


def _normalize_suffixes(values: Iterable[str]) -> Set[str]:
    suffixes: Set[str] = set()
    for value in values:
        for part in re.split(r"[\s,;，；]+", str(value or "")):
            cleaned = part.strip().lower().lstrip(".")
            if cleaned:
                suffixes.add(cleaned)
    return suffixes


def _file_suffix(name: str) -> str:
    value = str(name or "").rsplit(".", 1)
    return value[1].lower() if len(value) == 2 else ""


def _int_value(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback


def _find_http_url(value: Any, depth: int) -> str:
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if re.match(r"^https?://", trimmed, re.IGNORECASE) else ""
    if isinstance(value, list):
        for item in value:
            found = _find_http_url(item, depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("url", "download_url", "downloadUrl", "file_url", "fileUrl"):
            found = _find_http_url(value.get(key), depth + 1)
            if found:
                return found
        for nested in value.values():
            found = _find_http_url(nested, depth + 1)
            if found:
                return found
    return ""


def _is_private_download_url(value: str) -> bool:
    try:
        url = urlparse(value)
        if url.scheme not in ("http", "https"):
            return True
        host = (url.hostname or "").lower()
        return (
            host == "localhost"
            or host == "127.0.0.1"
            or host == "::1"
            or bool(re.match(r"^10\.", host))
            or bool(re.match(r"^192\.168\.", host))
            or bool(re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", host))
            or bool(re.match(r"^169\.254\.", host))
        )
    except Exception:
        return True


# ---------------------------------------------------------------------------
# RSA 加密/解密（纯 Python int/bytes 实现，无外部依赖）
# ---------------------------------------------------------------------------
def pan115_rsa_encrypt(value: str) -> str:
    tmp = _xor_bytes(value.encode("utf-8"), _RSA_KEY)[::-1]
    xored = _RSA_RAND_KEY + _xor_bytes(tmp, _G_KEY_L)
    return base64.b64encode(_rsa_transform_blocks(xored, 117, 128, True)).decode("ascii")


def pan115_rsa_decrypt(value: str) -> str:
    data = _rsa_transform_blocks(base64.b64decode(value), 128, 0, False)
    body = data[16:]
    key = _rsa_gen_key(data[:16], 12)
    tmp = _xor_bytes(body, key)[::-1]
    return _xor_bytes(tmp, _RSA_KEY).decode("utf-8")


def _rsa_transform_blocks(input_data: bytes, in_size: int, out_size: int, encrypt: bool) -> bytes:
    chunks: List[bytes] = []
    for offset in range(0, len(input_data), in_size):
        block = input_data[offset : offset + in_size]
        if encrypt:
            number = _pad_pkcs1(block)
        else:
            number = _buffer_to_big_int(block)
        transformed = pow(number, _RSA_E, _RSA_N)
        if encrypt:
            chunks.append(_big_int_to_buffer(transformed, out_size))
        else:
            plain = _big_int_to_buffer(transformed)
            zero_index = plain.find(b"\x00")
            if zero_index >= 0:
                chunks.append(plain[zero_index + 1 :])
            else:
                chunks.append(plain)
    return b"".join(chunks)


def _pad_pkcs1(block: bytes) -> int:
    if len(block) > 117:
        raise ValueError("115 RSA block too large")
    padded = b"\x00" + bytes([2]) * (126 - len(block)) + b"\x00" + block
    return _buffer_to_big_int(padded)


def _rsa_gen_key(rand_key: bytes, length: int = 4) -> bytes:
    key = bytearray(length)
    row = length * (length - 1)
    index = 0
    for i in range(length):
        x = (rand_key[i] + _G_KTS[index]) & 0xFF
        key[i] = _G_KTS[row] ^ x
        row -= length
        index += length
    return bytes(key)


def _xor_bytes(source: bytes, key: bytes) -> bytes:
    out = bytearray(source)
    remainder = len(source) & 3
    if remainder:
        _xor_chunk(source, key, out, 0, remainder)
    start = remainder
    while start < len(source):
        length = min(len(key), len(source) - start)
        _xor_chunk(source, key, out, start, length)
        start += len(key)
    return bytes(out)


def _xor_chunk(source: bytes, key: bytes, out: bytearray, start: int, length: int) -> None:
    value = _buffer_to_big_int(source[start : start + length]) ^ _buffer_to_big_int(key[:length])
    out[start : start + length] = _big_int_to_buffer(value, length)


def _buffer_to_big_int(buffer: bytes) -> int:
    if not buffer:
        return 0
    return int(buffer.hex() or "0", 16)


def _big_int_to_buffer(value: int, size: int = 0) -> bytes:
    hex_str = f"{value:x}"
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    buf = bytes.fromhex(hex_str) if hex_str != "0" else b"\x00"
    if size and len(buf) < size:
        buf = b"\x00" * (size - len(buf)) + buf
    if size:
        buf = buf[-size:]
    return buf
