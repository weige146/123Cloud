import asyncio
import base64
import json
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pan123 import (
    Pan123Client,
    Pan123Error,
    Pan123OauthBroker,
    Pan123OpenAPIClient,
    decode_oplist_callback_fragment,
    normalize_file,
    normalize_open_user_info,
    parse_pan123_share_url,
)


def _jwt(expires_at: int, marker: str = "token") -> str:
    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': expires_at, 'marker': marker})}.signature"


def _encode_callback_fragment(data: dict) -> str:
    return base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")


class InMemoryOauthStore:
    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.saved = []
        self.cleared = 0

    async def load(self):
        return dict(self.data) if self.data else None

    async def save(self, data):
        self.data.update(data)
        self.saved.append(dict(data))

    async def clear(self):
        self.data.clear()
        self.cleared += 1


class Pan123ShareClientTests(unittest.TestCase):
    def test_share_url_parser_supports_official_paths_and_rejects_external_hosts(self):
        cases = {
            "https://www.123pan.com/s/abc_DEF?pwd=ABCD": "abc_DEF",
            "https://www.123pan.cn/ps/abc_DEF.html": "abc_DEF",
            "https://www.123912.com/123pan/abc_DEF": "abc_DEF",
            "https://www.123635.com/s/abc_DEF": "abc_DEF",
            "https://1819914790.share.123pan.cn/123pan/abc_DEF": "abc_DEF",
            "https://1819914790.share.123pan.cn/gsb/s/abc_DEF": "abc_DEF",
        }
        for url, share_key in cases.items():
            with self.subTest(url=url):
                self.assertEqual(parse_pan123_share_url(url)["shareKey"], share_key)
        with self.assertRaises(Pan123Error):
            parse_pan123_share_url("https://evil.example/s/abc_DEF")

    def test_share_info_parses_string_boolean_fields(self):
        client = Pan123Client()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(200, json={
            "code": 0,
            "data": {"ShareKey": "demo", "UserID": 123, "HasPwd": "false", "Expired": "0"},
        })
        client._http.read = lambda: read_client

        info = asyncio.run(client.get_share_info("https://www.123pan.com/s/demo"))

        self.assertFalse(info["hasPassword"])
        self.assertFalse(info["expired"])

    def test_share_info_accepts_wrapped_info_response(self):
        client = Pan123Client()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(200, json={
            "info": {"code": 0, "message": "", "data": {"ShareKey": "demo", "UserID": 123}},
        })
        client._http.read = lambda: read_client

        info = asyncio.run(client.get_share_info("https://www.123pan.com/s/demo"))

        self.assertEqual(info["userId"], 123)

    def test_normalize_file_keeps_legacy_and_standard_fields(self):
        file = normalize_file(
            {
                "FileId": 123,
                "FileName": "很长的文件名.mkv",
                "Type": 0,
                "Size": "1048576",
                "ParentName": "动漫",
                "AbsPath": "/456/123",
                "UpdateAt": "2026-07-20T12:00:00+08:00",
            }
        )
        self.assertIsNotNone(file)
        assert file is not None
        self.assertEqual(file["id"], "123")
        self.assertEqual(file["name"], "很长的文件名.mkv")
        self.assertEqual(file["size"], 1048576)
        self.assertEqual(file["parentName"], "动漫")
        self.assertEqual(file["absPath"], "/456/123")


class Pan123OauthBrokerTests(unittest.TestCase):
    def test_decode_callback_fragment(self):
        data = {"access_token": "at", "refresh_token": "rt", "expires_in": 86400, "server_use": True}
        fragment = _encode_callback_fragment(data)
        self.assertEqual(
            decode_oplist_callback_fragment(f"/#{fragment}"),
            data,
        )
        self.assertEqual(decode_oplist_callback_fragment("https://x/#bad!!!"), {})
        self.assertEqual(decode_oplist_callback_fragment(""), {})

    def test_authorize_url_returns_redirect_uri(self):
        broker = Pan123OauthBroker()
        authorize_url = (
            "https://yun.123pan.com/auth?client_id=demo&redirect_uri="
            "https%3A%2F%2Fapi.oplist.org%2F123cloud%2Fcallback&scope=user%3Abase&state=OpenList"
        )
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(200, json={"text": authorize_url})
        broker._http.read = lambda: read_client

        info = asyncio.run(broker.authorize_url())

        self.assertEqual(info["authorizeUrl"], authorize_url)
        self.assertEqual(info["redirectUri"], "https://api.oplist.org/123cloud/callback")
        args, kwargs = read_client.get.await_args
        self.assertIn("/123cloud/requests", args[0])
        self.assertEqual(kwargs["params"]["server_use"], "true")

    def test_authorize_url_error_raises(self):
        broker = Pan123OauthBroker()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(500, json={"text": "传入参数缺少"})
        broker._http.read = lambda: read_client

        with self.assertRaises(Pan123Error):
            asyncio.run(broker.authorize_url())

    def test_exchange_code_parses_redirect_fragment(self):
        broker = Pan123OauthBroker()
        fragment = _encode_callback_fragment({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 86400, "server_use": True,
        })
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(
            302, headers={"location": f"/#{fragment}"}, json={"text": ""}
        )
        broker._http.read = lambda: read_client

        tokens = asyncio.run(broker.exchange_code("the-code"))

        self.assertEqual(tokens["accessToken"], "at-1")
        self.assertEqual(tokens["refreshToken"], "rt-1")
        self.assertEqual(tokens["expiresIn"], 86400)
        args, kwargs = read_client.get.await_args
        self.assertIn("/123cloud/callback", args[0])
        self.assertEqual(kwargs["params"], {"code": "the-code"})
        self.assertFalse(kwargs["follow_redirects"])

    def test_exchange_code_error_raises(self):
        broker = Pan123OauthBroker()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(500, json={"text": "无法获取AccessToken"})
        broker._http.read = lambda: read_client

        with self.assertRaises(Pan123Error):
            asyncio.run(broker.exchange_code("bad"))

    def test_refresh_returns_rotated_tokens(self):
        broker = Pan123OauthBroker()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(200, json={
            "access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600,
        })
        broker._http.read = lambda: read_client

        tokens = asyncio.run(broker.refresh("rt-1"))

        self.assertEqual(tokens["accessToken"], "at-2")
        self.assertEqual(tokens["refreshToken"], "rt-2")
        args, kwargs = read_client.get.await_args
        self.assertIn("/123cloud/renewapi", args[0])
        self.assertEqual(kwargs["params"], {"refresh_ui": "rt-1"})

    def test_refresh_invalid_grant_gets_friendly_error(self):
        broker = Pan123OauthBroker()
        read_client = AsyncMock()
        read_client.get.return_value = httpx.Response(500, json={
            "text": "The provided authorization grant or refresh token is invalid"
        })
        broker._http.read = lambda: read_client

        with self.assertRaises(Pan123Error) as ctx:
            asyncio.run(broker.refresh("dead"))
        self.assertIn("重新授权", str(ctx.exception))


class Pan123OpenAPIClientTests(unittest.TestCase):
    def test_openapi_list_stops_when_response_cursor_is_zero(self):
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker())
        client.request = AsyncMock(return_value={
            "data": {
                "fileList": [{"fileId": 123, "fileName": "done.mkv", "type": 0, "size": 1}],
                "lastFileId": 0,
            }
        })

        files = asyncio.run(client.list_files("0"))

        self.assertEqual([item["fileId"] for item in files], [123])
        client.request.assert_awaited_once()

    def test_get_token_uses_valid_cached_access_token_without_refresh(self):
        expires_at = int(time.time()) + 7 * 86400
        token = _jwt(expires_at)
        store = InMemoryOauthStore({"refreshToken": "rt", "accessToken": token, "expiresAt": float(expires_at)})
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker(), oauth_store=store)
        client.broker.refresh = AsyncMock(side_effect=AssertionError("must not refresh"))

        loaded = asyncio.run(client.get_token())

        self.assertEqual(loaded, token)
        self.assertEqual(client.broker.refresh.await_count, 0)
        self.assertEqual(store.saved, [])

    def test_get_token_refreshes_when_expired_and_persists(self):
        expires_at = int(time.time()) + 7 * 86400
        new_token = _jwt(expires_at)
        store = InMemoryOauthStore({"refreshToken": "rt-old"})
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker(), oauth_store=store)
        client.broker.refresh = AsyncMock(return_value={
            "accessToken": new_token, "refreshToken": "rt-new", "expiresIn": 7 * 86400,
        })

        async def run():
            return await asyncio.gather(*(client.get_token() for _ in range(8)))

        values = asyncio.run(run())

        self.assertEqual(values, [new_token] * 8)
        client.broker.refresh.assert_awaited_once_with("rt-old")
        # 轮换后的 refresh token 与新 access token 都要落盘
        self.assertEqual(store.data.get("refreshToken"), "rt-new")
        self.assertEqual(store.data.get("accessToken"), new_token)

    def test_get_token_without_authorization_raises(self):
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker(), oauth_store=InMemoryOauthStore())

        with self.assertRaises(Pan123Error):
            asyncio.run(client.get_token())

    def test_request_refreshes_once_after_401(self):
        expires_at = int(time.time()) + 7 * 86400
        old_token = _jwt(int(time.time()) + 86400, "old")
        new_token = _jwt(expires_at, "new")
        read_client = MagicMock()
        read_client.request = AsyncMock(
            side_effect=[
                httpx.Response(401, json={"code": 401, "message": "token expired"}),
                httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
            ]
        )
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker(), oauth_store=InMemoryOauthStore({"refreshToken": "rt"}))
        client._access_token = old_token
        client._access_token_expires_at = time.time() + 86400
        client.broker.refresh = AsyncMock(return_value={"accessToken": new_token, "refreshToken": "rt", "expiresIn": 7 * 86400})
        client._http.read = lambda: read_client

        result = asyncio.run(client._request_once("GET", "/api/v2/file/list", None, None, retried=False))

        self.assertEqual(result["data"]["ok"], True)
        self.assertEqual(client._access_token, new_token)
        self.assertEqual(read_client.request.await_count, 2)
        client.broker.refresh.assert_awaited_once()

    def test_get_open_user_info_normalizes_fields(self):
        client = Pan123OpenAPIClient(broker=Pan123OauthBroker())
        client.request = AsyncMock(return_value={
            "code": 0,
            "data": {"uid": 10086, "nickname": "小明", "vip": True, "spaceUsed": 2048, "spacePermanent": 4096},
        })

        info = asyncio.run(client.get_open_user_info())

        self.assertEqual(info["uid"], 10086)
        self.assertEqual(info["nickname"], "小明")
        self.assertEqual(info["spaceUsed"], 2048)
        self.assertEqual(info["spacePermanent"], 4096)
        self.assertTrue(info["vip"])

    def test_normalize_open_user_info_defaults(self):
        info = normalize_open_user_info({"uid": "42"})
        self.assertEqual(info["uid"], 42)
        self.assertEqual(info["nickname"], "123-42")


if __name__ == "__main__":
    unittest.main()
