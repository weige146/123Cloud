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


class Telegram115RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "botToken": "telegram-token",
            "allowedUserIds": [456],
            "pan115Helper": {"enabled": True, "pan115Cookie": "UID=1; CID=x; SEID=y"},
        }
        main.store.delete_value(main.pan123_copy_password_key(456))

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

    def test_command_menu_includes_channel_management(self):
        self.assertEqual([item["command"] for item in main.TELEGRAM_BOT_COMMANDS], ["start", "help", "myid", "recycle", "channels"])

    def test_start_queues_default_local_path_as_telegram_task(self):
        enqueue = AsyncMock(return_value={"id": "task-1"})
        with patch.object(main.store, "read_config", return_value={"transfer": {"enabled": True, "localPath115": "/云下载"}}), \
                patch.object(main.transfer_service, "enqueue_local_path", enqueue), \
                patch.object(main, "send_telegram_text", new=AsyncMock()):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update("/start"), "telegram-token", self.config))

        self.assertTrue(handled)
        enqueue.assert_awaited_once_with("/云下载", "telegram", chat_id=123, user_id=456, message_id=99)

    def test_help_returns_new_usage_text(self):
        send = AsyncMock(return_value={"message_id": 100})
        with patch.object(main, "send_telegram_text", send):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update("/help"), "telegram-token", self.config))

        self.assertTrue(handled)
        help_text = send.await_args.args[2]
        self.assertIn("/start", help_text)
        self.assertIn("/recycle", help_text)
        self.assertIn("115 分享链接", help_text)
        self.assertIn("magnet / ed2k", help_text)

    def test_recycle_uses_pan115_helper(self):
        recycle = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        send = AsyncMock(return_value={"message_id": 100})
        with patch.object(main, "empty_115_recycle", recycle), patch.object(main, "send_telegram_text", send):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update("/recycle"), "telegram-token", self.config))

        self.assertTrue(handled)
        recycle.assert_awaited_once_with(self.config["pan115Helper"])
        self.assertIn("115 回收站清理完成", send.await_args.args[2])

    def test_share_link_queues_transfer(self):
        enqueue = AsyncMock(return_value=[{"id": "task-1"}])
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue), \
                patch.object(main, "send_telegram_text", new=AsyncMock()):
            handled = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://115.com/s/demo-share?password=abcd"),
                "telegram-token",
                self.config,
            ))

        self.assertTrue(handled)
        enqueue.assert_awaited_once_with(
            "https://115.com/s/demo-share?password=abcd",
            "telegram",
            chat_id=123,
            user_id=456,
            message_id=99,
        )

    def test_admin_external_pan123_share_queues_copy_instead_of_submission(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
                    "shareKey": "demo", "shareName": "Demo", "userId": 200, "hasPassword": False, "expired": False,
                })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "submit_submission_links", AsyncMock()) as submit:
            handled = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://www.123pan.com/s/demo"),
                "telegram-token",
                self.config,
            ))

        self.assertTrue(handled)
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.args[0], "https://www.123pan.com/s/demo")
        submit.assert_not_awaited()

    def test_admin_gsb_share_uses_canonical_official_origin_for_copy(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
                    "shareKey": "MVkkjv-tufUd", "shareName": "Demo", "userId": 200, "hasPassword": False, "expired": False,
                })) as info, \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "submit_submission_links", AsyncMock()):
            handled = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://1819914790.share.123pan.cn/gsb/s/MVkkjv-tufUd"),
                "telegram-token",
                self.config,
            ))

        self.assertTrue(handled)
        info.assert_awaited_once_with("https://1819914790.share.123pan.cn/s/MVkkjv-tufUd")
        self.assertEqual(enqueue.await_args.args[0], "https://1819914790.share.123pan.cn/s/MVkkjv-tufUd")

    def test_admin_own_and_external_pan123_links_are_split(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        submit = AsyncMock(return_value={"draftCount": 1})

        async def share_info(url):
            user_id = 100 if "/own1" in url else 200
            return {"shareKey": url.rsplit("/", 1)[-1], "shareName": "Demo", "userId": user_id, "hasPassword": False, "expired": False}

        text = "https://www.123pan.com/s/own1\nhttps://www.123pan.com/s/other1"
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(side_effect=share_info)), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "submit_submission_links", submit):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update(text), "telegram-token", self.config))

        self.assertTrue(handled)
        enqueue.assert_awaited_once()
        submit.assert_awaited_once()
        submitted_links = submit.await_args.args[1]
        self.assertEqual([item["cleanUrl"] for item in submitted_links], ["https://www.123pan.com/s/own1"])

    def test_admin_password_share_waits_for_code_then_queues_copy(self):
        info = {"shareKey": "demo", "shareName": "Demo", "userId": 200, "hasPassword": True, "expired": False}
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(return_value=info)), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "send_telegram_text", AsyncMock()):
            first = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://www.123pan.com/s/demo"), "telegram-token", self.config
            ))
            second = asyncio.run(main.handle_transfer_telegram_update(self.update("ABCD"), "telegram-token", self.config))

        self.assertTrue(first)
        self.assertTrue(second)
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.args[1], "ABCD")

    def test_admin_share_without_owner_uid_is_not_copied(self):
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        send = AsyncMock()
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(return_value={
                    "shareKey": "demo", "shareName": "Demo", "userId": 0, "hasPassword": False, "expired": False,
                })), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "send_telegram_text", send):
            handled = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://www.123pan.com/s/demo"), "telegram-token", self.config
            ))

        self.assertTrue(handled)
        enqueue.assert_not_awaited()
        self.assertIn("未返回 UserID", send.await_args.args[2])

    def test_admin_multiple_password_shares_do_not_reuse_one_source_password(self):
        info = {"shareName": "Demo", "userId": 200, "hasPassword": True, "expired": False}
        enqueue = AsyncMock(return_value={"id": "copy-1"})
        send = AsyncMock()
        text = "https://www.123pan.com/s/first\nhttps://www.123pan.com/s/second\n提取码：ABCD"
        with patch.object(main.store, "read_session", return_value={"token": "token", "loginUuid": "uuid", "profile": {"uid": 100}}), \
                patch.object(main.pan123, "get_share_info", AsyncMock(return_value=info)), \
                patch.object(main.transfer_service, "enqueue_pan123_share_copy", enqueue), \
                patch.object(main, "send_telegram_text", send):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update(text), "telegram-token", self.config))

        self.assertTrue(handled)
        enqueue.assert_not_awaited()
        self.assertTrue(any("分别发送" in call.args[2] for call in send.await_args_list))

    def test_non_admin_pan123_share_is_not_intercepted(self):
        config = {**self.config, "telegramAdminUserIds": [999], "allowedUserIds": [456]}
        with patch.object(main.pan123, "get_share_info", AsyncMock()) as info:
            handled = asyncio.run(main.handle_transfer_telegram_update(
                self.update("https://www.123pan.com/s/demo"), "telegram-token", config
            ))

        self.assertFalse(handled)
        info.assert_not_awaited()

    def test_magnet_uses_pan115_helper_offline(self):
        offline = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        send = AsyncMock(return_value={"message_id": 101})
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        with patch.object(main, "submit_115_offline_from_text", offline), patch.object(main, "send_telegram_text", send):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update(magnet), "telegram-token", self.config))

        self.assertTrue(handled)
        offline.assert_awaited_once_with(self.config["pan115Helper"], magnet)
        self.assertIn("115 离线提交完成", send.await_args.args[2])

    def test_mixed_share_and_magnet_runs_both_routes(self):
        enqueue = AsyncMock(return_value=[{"id": "task-1"}])
        offline = AsyncMock(return_value={"total": 1, "success": 1, "failed": 0})
        text = "https://115.com/s/demo-share?password=abcd\nmagnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        with patch.object(main.transfer_service, "enqueue_from_text", enqueue), \
                patch.object(main, "submit_115_offline_from_text", offline), \
                patch.object(main, "send_telegram_text", new=AsyncMock()):
            handled = asyncio.run(main.handle_transfer_telegram_update(self.update(text), "telegram-token", self.config))

        self.assertTrue(handled)
        enqueue.assert_awaited_once()
        offline.assert_awaited_once()

    def test_legacy_pan115_commands_are_not_handled(self):
        handled = asyncio.run(main.handle_transfer_telegram_update(self.update("/pan115status"), "telegram-token", self.config))
        self.assertFalse(handled)

    def test_queued_message_returns_reference_for_success_cleanup(self):
        task = {
            "id": "task-1",
            "source": "telegram",
            "shareCode": "local:/云下载",
            "sourceText": "/云下载",
            "chatId": 123,
        }
        with patch.object(main.store, "read_submission_config", return_value=self.config), \
                patch.object(main, "send_telegram_text", new=AsyncMock(return_value={"message_id": 100})):
            refs = asyncio.run(main.send_telegram_transfer_queued_messages([task]))

        self.assertEqual(refs, [{"taskId": "task-1", "chatId": 123, "messageId": 100}])

    def test_cleanup_uses_current_bot_token_and_all_recorded_ids(self):
        delete = AsyncMock()
        payload = {
            "task": {"source": "telegram"},
            "chatId": 123,
            "messageIds": [99, 100],
        }
        with patch.object(main.store, "read_submission_config", return_value=self.config), \
                patch.object(main, "delete_telegram_messages", delete):
            asyncio.run(main.cleanup_telegram_transfer_messages(payload))

        delete.assert_awaited_once_with("telegram-token", 123, [99, 100])


def tearDownModule() -> None:
    _DATA_DIR.cleanup()
