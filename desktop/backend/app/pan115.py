from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from .pan115_cipher import build_upload_request, decode_upload_response


USER_AGENT = "Mozilla/5.0 115disk/31.4.2 115Browser/31.4.2 115wangpan_android/34.0.0"
APP_VERSION = "31.4.2"
SHARE_SNAP_URL = "https://webapi.115.com/share/snap"
SHARE_DOWN_WEB_URL = "https://webapi.115.com/share/downurl"
SHARE_DOWN_APP_URL = "https://proapi.115.com/app/share/downurl"
SHARE_DOWN_CHROME_URL = "https://proapi.115.com/2.0/share/downurl"
OFFLINE_SIGN_URL = "https://proapi.115.com/android/files/offlinesign"
OFFLINE_ADD_URL = "https://clouddownload.115.com/lixianssp/"
OPEN_OFFLINE_ADD_URL = "https://proapi.115.com/open/offline/add_task_urls"
RECYCLE_CLEAN_URL = "https://webapi.115.com/rb/clean"
USER_INFO_URL = "https://my.115.com/?ct=ajax&ac=get_user_aq"

LOCAL_FILES_URL = "https://webapi.115.com/files"
LOCAL_MKDIR_URL = "https://webapi.115.com/files/add"
UPLOAD_INIT_URL = "https://uplb.115.com/4.0/initupload.php"
UPLOAD_KEY_URL = "https://proapi.115.com/android/2.0/user/upload_key"
UPLOAD_INFO_URL = "https://proapi.115.com/app/uploadinfo"
FILE_UPDATE_URL = "https://proapi.115.com/open/ufile/update"
# initupload 的 UA 与 appversion 必须配套（token 签名含 appversion，服务端会校验）
UPLOAD_UA = "Mozilla/5.0 115disk/36.2.28 115Browser/36.2.28 115wangpan_android/36.2.28"
UPLOAD_APP_VERSION = "36.2.28"
SHARE_RE = re.compile(r"https?://(?:www\.)?(?:115cdn\.com|115\.com)/s/[^\s<>\"']+", re.I)
CODE_RE = re.compile(r"(?:提取码|访问码|密码|password|receive[_\s-]?code|code|pwd)[=：:\s]*([A-Za-z0-9]{4,8})", re.I)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[A-Za-z0-9]{32,40}[^\s<>\"']*", re.I)
ED2K_RE = re.compile(r"ed2k://\|file\|[^\r\n<>\"']+?\|\d+\|[A-Fa-f0-9]{32}\|/?", re.I)

G_KEY_L = bytes([0x78, 0x06, 0xAD, 0x4C, 0x33, 0x86, 0x5D, 0x18, 0x4C, 0x01, 0x3F, 0x46])
RSA_RAND_KEY = bytes(16)
RSA_KEY = bytes([0x8D, 0xA5, 0xA5, 0x8D])
G_KTS = bytes(
    [
        0xF0,
        0xE5,
        0x69,
        0xAE,
        0xBF,
        0xDC,
        0xBF,
        0x8A,
        0x1A,
        0x45,
        0xE8,
        0xBE,
        0x7D,
        0xA6,
        0x73,
        0xB8,
        0xDE,
        0x8F,
        0xE7,
        0xC4,
        0x45,
        0xDA,
        0x86,
        0xC4,
        0x9B,
        0x64,
        0x8B,
        0x14,
        0x6A,
        0xB4,
        0xF1,
        0xAA,
        0x38,
        0x01,
        0x35,
        0x9E,
        0x26,
        0x69,
        0x2C,
        0x86,
        0x00,
        0x6B,
        0x4F,
        0xA5,
        0x36,
        0x34,
        0x62,
        0xA6,
        0x2A,
        0x96,
        0x68,
        0x18,
        0xF2,
        0x4A,
        0xFD,
        0xBD,
        0x6B,
        0x97,
        0x8F,
        0x4D,
        0x8F,
        0x89,
        0x13,
        0xB7,
        0x6C,
        0x8E,
        0x93,
        0xED,
        0x0E,
        0x0D,
        0x48,
        0x3E,
        0xD7,
        0x2F,
        0x88,
        0xD8,
        0xFE,
        0xFE,
        0x7E,
        0x86,
        0x50,
        0x95,
        0x4F,
        0xD1,
        0xEB,
        0x83,
        0x26,
        0x34,
        0xDB,
        0x66,
        0x7B,
        0x9C,
        0x7E,
        0x9D,
        0x7A,
        0x81,
        0x32,
        0xEA,
        0xB6,
        0x33,
        0xDE,
        0x3A,
        0xA9,
        0x59,
        0x34,
        0x66,
        0x3B,
        0xAA,
        0xBA,
        0x81,
        0x60,
        0x48,
        0xB9,
        0xD5,
        0x81,
        0x9C,
        0xF8,
        0x6C,
        0x84,
        0x77,
        0xFF,
        0x54,
        0x78,
        0x26,
        0x5F,
        0xBE,
        0xE8,
        0x1E,
        0x36,
        0x9F,
        0x34,
        0x80,
        0x5C,
        0x45,
        0x2C,
        0x9B,
        0x76,
        0xD5,
        0x1B,
        0x8F,
        0xCC,
        0xC3,
        0xB8,
        0xF5,
    ]
)
RSA_N = int(
    "8686980c0f5a24c4b9d43020cd2c22703ff3f450756529058b1cf88f09b8602136477198a6e2683149659bd122c33592fdb5ad47944ad1ea4d36c6b172aad6338c3bb6ac6227502d010993ac967d1aef00f0c8e038de2e4d3bc2ec368af2e9f10a6f1eda4f7262f136420c07c331b871bf139f74f3010e3c4fe57df3afb71683",
    16,
)
RSA_E = 0x10001


class Pan115Error(RuntimeError):
    pass


@dataclass
class Pan115ShareLink:
    url: str
    clean_url: str
    share_code: str
    receive_code: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "url": self.url,
            "cleanUrl": self.clean_url,
            "shareCode": self.share_code,
            **({"receiveCode": self.receive_code} if self.receive_code else {}),
        }


def extract_115_links(text: str) -> List[Pan115ShareLink]:
    links: List[Pan115ShareLink] = []
    seen: set[str] = set()
    value = str(text or "")
    for match in SHARE_RE.finditer(value):
        raw = trim_url(match.group(0))
        share_code = extract_share_code(raw)
        if not share_code or share_code.lower() in seen:
            continue
        seen.add(share_code.lower())
        receive_code = query_password(raw) or regex_group(CODE_RE.search(raw), 1) or regex_group(CODE_RE.search(value), 1) or ""
        clean_url = f"https://115cdn.com/s/{share_code}"
        if receive_code:
            clean_url += f"?password={url_quote(receive_code)}"
        links.append(Pan115ShareLink(url=raw, clean_url=clean_url, share_code=share_code, receive_code=receive_code))
    return links


def extract_pan115_offline_links(text: str) -> List[str]:
    value = str(text or "")
    raw_values = [
        *(match.group(0) for match in MAGNET_RE.finditer(value)),
        *(match.group(0) for match in ED2K_RE.finditer(value)),
    ]
    result: List[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        normalized = normalize_offline_url(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


class Pan115Client:
    def __init__(self, cookie: str, request_interval_ms: int = 2500, timeout_seconds: float = 30.0):
        self.cookie = str(cookie or "")
        self.request_interval_ms = max(0, int(request_interval_ms or 0))
        self.timeout = timeout_seconds
        self._last_request_at = 0.0
        self._cached_user_id = ""
        self._upload_credentials: Dict[str, str] = {}

    async def inspect_and_flatten(self, link: Pan115ShareLink) -> Dict[str, Any]:
        if not self.cookie.strip():
            raise Pan115Error("请先配置 115 Cookie")
        if not link.receive_code:
            raise Pan115Error("115 分享缺少提取码；请在同一条消息里带上提取码")

        files: List[Dict[str, Any]] = []
        root = await self.list_share(link.share_code, link.receive_code, "", 1000, 0)
        data = as_record(root.get("data"))
        share_info = as_record(data.get("shareinfo"))
        title = str(share_info.get("share_title") or "").strip()
        receive_code = str(share_info.get("receive_code") or link.receive_code or "").strip()

        async def visit(dir_id: str, path: List[str]) -> None:
            offset = 0
            while True:
                page = await self.list_share(link.share_code, receive_code, dir_id, 1000, offset)
                page_data = as_record(page.get("data"))
                items = page_data.get("list") if isinstance(page_data.get("list"), list) else []
                for item_value in items:
                    item = as_record(item_value)
                    name = str(item.get("n") or item.get("name") or "").strip()
                    if not name:
                        continue
                    is_dir = int_number(item.get("fc"), 1) == 0
                    if is_dir:
                        child_dir_id = str(item.get("cid") or item.get("fid") or "").strip()
                        if child_dir_id:
                            await visit(child_dir_id, [*path, name])
                        continue
                    file_id = str(item.get("fid") or "").strip()
                    if file_id:
                        files.append(
                            {
                                "id": file_id,
                                "name": name,
                                "size": int_number(item.get("s"), 0),
                                **({"sha1": str(item.get("sha") or "").lower()} if item.get("sha") else {}),
                                "path": path,
                                "status": "pending",
                            }
                        )
                offset += len(items)
                count = int_number(page_data.get("count"), len(items))
                if not items or offset >= count:
                    break

        for item_value in data.get("list") if isinstance(data.get("list"), list) else []:
            item = as_record(item_value)
            name = str(item.get("n") or item.get("name") or "").strip()
            is_dir = int_number(item.get("fc"), 1) == 0
            if is_dir:
                child_dir_id = str(item.get("cid") or item.get("fid") or "").strip()
                if child_dir_id:
                    await visit(child_dir_id, [name] if name else [])
            elif item.get("fid") and name:
                files.append(
                    {
                        "id": str(item.get("fid")),
                        "name": name,
                        "size": int_number(item.get("s"), 0),
                        **({"sha1": str(item.get("sha") or "").lower()} if item.get("sha") else {}),
                        "path": [],
                        "status": "pending",
                    }
                )
        return {"title": title, "receiveCode": receive_code, "files": files}

    async def get_download_url(self, share_code: str, receive_code: str, file_id: str) -> str:
        payload = {
            "share_code": str(share_code),
            "receive_code": str(receive_code),
            "file_id": str(file_id),
            "dl": "1",
        }
        errors: List[str] = []
        for fetcher in (self._fetch_app_download_url, self._fetch_chrome_download_url, self._fetch_web_download_url):
            try:
                download_url = await fetcher(payload, share_code, receive_code)
                if not download_url:
                    continue
                if is_private_download_url(download_url):
                    errors.append("115 返回了局域网直链，已跳过")
                    continue
                return download_url
            except Exception as error:
                errors.append(str(error))
        raise Pan115Error("；".join([item for item in errors if item]) or "115 未返回文件直链")

    async def add_offline_urls(self, urls: List[str], target_dir_id: str) -> str:
        try:
            return await self.add_offline_urls_open(urls, target_dir_id)
        except Exception as open_error:
            if len(urls) == 1:
                return await self.add_offline_urls_legacy(urls, target_dir_id, open_error)
            try:
                return await self.add_offline_urls_legacy(urls, target_dir_id, open_error)
            except Exception:
                messages: List[str] = []
                for url in urls:
                    messages.append(await self.add_offline_urls_legacy([url], target_dir_id, open_error))
                return messages[-1] if messages else str(open_error)

    async def add_offline_urls_open(self, urls: List[str], target_dir_id: str) -> str:
        payload = await self.post_form(
            OPEN_OFFLINE_ADD_URL,
            "115 open 离线任务",
            {
                "urls": "\n".join(urls),
                "wp_path_id": str(target_dir_id or "0"),
            },
        )
        return pan115_message(payload) or f"已批量提交 {len(urls)} 个离线任务"

    async def add_offline_urls_legacy(self, urls: List[str], target_dir_id: str, cause: Optional[BaseException] = None) -> str:
        sign = await self.get_offline_sign()
        payload = build_pan115_offline_payload(urls, target_dir_id, sign)
        response = await self.request_encrypted(OFFLINE_ADD_URL, payload, "115 离线任务")
        message = pan115_message(response) or f"已提交 {len(urls)} 个离线任务"
        if cause:
            return f"{message}（open 接口回退）"
        return message

    async def clean_recycle_bin(self, password: str = "") -> str:
        payload = await self.post_form(RECYCLE_CLEAN_URL, "115 清空回收站", {"password": password} if password else {})
        return pan115_message(payload) or "回收站已清空"

    async def user_info(self) -> Dict[str, Any]:
        return await self.request_json(USER_INFO_URL, "115 用户信息")

    async def get_offline_sign(self) -> Dict[str, str]:
        payload = await self.request_json(OFFLINE_SIGN_URL, "115 离线签名")
        data = as_record(payload.get("data"))
        sign = str(payload.get("sign") or data.get("sign") or "").strip()
        sign_time = str(payload.get("time") or data.get("time") or "").strip()
        if not sign or not sign_time:
            raise Pan115Error("115 离线签名响应缺少 sign/time")
        return {"sign": sign, "time": sign_time}

    # ------------------------------------------------------------------
    # 本地盘操作（123 → 115 搬运使用）
    # ------------------------------------------------------------------
    async def list_local_dir(self, dir_id: str, limit: int, offset: int) -> Dict[str, Any]:
        return await self.request_json(
            LOCAL_FILES_URL,
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

    async def list_local_entries(self, dir_id: str) -> List[Dict[str, Any]]:
        """翻页列出目录下全部条目，规范化为 {fid,name,size,isDir,sha,pickCode}。"""
        entries: List[Dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self.list_local_dir(dir_id, 1000, offset)
            items = local_list_items(payload)
            if not items:
                break
            for item in items:
                name = str(item.get("n") or item.get("fn") or item.get("file_name") or item.get("name") or "").strip()
                if not name:
                    continue
                is_dir = local_item_is_dir(item)
                # 115 files 列表：目录条目的 ID 在 cid（没有 fid），文件条目在 fid
                if is_dir:
                    fid = str(item.get("cid") or item.get("category_id") or item.get("fid") or item.get("file_id") or item.get("id") or "").strip()
                else:
                    fid = str(item.get("fid") or item.get("file_id") or item.get("id") or "").strip()
                if not fid:
                    continue
                entries.append({
                    "fid": fid,
                    "name": name,
                    "size": 0 if is_dir else int_number(item.get("s") or item.get("fs") or item.get("file_size") or item.get("size"), 0),
                    "isDir": is_dir,
                    "sha": str(item.get("sha") or item.get("sha1") or item.get("file_sha1") or "").strip().lower(),
                    "pickCode": str(item.get("pc") or item.get("pick_code") or item.get("pickcode") or "").strip(),
                })
            offset += len(items)
            if len(items) < 1000:
                break
            count = local_list_count(payload, 0)
            if count and offset >= count:
                break
        return entries

    async def ensure_local_dir(self, parent_cid: str, name: str) -> str:
        """在 parent_cid 下找同名目录，找不到就创建，返回目录 cid。"""
        for entry in await self.list_local_entries(parent_cid):
            if entry.get("isDir") and entry.get("name") == name:
                return str(entry.get("fid") or "")
        return await self.mkdir_local_dir(parent_cid, name)

    async def mkdir_local_dir(self, parent_cid: str, name: str) -> str:
        """直接创建目录（不做同名检查，重复创建由调用方先用列表判断）。

        注意 webapi files/add 的目录名字段是 cname（不是 file_name）。
        响应里目录 ID 的位置随端点/版本漂移（顶层 cid、data.file_id、
        data.category_id……），解析不到时回查父目录列表按名字定位，仍找不到
        才报错并附带响应片段，便于继续排查。
        """
        try:
            payload = await self.post_form(
                LOCAL_MKDIR_URL,
                "115 新建目录",
                {"pid": str(parent_cid or "0"), "cname": name},
            )
        except Pan115Error as error:
            # errno 20004：同名目录已存在——回查父目录按名字定位（并发/重复创建时自愈）
            if "20004" in str(error) or "已存在" in str(error):
                for entry in await self.list_local_entries(parent_cid):
                    if entry.get("isDir") and entry.get("name") == name:
                        return str(entry.get("fid") or "")
            raise
        raw_data = payload.get("data")
        data = as_record(raw_data)
        if isinstance(raw_data, list) and raw_data:
            data = as_record(raw_data[0])
        cid = str(
            payload.get("cid")
            or data.get("file_id") or data.get("fileID") or data.get("fid")
            or data.get("category_id") or data.get("cid") or data.get("id")
            or (raw_data if isinstance(raw_data, int) else None)
            or payload.get("file_id") or payload.get("id")
            or ""
        ).strip()
        if cid and cid != "0":
            return cid
        # 响应里没有可识别的 ID：回查父目录，按名字找刚建的目录
        try:
            for entry in await self.list_local_entries(parent_cid):
                if entry.get("isDir") and entry.get("name") == name:
                    return str(entry.get("fid") or "")
        except Exception:
            pass
        try:
            snippet = json.dumps(payload, ensure_ascii=False)[:200]
        except Exception:
            snippet = str(payload)[:200]
        raise Pan115Error(f"115 新建目录未返回目录 ID：{name}（响应片段：{snippet}）")

    async def rename_local_file(self, file_id: str, new_name: str) -> None:
        await self.post_form(
            FILE_UPDATE_URL,
            "115 重命名文件",
            {"file_id": str(file_id), "file_name": new_name},
        )

    async def upload_init_fast(
        self,
        file_name: str,
        sha1: str,
        size: int,
        target_pid: str,
        fetch_range_bytes=None,
    ) -> Dict[str, Any]:
        """115 秒传：uplb 4.0/initupload.php（Cookie + userid/userkey 认证）。

        注意不要用 proapi 的 /open/upload/init——它要求 Bearer access_token，
        纯 Cookie 会报"access_token 格式错误"。initupload 的请求体与响应都是
        AES 加密 + LZ4 压缩（签名协议见 pan115_cipher），响应 status==2 即秒传
        成功；status==7 时服务器下发 sign_check（HTTP Range 形式的字节范围）做
        二次验证，取到片段算 SHA1 作为 sign_val 重发；其余状态视为 115 服务器
        上没有该文件。偶发 HTTP 401 重试即可（p115client 注明的已知怪癖）。
        """
        if not re.fullmatch(r"[0-9a-fA-F]{40}", str(sha1 or "")):
            raise Pan115Error("115 秒传需要 40 位 SHA1")
        creds = await self._get_upload_credentials()
        base_payload = {
            "fileid": str(sha1).upper(),
            "filename": str(file_name or "file"),
            "filesize": str(max(0, int(size or 0))),
            "target": f"U_1_{str(target_pid or '0')}",
            "topupload": "true",
            "userid": creds["userid"],
            "userkey": creds["userkey"],
            "sign_key": "",
            "sign_val": "",
            "appversion": UPLOAD_APP_VERSION,
        }
        payload = dict(base_payload)
        challenged = False
        for _ in range(4):
            request = build_upload_request(payload)
            status_code, payload_json = await self._post_upload_init(request)
            if status_code == 401:
                continue
            if status_code >= 300 or payload_json is None:
                raise Pan115Error(f"115 秒传接口 HTTP {status_code}")
            if payload_json.get("state") is False:
                raise Pan115Error(pan115_message(payload_json) or "115 秒传接口返回失败")
            data = as_record(payload_json.get("data"))
            status = int_number(data.get("status"), int_number(payload_json.get("status"), 0))
            if status == 2:
                return {
                    "reuse": True,
                    "fileId": str(data.get("file_id") or data.get("fileid") or data.get("pickcode") or ""),
                }
            if status != 7 or challenged:
                return {"reuse": False, "fileId": "", "status": status}
            sign_key = str(data.get("sign_key") or payload_json.get("sign_key") or "").strip()
            sign_check = str(data.get("sign_check") or payload_json.get("sign_check") or "").strip()
            if not sign_key or not sign_check:
                return {"reuse": False, "fileId": "", "status": status}
            if fetch_range_bytes is None:
                raise Pan115Error("115 要求秒传二次验证，但缺少文件内容读取通道")
            content = await fetch_range_bytes(sign_check)
            if content is None:
                return {"reuse": False, "fileId": "", "status": status}
            sign_val = content if isinstance(content, str) else hashlib.sha1(content).hexdigest().upper()
            payload = {**base_payload, "sign_key": sign_key, "sign_val": sign_val.upper()}
            challenged = True
        return {"reuse": False, "fileId": ""}

    async def _post_upload_init(self, request: Dict[str, Any]) -> tuple:
        """POST initupload 并解密响应；返回 (http_status, 解密 JSON 或 None)。"""
        await self._throttle()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                UPLOAD_INIT_URL,
                params=request["params"],
                content=request["data"],
                headers={
                    "accept": "*/*",
                    "content-type": "application/x-www-form-urlencoded",
                    "cookie": self.cookie,
                    "user-agent": UPLOAD_UA,
                },
            )
        if response.status_code != 200:
            return response.status_code, None
        content = response.content
        text = content.decode("utf-8", "replace").strip()
        # 依次尝试：明文 JSON / AES+LZ4 / 仅 AES / 仅 LZ4 / 项目内 RSA 方案
        if text.startswith("{"):
            try:
                return response.status_code, json.loads(text)
            except Exception:
                pass
        try:
            return response.status_code, decode_upload_response(content)
        except Exception:
            pass
        try:
            from .pan115_cipher import aes_cbc_decrypt as _dec, lz4_decompress as _lz4, AES_KEY as _K, AES_IV as _IV
            aligned = content[:len(content) & -16]
            plain = _dec(aligned, _K, _IV)
            try:
                return response.status_code, json.loads(_lz4(plain))
            except Exception:
                pass
            return response.status_code, json.loads(plain)
        except Exception:
            pass
        try:
            from .pan115_cipher import lz4_decompress as _lz4_only
            return response.status_code, json.loads(_lz4_only(content))
        except Exception:
            pass
        try:
            from urllib.parse import unquote
            return response.status_code, json.loads(pan115_rsa_decrypt(unquote(text)))
        except Exception:
            pass
        raise Pan115Error(
            f"115 秒传响应无法解析（{len(content)} 字节，hex：{content.hex()[:480]}，片段：{text[:120]!r}）"
        )

        return {"reuse": False, "fileId": ""}

    async def _get_upload_credentials(self) -> Dict[str, str]:
        """获取秒传必需的 userid + userkey（Cookie 认证，进程内缓存）。

        优先 android/2.0/user/upload_key（p115client 的标准取法），
        失败再试 app/uploadinfo；都不给 userkey 时报错并附带响应片段。
        """
        if self._upload_credentials:
            return self._upload_credentials
        errors: List[str] = []
        for url, label in (
            (UPLOAD_KEY_URL, "115 上传凭据"),
            (UPLOAD_INFO_URL, "115 上传信息"),
        ):
            try:
                payload = await self.request_json(url, label)
            except Pan115Error as error:
                errors.append(f"{label}：{error}")
                continue
            data = as_record(payload.get("data"))
            if isinstance(payload.get("data"), list) and payload["data"]:
                data = as_record(payload["data"][0])
            userkey = str(data.get("userkey") or data.get("userKey") or payload.get("userkey") or "").strip()
            if not userkey:
                errors.append(f"{label}：响应缺少 userkey（键：{sorted(set(data) | set(payload))[:12]}）")
                continue
            user_id = str(data.get("user_id") or data.get("userId") or "").strip()
            if not user_id:
                user_id = await self.get_user_id()
            # Cookie 的 UID 形如 9999966_R2_1787280212，上传接口只要下划线前的数字段
            user_id = user_id.split("_", 1)[0].strip()
            self._upload_credentials = {"userid": user_id, "userkey": userkey}
            return self._upload_credentials
        raise Pan115Error("；".join(errors) or "115 上传凭据获取失败")

    async def get_user_id(self) -> str:
        if self._cached_user_id:
            return self._cached_user_id
        match = re.search(r"(?:^|;\s*)UID=([^;]+)", self.cookie, re.I)
        if match:
            self._cached_user_id = match.group(1)
            return self._cached_user_id
        info = await self.user_info()
        user_id = self.user_id_from_payload(info)
        if not user_id:
            raise Pan115Error("115 Cookie 中缺少 UID，且用户信息接口未返回用户 ID")
        self._cached_user_id = user_id
        return user_id

    def user_id_from_payload(self, payload: Any) -> str:
        root = as_record(payload)
        data = as_record(root.get("data"))
        value = root.get("uid") or root.get("user_id") or data.get("uid") or data.get("user_id") or data.get("id")
        return "" if value is None else str(value)

    async def list_all_share_root(self, share_code: str, receive_code: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = await self.list_share(share_code, receive_code, "", 1000, offset)
            data = as_record(page.get("data"))
            items = data.get("list") if isinstance(data.get("list"), list) else []
            result.extend(as_record(item) for item in items)
            offset += len(items)
            count = int_number(data.get("count"), len(result))
            if not items or offset >= count:
                break
        return result

    async def list_share(self, share_code: str, receive_code: str, dir_id: str, limit: int, offset: int) -> Dict[str, Any]:
        response = await self.request_json(
            SHARE_SNAP_URL,
            "115 分享列表",
            params={
                "share_code": str(share_code),
                "receive_code": str(receive_code),
                "cid": str(dir_id),
                "limit": str(limit),
                "asc": "0",
                "offset": str(offset),
                "format": "json",
            },
            headers={"referer": f"https://115.com/s/{share_code}?password={receive_code}&"},
        )
        if not response.get("data"):
            raise Pan115Error(pan115_message(response) or "115 分享列表为空或无权限访问")
        return response

    async def post_form(self, url: str, label: str, form: Dict[str, str], referer: str = "") -> Dict[str, Any]:
        headers = {"content-type": "application/x-www-form-urlencoded"}
        if referer:
            headers["referer"] = referer
        return await self.request_json(url, label, method="POST", headers=headers, data=form)

    async def request_encrypted(self, url: str, payload: Dict[str, str], label: str) -> Dict[str, Any]:
        body = {"data": pan115_rsa_encrypt(json.dumps(payload, ensure_ascii=False))}
        response = await self.request_json(url, label, method="POST", headers={"content-type": "application/x-www-form-urlencoded"}, data=body)
        if isinstance(response.get("data"), str):
            response = {**response, "data": parse_maybe_encrypted_payload(str(response.get("data")), label)}
        return response

    async def request_json(
        self,
        url: str,
        label: str,
        method: str = "GET",
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        await self._throttle()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(
                method,
                url,
                params=params,
                data=data,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "cookie": self.cookie,
                    "user-agent": USER_AGENT,
                    **(headers or {}),
                },
            )
        payload = read_pan115_payload(response, label)
        assert_pan115_ok(response, payload, label)
        return payload

    async def _fetch_app_download_url(self, payload: Dict[str, str], share_code: str, receive_code: str) -> str:
        response = await self.request_json(
            SHARE_DOWN_APP_URL,
            "115 app downurl",
            method="POST",
            headers={"content-type": "application/x-www-form-urlencoded", "referer": f"https://115.com/s/{share_code}?password={receive_code}&"},
            data={"data": pan115_rsa_encrypt(json.dumps(payload, ensure_ascii=False))},
        )
        data = response.get("data")
        if isinstance(data, str):
            data = json.loads(pan115_rsa_decrypt(data))
        return find_http_url(data)

    async def _fetch_chrome_download_url(self, payload: Dict[str, str], share_code: str, receive_code: str) -> str:
        response = await self.request_json(SHARE_DOWN_CHROME_URL, "115 取直链", params=payload, headers={"referer": f"https://115.com/s/{share_code}?password={receive_code}&"})
        return find_http_url(response.get("data"))

    async def _fetch_web_download_url(self, payload: Dict[str, str], share_code: str, receive_code: str) -> str:
        response = await self.request_json(SHARE_DOWN_WEB_URL, "115 取直链", params=payload, headers={"referer": f"https://115.com/s/{share_code}?password={receive_code}&"})
        return find_http_url(response.get("data"))

    async def _throttle(self) -> None:
        elapsed_ms = (time.time() - self._last_request_at) * 1000
        wait_ms = max(0.0, self.request_interval_ms - elapsed_ms)
        if wait_ms > 0:
            import asyncio

            await asyncio.sleep(wait_ms / 1000)
        self._last_request_at = time.time()


def summarize_helper_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    success = len([item for item in results if item.get("ok")])
    return {
        "ok": bool(results) and success == len(results),
        "total": len(results),
        "success": success,
        "failed": len(results) - success,
        "results": results,
    }


async def submit_115_offline_from_text(config: Dict[str, Any], text: str) -> Dict[str, Any]:
    ensure_helper_enabled(config)
    urls = extract_pan115_offline_links(text)
    if not urls:
        raise Pan115Error("没有找到 magnet 或 ed2k 链接")
    client = create_helper_client(config)
    target_dir_id = str(config.get("offlineTargetDirId") or "0")
    results: List[Dict[str, Any]] = []
    for batch in offline_submit_chunks(urls):
        try:
            message = await client.add_offline_urls(batch, target_dir_id)
            results.extend({"ok": True, "type": "offline", "label": url, "link": url, "message": message} for url in batch)
        except Exception as error:
            results.extend({"ok": False, "type": "offline", "label": url, "link": url, "message": str(error)} for url in batch)
    return summarize_helper_results(results)


async def empty_115_recycle(config: Dict[str, Any]) -> Dict[str, Any]:
    ensure_helper_enabled(config)
    client = create_helper_client(config)
    message = await client.clean_recycle_bin(str(config.get("trashPassword") or ""))
    return summarize_helper_results([{"ok": True, "type": "recycle", "label": "115 回收站", "message": message}])


async def helper_status(config: Dict[str, Any]) -> Dict[str, Any]:
    account = select_115_account(config)
    client = Pan115Client(account["cookie"], int_number(config.get("requestIntervalMs"), 2500))
    info = await client.user_info()
    return {"ok": True, "accountName": account["name"], "userId": client.user_id_from_payload(info), "raw": info}


def ensure_helper_enabled(config: Dict[str, Any]) -> None:
    if not config.get("enabled"):
        raise Pan115Error("115 助手未启用")
    select_115_account(config)


def create_helper_client(config: Dict[str, Any]) -> Pan115Client:
    account = select_115_account(config)
    return Pan115Client(account["cookie"], int_number(config.get("requestIntervalMs"), 2500))


def select_115_account(config: Dict[str, Any]) -> Dict[str, str]:
    line = first_115_cookie_line(config)
    split_index = line.find("|")
    name = line[:split_index].strip() if split_index > 0 else "默认账号"
    cookie = line[split_index + 1 :].strip() if split_index > 0 else line.strip()
    if not cookie:
        raise Pan115Error("请先配置 115 Cookie")
    return {"name": name or "默认账号", "cookie": cookie}


def first_115_cookie_line(config: Dict[str, Any]) -> str:
    direct = str(config.get("pan115Cookie") or "").strip()
    if direct:
        return next((line.strip() for line in re.split(r"\n\s*\n|[\r\n]+", direct) if line.strip()), "")
    legacy_pool = config.get("pan115Cookies") if isinstance(config.get("pan115Cookies"), list) else []
    return next((str(value or "").strip() for value in legacy_pool if str(value or "").strip()), "")


def transfer_cookie_pool(config: Dict[str, Any]) -> List[Dict[str, str]]:
    raw_values = [*(config.get("pan115Cookies") if isinstance(config.get("pan115Cookies"), list) else []), config.get("pan115Cookie") or ""]
    lines = [line.strip() for value in raw_values for line in re.split(r"\n\s*\n|[\r\n]+", str(value or "")) if line.strip()]
    result: List[Dict[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        split_index = line.find("|")
        name = line[:split_index].strip() if split_index > 0 else f"账号 {len(result) + 1}"
        cookie = line[split_index + 1 :].strip() if split_index > 0 else line
        if cookie and cookie not in seen:
            seen.add(cookie)
            result.append({"name": name or f"账号 {len(result) + 1}", "cookie": cookie})
    return result


def build_pan115_offline_payload(urls: List[str], target_dir_id: str, sign: Dict[str, str]) -> Dict[str, str]:
    single = len(urls) == 1
    payload = {
        "ac": "add_task_url" if single else "add_task_urls",
        "wp_path_id": str(target_dir_id or "0"),
        "savepath": "",
        "sign": str(sign.get("sign") or ""),
        "time": str(sign.get("time") or ""),
        "app_ver": APP_VERSION,
    }
    if single:
        payload["url"] = urls[0] if urls else ""
    else:
        for index, url in enumerate(urls):
            payload[f"url[{index}]"] = url
    return payload


def offline_submit_chunks(urls: List[str]) -> List[List[str]]:
    result: List[List[str]] = []
    batch: List[str] = []
    for url in urls:
        batch.append(url)
        if len(batch) >= 50:
            result.append(batch)
            batch = []
    if batch:
        result.append(batch)
    return result


def read_pan115_payload(response: httpx.Response, label: str) -> Dict[str, Any]:
    text = response.text.strip()
    if not text:
        raise Pan115Error(f"{label} 返回空响应（HTTP {response.status_code}）")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            payload = json.loads(pan115_rsa_decrypt(text))
        except Exception:
            if re.search(r"^<!doctype|^<html", text, re.I):
                raise Pan115Error(f"{label} 返回了网页页面（HTTP {response.status_code}），可能是 115 Cookie 失效、需要验证或接口风控")
            raise Pan115Error(f"{label} 返回的不是 JSON（HTTP {response.status_code}）：{response_snippet(text)}")
    return payload if isinstance(payload, dict) else {}


def parse_maybe_encrypted_payload(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return json.loads(pan115_rsa_decrypt(value))
        except Exception as error:
            raise Pan115Error(f"{label} 加密响应解析失败") from error


def assert_pan115_ok(response: httpx.Response, payload: Dict[str, Any], label: str) -> None:
    errno = int_number(payload.get("errno"), 0)
    if 200 <= response.status_code < 300 and payload.get("state") is not False and errno == 0:
        return
    message = pan115_message(payload) or f"{label} {response.status_code}"
    if re.search(r"无需重复|已经接收|已存在|重复", message, re.I):
        return
    raise Pan115Error(message)


def pan115_message(payload: Dict[str, Any]) -> str:
    data = as_record(payload.get("data"))
    return str(payload.get("error") or payload.get("message") or payload.get("msg") or data.get("error") or data.get("message") or data.get("msg") or "").strip()


def pan115_rsa_encrypt(value: str) -> str:
    tmp = xor_bytes(value.encode("utf-8"), RSA_KEY)[::-1]
    xored = RSA_RAND_KEY + xor_bytes(tmp, G_KEY_L)
    return base64.b64encode(rsa_transform_blocks(xored, 117, 128, encrypt=True)).decode("ascii")


def pan115_rsa_decrypt(value: str) -> str:
    data = rsa_transform_blocks(base64.b64decode(value), 128, 0, encrypt=False)
    body = data[16:]
    key = rsa_gen_key(data[:16], 12)
    tmp = xor_bytes(body, key)[::-1]
    return xor_bytes(tmp, RSA_KEY).decode("utf-8")


def rsa_transform_blocks(input_value: bytes, in_size: int, out_size: int, encrypt: bool) -> bytes:
    chunks: List[bytes] = []
    for offset in range(0, len(input_value), in_size):
        block = input_value[offset : offset + in_size]
        number = pad_pkcs1(block) if encrypt else buffer_to_int(block)
        transformed = pow(number, RSA_E, RSA_N)
        if encrypt:
            chunks.append(int_to_buffer(transformed, out_size))
        else:
            plain = int_to_buffer(transformed)
            zero_index = plain.find(b"\x00")
            chunks.append(plain[zero_index + 1 :] if zero_index >= 0 else plain)
    return b"".join(chunks)


def pad_pkcs1(block: bytes) -> int:
    if len(block) > 117:
        raise Pan115Error("115 RSA block too large")
    return buffer_to_int(bytes([0]) + bytes([2]) * (126 - len(block)) + bytes([0]) + block)


def rsa_gen_key(rand_key: bytes, length: int = 4) -> bytes:
    key = bytearray(length)
    row = length * (length - 1)
    index = 0
    for i in range(length):
        x = (rand_key[i] + G_KTS[index]) & 0xFF
        key[i] = G_KTS[row] ^ x
        row -= length
        index += length
    return bytes(key)


def xor_bytes(source: bytes, key: bytes) -> bytes:
    out = bytearray(len(source))
    remainder = len(source) & 3
    if remainder:
        out[:remainder] = xor_chunk(source, key, 0, remainder)
    for start in range(remainder, len(source), len(key)):
        length = min(len(key), len(source) - start)
        out[start : start + length] = xor_chunk(source, key, start, length)
    return bytes(out)


def xor_chunk(source: bytes, key: bytes, start: int, length: int) -> bytes:
    value = buffer_to_int(source[start : start + length]) ^ buffer_to_int(key[:length])
    return int_to_buffer(value, length)


def buffer_to_int(value: bytes) -> int:
    return int.from_bytes(value, "big") if value else 0


def int_to_buffer(value: int, size: int = 0) -> bytes:
    if value == 0:
        raw = b"\x00"
    else:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if size and len(raw) < size:
        raw = bytes(size - len(raw)) + raw
    return raw[-size:] if size else raw


def trim_url(value: str) -> str:
    return re.sub(r"[)，。；;,\])]+$", "", str(value or ""))


def extract_share_code(value: str) -> str:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        return parts[1] if len(parts) >= 2 and parts[0] == "s" else ""
    except Exception:
        match = re.search(r"/s/([A-Za-z0-9]+)", value, re.I)
        return match.group(1) if match else ""


def query_password(value: str) -> str:
    try:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(value).query)
        return (query.get("password") or query.get("pwd") or query.get("code") or [""])[0]
    except Exception:
        return ""


def normalize_offline_url(value: str) -> str:
    trimmed = trim_url(str(value or "").strip())
    return normalize_ed2k_url(trimmed) if trimmed.lower().startswith("ed2k://") else trimmed


def normalize_ed2k_url(value: str) -> str:
    from urllib.parse import unquote

    trimmed = trim_url(value)
    parts = trimmed.split("|")
    if len(parts) < 5 or parts[0].lower() != "ed2k://" or parts[1].lower() != "file":
        return trimmed
    size = parts[3]
    hash_value = parts[4]
    if not re.match(r"^\d+$", size) or not re.match(r"^[a-f0-9]{32}$", hash_value, re.I):
        return trimmed
    return f"ed2k://|file|{unquote(parts[2] or '')}|{size}|{hash_value.upper()}|/"


def find_http_url(value: Any, depth: int = 0) -> str:
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        return text if re.match(r"^https?://", text, re.I) else ""
    if isinstance(value, list):
        for item in value:
            found = find_http_url(item, depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("url", "download_url", "downloadUrl", "file_url", "fileUrl"):
            found = find_http_url(value.get(key), depth + 1)
            if found:
                return found
        for nested in value.values():
            found = find_http_url(nested, depth + 1)
            if found:
                return found
    return ""


def is_private_download_url(value: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"}:
            return True
        return (
            host in {"localhost", "127.0.0.1", "::1"}
            or re.match(r"^10\.", host) is not None
            or re.match(r"^192\.168\.", host) is not None
            or re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", host) is not None
            or re.match(r"^169\.254\.", host) is not None
        )
    except Exception:
        return True


def local_list_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 115 本地盘列表（webapi.115.com/files）响应里的条目列表。"""
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


def local_list_count(payload: Dict[str, Any], fallback: int) -> int:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (payload.get("count"), payload.get("file_count"), data.get("count"), data.get("file_count"), data.get("total")):
        number = int_number(value, -1)
        if number >= 0:
            return number
    return fallback


def local_item_is_dir(item: Dict[str, Any]) -> bool:
    """115 列表条目里 fc==0 表示目录；无 fc 时按是否有 sha/大小兜底判断。"""
    if item.get("fc") is not None:
        return int_number(item.get("fc"), 1) == 0
    for key in ("sha", "sha1", "file_sha1"):
        if item.get(key) is not None:
            return not str(item.get(key) or "").strip()
    return not any(item.get(key) for key in ("fid", "file_id", "s", "fs", "file_size", "size"))


def as_record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def int_number(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def regex_group(match: Optional[re.Match[str]], index: int) -> str:
    return match.group(index) if match else ""


def response_snippet(value: str) -> str:
    return re.sub(r"\s+", " ", value)[:160]


def url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def new_task_id() -> str:
    return uuid.uuid4().hex
