from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from app.pan115_transfer import Pan115TransferClient, classify_pan115_account_error


class ClassifyPan115AccountErrorTests(unittest.TestCase):
    def test_expired_error_codes_and_messages(self):
        cases = [
            "[errno 990001] 需要登录账号",
            "115 取直链 请重新登录",
            "115 分享列表 未登录",
            "登录已失效，请重新登录 115",
        ]
        for message in cases:
            self.assertEqual(classify_pan115_account_error(ValueError(message)), "expired", message)

    def test_transient_rate_limit_and_pages(self):
        cases = [
            "操作频繁",
            "115 API 429",
            "too many requests",
        ]
        for message in cases:
            self.assertEqual(classify_pan115_account_error(ValueError(message)), "transient", message)

    def test_html_page_is_expired_for_account_but_retryable_for_listing(self):
        message = "115 分享列表 返回了网页页面（HTTP 200），可能是 115 Cookie 失效、需要验证或接口临时风控"
        self.assertEqual(classify_pan115_account_error(ValueError(message)), "expired")
        from app.pan115_transfer import _is_transient_share_list_error

        self.assertTrue(_is_transient_share_list_error(ValueError(message)))

    def test_share_gone_errors_take_priority_over_page_and_expired(self):
        cases = [
            "[errno 4100010] 分享已取消",
            "分享不存在",
            "该分享已过期",
            # 真实场景：分享取消后三个取直链接口报错拼接，混入"返回了网页页面"字样，
            # 曾被误判为 Cookie 失效导致整个账号池被冷却 30 分钟
            (
                "115 app downurl 200；115 取直链 返回了网页页面（HTTP 404），"
                "可能是 115 Cookie 失效、需要验证或接口临时风控；[errno 4100010] 分享已取消"
            ),
        ]
        for message in cases:
            self.assertEqual(classify_pan115_account_error(ValueError(message)), "share_gone", message)

    def test_other_errors(self):
        self.assertEqual(classify_pan115_account_error(ValueError("文件不存在")), "other")


class InspectAndFlattenTests(unittest.TestCase):
    def test_root_directory_paginates_beyond_first_page(self):
        async def run() -> None:
            client = Pan115TransferClient("UID=1; CID=c; SEID=s")
            pages = {
                0: {
                    "data": {
                        "shareinfo": {"share_title": "big", "receive_code": "abcd"},
                        "list": [
                            {"fid": f"f{i}", "n": f"file-{i}.mkv", "s": 1, "sha": "A" * 40, "fc": 1}
                            for i in range(1000)
                        ],
                        "count": 1001,
                    }
                },
                1000: {
                    "data": {
                        "list": [
                            {"fid": "f-tail", "n": "tail.mkv", "s": 2, "sha": None, "fc": 1}
                        ],
                        "count": 1001,
                    }
                },
            }

            async def fake_list(share_code, receive_code, dir_id, limit, offset):
                return pages[offset]

            client._list = AsyncMock(side_effect=fake_list)  # type: ignore[method-assign]
            try:
                inspection = await client.inspect_and_flatten({
                    "url": "https://115.com/s/abc",
                    "clean_url": "https://115.com/s/abc",
                    "share_code": "abc",
                    "receive_code": "abcd",
                })
            finally:
                await client.close()

            names = [file["name"] for file in inspection["files"]]
            self.assertEqual(len(names), 1001)
            self.assertIn("tail.mkv", names)
            self.assertEqual(inspection["title"], "big")
            # 根目录第一页（offset=0）只请求一次：shareinfo 复用后不重复拉取
            offsets = [call.args[4] for call in client._list.await_args_list]
            self.assertEqual(offsets, [0, 1000])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
