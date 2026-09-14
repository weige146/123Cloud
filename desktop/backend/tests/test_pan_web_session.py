"""网页端登录会话中转接口测试：/api/pan/session（推送/拉取/清除 + 令牌门禁）。

油猴脚本「会话复用」的新通道：已登录浏览器把网页会话（authorToken + LoginUuid）
推给客户端存档，其他浏览器从客户端拉取登录。凭据敏感，鉴权与影库接口同门
（配置了影库访问令牌就必须携带；未配置一律放行，后端默认只监听 127.0.0.1）。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app import main


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("Client", (), {"host": host})()


class PanWebSessionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_store = main.store
        self.addCleanup(setattr, main, "store", self._original_store)
        from app.session_store import SessionStore
        self.store = main.store = SessionStore(Path(self._directory.name))

    def _set_token(self, token: str) -> None:
        asyncio.run(main.write_library_config(
            main.LibraryConfigRequest(token=token),
            _StubRequest(host="127.0.0.1"),
        ))

    def test_push_pull_roundtrip(self):
        with unittest.mock.patch.object(main, "store", self.store):
            # 初始为空
            read = asyncio.run(main.read_pan_web_session(_StubRequest(), ""))
            self.assertTrue(read["ok"])
            self.assertTrue(read["empty"])
            self.assertEqual(read["session"]["authorToken"], "")

            # 推送 → 拉取往返（origin 只保留 scheme://host）
            saved = asyncio.run(main.write_pan_web_session(_StubRequest(), main.PanWebSessionRequest(
                authorToken="eyJhbGciOi.token.value", loginUuid="a3f1c2b4", account=" tester ",
                origin="https://yun.123pan.cn/some/path?query=1",
            )))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["session"]["origin"], "https://yun.123pan.cn")
            # 推送响应里 token 打码，不回显完整凭据
            self.assertTrue(saved["session"]["authorToken"].startswith("eyJhbGci…"))
            self.assertNotIn("eyJhbGciOi.token.value", saved["session"]["authorToken"])

            read = asyncio.run(main.read_pan_web_session(_StubRequest(), ""))
            self.assertFalse(read["empty"])
            self.assertEqual(read["session"]["authorToken"], "eyJhbGciOi.token.value")
            self.assertEqual(read["session"]["loginUuid"], "a3f1c2b4")
            self.assertEqual(read["session"]["account"], "tester")
            self.assertEqual(read["session"]["origin"], "https://yun.123pan.cn")
            self.assertTrue(read["session"]["updatedAt"])

            # 再次推送覆盖旧会话
            asyncio.run(main.write_pan_web_session(_StubRequest(), main.PanWebSessionRequest(
                authorToken="new-token", loginUuid="new-uuid",
            )))
            read = asyncio.run(main.read_pan_web_session(_StubRequest(), ""))
            self.assertEqual(read["session"]["authorToken"], "new-token")
            self.assertEqual(read["session"]["account"], "")
        print("ok 推送/拉取往返：会话存档与覆盖")

    def test_push_requires_complete_session(self):
        with unittest.mock.patch.object(main, "store", self.store):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.write_pan_web_session(_StubRequest(), main.PanWebSessionRequest(
                    authorToken="only-token", loginUuid="",
                )))
            self.assertEqual(ctx.exception.status_code, 400)
            # 垃圾 origin 归一化为空
            saved = asyncio.run(main.write_pan_web_session(_StubRequest(), main.PanWebSessionRequest(
                authorToken="t", loginUuid="u", origin="javascript:alert(1)",
            )))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["session"]["origin"], "")
            # 清除后回到空
            asyncio.run(main.clear_pan_web_session(_StubRequest(), ""))
            read = asyncio.run(main.read_pan_web_session(_StubRequest(), ""))
            self.assertTrue(read["empty"])
        print("ok 会话不完整：400 且不落库")

    def test_clear_session(self):
        with unittest.mock.patch.object(main, "store", self.store):
            asyncio.run(main.write_pan_web_session(_StubRequest(), main.PanWebSessionRequest(
                authorToken="t", loginUuid="u",
            )))
            cleared = asyncio.run(main.clear_pan_web_session(_StubRequest(), ""))
            self.assertTrue(cleared["ok"])
            read = asyncio.run(main.read_pan_web_session(_StubRequest(), ""))
            self.assertTrue(read["empty"])
        print("ok 清除会话：拉取回到空")

    def test_token_guard_shares_library_config(self):
        with unittest.mock.patch.object(main, "store", self.store):
            self._set_token("k" * 24)
            # 未带令牌 → 401；正确 query → 放行；正确 Bearer → 放行
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.read_pan_web_session(_StubRequest(host="10.0.0.2"), ""))
            self.assertEqual(ctx.exception.status_code, 401)
            with self.assertRaises(HTTPException):
                asyncio.run(main.write_pan_web_session(_StubRequest(host="10.0.0.2"), main.PanWebSessionRequest(
                    authorToken="t", loginUuid="u", token="wrong",
                )))
            read = asyncio.run(main.read_pan_web_session(_StubRequest(host="10.0.0.2"), "k" * 24))
            self.assertTrue(read["ok"])
            saved = asyncio.run(main.write_pan_web_session(_StubRequest(host="10.0.0.2"), main.PanWebSessionRequest(
                authorToken="t", loginUuid="u", token="k" * 24,
            )))
            self.assertTrue(saved["ok"])
            # 脚本统一把令牌拼在 URL query 上：query 正确也要放行（body.token 可缺省）
            saved = asyncio.run(main.write_pan_web_session(_StubRequest(host="10.0.0.2"), main.PanWebSessionRequest(
                authorToken="t2", loginUuid="u2",
            ), "k" * 24))
            self.assertTrue(saved["ok"])
            cleared = asyncio.run(main.clear_pan_web_session(
                _StubRequest(host="10.0.0.2", authorization="Bearer " + "k" * 24), ""))
            self.assertTrue(cleared["ok"])
        print("ok 令牌门禁：与影库访问令牌同门（query/Bearer 均可）")


if __name__ == "__main__":
    unittest.main()
