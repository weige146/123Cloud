"""route_submission_text 分流核心测试：油猴脚本 / 后台提交统一入口。

- 第三方 123 分享 → 123 转存任务（搬运）
- 自己的 123 分享 / 秒传 → 投稿草稿
- 115 分享链接 → 115 搬运任务
- magnet / ed2k → 115 助手离线下载
"""
from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


_DATA_DIR = tempfile.TemporaryDirectory()
_PREVIOUS_DATA_DIR = os.environ.get("DATA_DIR")
os.environ["DATA_DIR"] = _DATA_DIR.name
main = importlib.import_module("app.main")
if _PREVIOUS_DATA_DIR is None:
    os.environ.pop("DATA_DIR", None)
else:
    os.environ["DATA_DIR"] = _PREVIOUS_DATA_DIR


class SubmissionRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        main.store.write_config({"transfer": {"enabled": True, "localPath115": "/云下载"}})
        main.store.write_session({"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}})

    def route(self, text: str, **kwargs):
        return asyncio.run(main.route_submission_text(text, "油猴投稿", **kwargs))

    def test_external_pan123_share_queues_copy_not_submission(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
            "shareKey": "demo", "shareName": "Demo", "userId": 200, "hasPassword": False, "expired": False,
        })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "submit_submission_links", AsyncMock()) as submit:
            result = self.route("https://www.123pan.com/s/demo")

        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["transfers"], 1)
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.args[0], "https://www.123pan.com/s/demo")
        submit.assert_not_awaited()

    def test_share_password_in_url_is_passed_to_copy_task(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
            "shareKey": "demo", "shareName": "Demo", "userId": 200, "hasPassword": True, "expired": False,
        })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue):
            result = self.route("https://www.123pan.com/s/demo?pwd=ABCD")

        self.assertEqual(result["transfers"], 1)
        self.assertEqual(enqueue.await_args.args[1], "ABCD")

    def test_password_share_without_code_fails_with_reason(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
            "shareKey": "demo", "shareName": "Demo", "userId": 200, "hasPassword": True, "expired": False,
        })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue):
            result = self.route("https://www.123pan.com/s/demo")

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["transfers"], 0)
        enqueue.assert_not_awaited()
        self.assertTrue(any("提取码" in item for item in result["failures"]))

    def test_own_and_external_pan123_links_are_split(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        submit = AsyncMock(return_value={"draftCount": 1, "sentCount": 1})

        async def share_info(url):
            user_id = 100 if "/own1" in url else 200
            return {"shareKey": url.rsplit("/", 1)[-1], "shareName": "Demo", "userId": user_id, "hasPassword": False, "expired": False}

        text = "https://www.123pan.com/s/own1\nhttps://www.123pan.com/s/other1"
        with patch.object(main.pan123, "get_share_info", AsyncMock(side_effect=share_info)), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "submit_submission_links", submit):
            result = self.route(text)

        self.assertEqual(result["transfers"], 1)
        self.assertEqual(result["drafts"], 1)
        self.assertEqual(result["accepted"], 2)
        enqueue.assert_awaited_once()
        submit.assert_awaited_once()
        submitted_links = submit.await_args.args[1]
        self.assertEqual([item["cleanUrl"] for item in submitted_links], ["https://www.123pan.com/s/own1"])

    def test_gsb_share_uses_canonical_official_origin_for_copy(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
            "shareKey": "MVkkjv-tufUd", "shareName": "Demo", "userId": 200, "hasPassword": False, "expired": False,
        })) as info, \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue):
            self.route("https://1819914790.share.123pan.cn/gsb/s/MVkkjv-tufUd")

        info.assert_awaited_once_with("https://1819914790.share.123pan.cn/s/MVkkjv-tufUd")
        self.assertEqual(enqueue.await_args.args[0], "https://1819914790.share.123pan.cn/s/MVkkjv-tufUd")

    def test_share_without_owner_uid_is_reported_as_failure(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
            "shareKey": "demo", "shareName": "Demo", "userId": 0, "hasPassword": False, "expired": False,
        })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue):
            result = self.route("https://www.123pan.com/s/demo")

        enqueue.assert_not_awaited()
        self.assertTrue(any("未返回 UserID" in item for item in result["failures"]))

    def test_not_logged_in_reports_failure_for_pan123_links(self):
        main.store.write_session({})
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue):
            result = self.route("https://www.123pan.com/s/demo")

        enqueue.assert_not_awaited()
        self.assertTrue(any("未登录" in item for item in result["failures"]))

    def test_115_share_link_queues_transfer(self):
        enqueue = AsyncMock(return_value=[{"id": "task-1"}])
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue):
            result = self.route("https://115.com/s/demo-share?password=abcd 提取码：abcd")

        self.assertEqual(result["transfers"], 1)
        self.assertEqual(result["accepted"], 1)
        enqueue.assert_awaited_once()

    def test_magnet_uses_pan115_helper_offline(self):
        offline = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        with patch.object(main, "submit_115_offline_from_text", offline):
            result = self.route(magnet)

        self.assertEqual(result["offline"], 1)
        offline.assert_awaited_once()

    def test_mixed_share_and_magnet_runs_both_routes(self):
        enqueue = AsyncMock(return_value=[{"id": "task-1"}])
        offline = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        text = "https://115.com/s/demo-share?password=abcd\nmagnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue), \
                patch.object(main, "submit_115_offline_from_text", offline):
            result = self.route(text)

        self.assertEqual(result["transfers"], 1)
        self.assertEqual(result["offline"], 1)
        self.assertEqual(result["accepted"], 2)

    def test_text_without_links_reports_failure(self):
        result = self.route("这是一段没有链接的文字")
        self.assertEqual(result["accepted"], 0)
        self.assertTrue(result["failures"])


class TelegramBotTextRoutingTests(unittest.TestCase):
    """机器人文本入口（管理员发 115 链接 / 磁力 / 命令），走最小化轮询。"""

    def setUp(self) -> None:
        self.config = {
            "botToken": "telegram-token",
            "telegramAdminUserIds": [456],
            "pan115Helper": {"enabled": True, "pan115Cookie": "UID=1; CID=x; SEID=y"},
        }

    @staticmethod
    def update(text: str) -> dict:
        return {
            "message": {
                "message_id": 99,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": text,
            }
        }

    def test_115_share_link_queues_transfer(self):
        enqueue = AsyncMock(return_value=[{"id": "task-1"}])
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue):
            handled = asyncio.run(main.handle_telegram_button_update(
                self.update("https://115.com/s/demo-share?password=abcd"), "telegram-token", self.config
            ))
        enqueue.assert_awaited_once()
        self.assertTrue(handled is None)  # 按钮循环不返回 handled 标志，正常处理完即可

    def test_magnet_uses_pan115_helper_offline(self):
        offline = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        with patch.object(main, "submit_115_offline_from_text", offline), \
                patch.object(main, "send_telegram_text", AsyncMock(return_value={"message_id": 101})):
            magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            asyncio.run(main.handle_telegram_button_update(self.update(magnet), "telegram-token", self.config))
        offline.assert_awaited_once_with(self.config["pan115Helper"], magnet)

    def test_recycle_command_uses_pan115_helper(self):
        recycle = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        send = AsyncMock(return_value={"message_id": 100})
        delete = AsyncMock()
        with patch.object(main, "empty_115_recycle", recycle), \
                patch.object(main, "send_telegram_text", send), \
                patch.object(main, "delete_telegram_messages", delete):
            asyncio.run(main.handle_telegram_button_update(self.update("/recycle"), "telegram-token", self.config))
        recycle.assert_awaited_once_with(self.config["pan115Helper"])

    def test_non_admin_text_is_ignored(self):
        config = {**self.config, "telegramAdminUserIds": [999]}
        enqueue = AsyncMock()
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue):
            asyncio.run(main.handle_telegram_button_update(
                self.update("https://115.com/s/demo-share?password=abcd"), "telegram-token", config
            ))
        enqueue.assert_not_awaited()


    def test_queued_message_returns_reference_for_success_cleanup(self):
        task = {
            "id": "task-1",
            "source": "telegram",
            "shareCode": "local:/云下载",
            "sourceText": "/云下载",
            "chatId": 123,
        }
        with patch.object(main.store, "read_submission_config", return_value=self.config), \
                patch.object(main, "send_telegram_text", AsyncMock(return_value={"message_id": 100})):
            refs = asyncio.run(main.send_telegram_transfer_queued_messages([task]))

        # 引用记录在第一个管理员聊天上（456），成功后按它清理
        self.assertEqual(refs, [{"taskId": "task-1", "chatId": 456, "messageId": 100}])

    def test_cleanup_deletes_recorded_messages(self):
        delete = AsyncMock()
        payload = {"chatId": 123, "messageIds": [99, 100]}
        with patch.object(main.store, "read_submission_config", return_value=self.config), \
                patch.object(main, "delete_telegram_messages", delete):
            asyncio.run(main.cleanup_telegram_transfer_messages(payload))

        delete.assert_awaited_once_with("telegram-token", 123, [99, 100])


if __name__ == "__main__":
    unittest.main()
