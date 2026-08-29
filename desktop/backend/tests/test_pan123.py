import asyncio
import base64
import json
import time
import unittest
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pan123 import (
    Pan123Client,
    Pan123Error,
    Pan123OpenAPIClient,
    crc32_text,
    normalize_file,
    normalize_user_info,
    parse_pan123_share_url,
    signed_query,
)


class Pan123Tests(unittest.TestCase):
    @staticmethod
    def _jwt(expires_at: int, marker: str = "token") -> str:
        def encode(value):
            raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        return f"{encode({'alg': 'none'})}.{encode({'exp': expires_at, 'marker': marker})}.signature"

    def test_signed_query_is_stable_with_inputs(self):
        result = signed_query("/b/api/file/list/new", now_ms=1721462400123, random_value=1234567)
        self.assertEqual(len(result), 1)
        key, value = next(iter(result.items()))
        self.assertTrue(key.isdigit())
        self.assertEqual(
            value,
            "1721462400.123-1234567-{}".format(
                crc32_text("1721462400.123|1234567|/b/api/file/list/new|web|3|{}".format(key))
            ),
        )

    def test_share_copy_signature_matches_captured_web_requests(self):
        submit = signed_query(
            "/b/api/restful/goapi/v1/file/copy/save",
            now_ms=1785417019000,
            random_value=1309509,
            app_version="139",
        )
        self.assertEqual(submit, {"591039761": "1785417019-1309509-3695252838"})
        status = signed_query(
            "/b/api/restful/goapi/v1/file/copy/save/get?taskID=43674055",
            now_ms=1785417153000,
            random_value=2864152,
            app_version="139",
        )
        self.assertEqual(status, {"609700104": "1785417153-2864152-462093211"})

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

        import asyncio

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

        import asyncio

        info = asyncio.run(client.get_share_info("https://www.123pan.com/s/demo"))

        self.assertEqual(info["userId"], 123)

    def test_share_copy_request_maps_root_items_to_target_directory(self):
        client = Pan123Client()
        client._request_share_copy_api = AsyncMock(return_value={"data": {"taskID": 32930572}})  # type: ignore[method-assign]

        import asyncio

        task_id = asyncio.run(client.create_share_copy_task(
            {"token": "token", "loginUuid": "uuid"},
            "https://1819914790.share.123pan.cn/gsb/s/MVkkjv-tufUd",
            "ABCD",
            "456",
            [{"FileId": 82760950, "Size": 20129863851, "Type": 1, "FileName": "辣妹刺客", "DriveId": 0}],
        ))

        self.assertEqual(task_id, 32930572)
        request_payload = client._request_share_copy_api.await_args.args[4]  # type: ignore[attr-defined]
        self.assertEqual(request_payload, {
            "fileList": [{
                "fileID": 82760950,
                "size": 20129863851,
                "etag": "",
                "type": 1,
                "parentFileID": 456,
                "fileName": "辣妹刺客",
                "driveID": 0,
            }],
            "shareKey": "MVkkjv-tufUd",
            "sharePwd": "ABCD",
            "currentLevel": 0,
            "superAdmin": None,
        })

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
        self.assertEqual(file["fileId"], 123)
        self.assertEqual(file["name"], "很长的文件名.mkv")
        self.assertEqual(file["filename"], "很长的文件名.mkv")
        self.assertEqual(file["size"], 1048576)
        self.assertEqual(file["parentName"], "动漫")
        self.assertEqual(file["absPath"], "/456/123")
        self.assertEqual(file["updateAt"], "2026-07-20T12:00:00+08:00")

    def test_normalize_user_info_prefers_display_fields(self):
        info = normalize_user_info(
            {
                "nickname": "小明",
                "phone": "13800000000",
                "spaceUsed": "2048",
                "spacePermanent": 4096,
                "vip": 1,
            },
            fallback_user="13800000000",
        )
        self.assertEqual(info["nickname"], "小明")
        self.assertEqual(info["passport"], "13800000000")
        self.assertEqual(info["spaceUsed"], 2048)
        self.assertEqual(info["spacePermanent"], 4096)
        self.assertTrue(info["vip"])

    def test_openapi_list_stops_when_response_cursor_is_zero(self):
        client = Pan123OpenAPIClient("client", "secret")
        client.request = AsyncMock(return_value={
            "data": {
                "fileList": [{"fileId": 123, "fileName": "done.mkv", "type": 0, "size": 1}],
                "lastFileId": 0,
            }
        })

        import asyncio

        files = asyncio.run(client.list_files("0"))

        self.assertEqual([item["fileId"] for item in files], [123])
        client.request.assert_awaited_once()

    def test_openapi_token_uses_jwt_exp_when_response_omits_expiry(self):
        expires_at = int(time.time()) + 7 * 86400
        token = self._jwt(expires_at)
        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=httpx.Response(200, json={"code": 0, "data": {"accessToken": token}}))
        client = Pan123OpenAPIClient("client", "secret")
        client._http.write = lambda: http_client

        async def run():
            values = await asyncio.gather(*(client.get_token() for _ in range(8)))
            return values

        values = asyncio.run(run())

        self.assertEqual(values, [token] * 8)
        self.assertEqual(client._access_token_expires_at, float(expires_at))
        http_client.post.assert_awaited_once()

    def test_openapi_cached_legacy_one_day_expiry_is_repaired_from_jwt(self):
        expires_at = int(time.time()) + 6 * 86400
        token = self._jwt(expires_at)

        class Store:
            def __init__(self):
                self.saved = []

            async def load(self):
                return {"accessToken": token, "expiresAt": time.time() - 5}

            async def save(self, access_token, corrected_expires_at):
                self.saved.append((access_token, corrected_expires_at))

            async def clear(self):
                raise AssertionError("valid token must not be cleared")

        store = Store()
        client = Pan123OpenAPIClient("client", "secret", token_store=store)
        client._http.write = MagicMock(side_effect=AssertionError("token endpoint must not be called"))

        loaded = asyncio.run(client.get_token())

        self.assertEqual(loaded, token)
        self.assertEqual(client._access_token_expires_at, float(expires_at))
        self.assertEqual(store.saved, [(token, float(expires_at))])

    def test_openapi_explicit_invalid_token_refreshes_once(self):
        old_token = self._jwt(int(time.time()) + 86400, "old")
        new_token = self._jwt(int(time.time()) + 7 * 86400, "new")
        read_client = MagicMock()
        read_client.request = AsyncMock(
            side_effect=[
                httpx.Response(401, json={"code": 401, "message": "token expired"}),
                httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
            ]
        )
        write_client = MagicMock()
        write_client.post = AsyncMock(return_value=httpx.Response(200, json={"code": 0, "data": {"accessToken": new_token}}))
        client = Pan123OpenAPIClient("client", "secret")
        client._access_token = old_token
        client._access_token_expires_at = time.time() + 86400
        client._cached_token_loaded = True
        client._http.read = lambda: read_client
        client._http.write = lambda: write_client

        result = asyncio.run(client._request_once("GET", "/api/v2/file/list", None, None, retried=False))

        self.assertEqual(result["data"]["ok"], True)
        self.assertEqual(client._access_token, new_token)
        self.assertEqual(read_client.request.await_count, 2)
        write_client.post.assert_awaited_once()

if __name__ == "__main__":
    unittest.main()
