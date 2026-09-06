"""TG 用户 Session 登录流程的测试（不连真实 Telegram，telethon 用替身）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app import telegram_session
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, SessionPasswordNeededError


class _FakeStringSession:
    def save(self) -> str:
        return "1BVtsOHwBuibcFAKEsessionSTRING"


class _FakeMe:
    id = 668899
    username = "tester"
    first_name = "Test"
    last_name = "User"
    phone = "+8613800000000"


def _sent_code(class_name: str = "SentCodeTypeSms"):
    class Sent:
        phone_code_hash = "hash-1"

    Sent.type = type(class_name, (), {})()
    return Sent()


class _FakeClient:
    """scenario: ok（直接通过）/ password（先要两步验证）/ invalid（验证码错）。"""

    scenario = "ok"
    instances: list = []

    def __init__(self, session, api_id, api_hash):
        self.session = _FakeStringSession()
        self.api_id = api_id
        self.api_hash = api_hash
        self.disconnected = False
        self.signed_in_with: dict = {}
        self.phone = ""
        _FakeClient.instances.append(self)

    async def connect(self) -> None:
        return None

    async def send_code_request(self, phone):
        self.phone = phone
        return _sent_code()

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        self.signed_in_with = {"phone": phone, "code": code, "password": password, "hash": phone_code_hash}
        if self.scenario == "password" and not password:
            raise SessionPasswordNeededError(request=None)
        if self.scenario == "invalid":
            raise PhoneCodeInvalidError(request=None)
        return _FakeMe()

    async def get_me(self):
        return _FakeMe()

    async def disconnect(self) -> None:
        self.disconnected = True


class TelegramSessionLoginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await telegram_session.reset_login_state()
        _FakeClient.instances = []
        _FakeClient.scenario = "ok"
        self.patcher = patch.multiple(
            telegram_session,
            TelegramClient=_FakeClient,
            StringSession=_FakeStringSession,
        )
        self.patcher.start()

    async def asyncTearDown(self) -> None:
        self.patcher.stop()
        await telegram_session.reset_login_state()

    async def test_start_login_rejects_phone_without_country_code(self):
        with self.assertRaises(telegram_session.TelegramLoginError) as caught:
            await telegram_session.start_login("123456", "hash", "13800138000")
        self.assertIn("国际区号", str(caught.exception))

    async def test_start_login_requires_api_credentials(self):
        with self.assertRaises(telegram_session.TelegramLoginError) as caught:
            await telegram_session.start_login("", "", "+8613800000000")
        self.assertIn("API ID", str(caught.exception))

    async def test_start_login_sends_code_and_keeps_pending_session(self):
        result = await telegram_session.start_login("123456", "hash", "+86 138 0000 0000")
        self.assertTrue(result["ok"])
        self.assertTrue(result["loginId"])
        self.assertEqual(result["phone"], "+8613800000000")
        self.assertEqual(result["delivery"], "短信")
        self.assertIn(result["loginId"], telegram_session.pending_login_ids())

    async def test_verify_code_returns_session_and_account(self):
        started = await telegram_session.start_login("123456", "hash", "+8613800000000")
        result = await telegram_session.verify_code(started["loginId"], "1 2 3 4 5")
        self.assertTrue(result["ok"])
        self.assertFalse(result["needPassword"])
        self.assertEqual(result["session"], "1BVtsOHwBuibcFAKEsessionSTRING")
        self.assertEqual(result["apiId"], "123456")
        self.assertEqual(result["account"]["username"], "tester")
        self.assertEqual(result["account"]["displayName"], "Test User")
        # 登录成功后会话被清理、连接被断开
        self.assertEqual(telegram_session.pending_login_ids(), [])
        self.assertTrue(_FakeClient.instances[0].disconnected)

    async def test_verify_code_asks_for_two_step_password_then_succeeds(self):
        _FakeClient.scenario = "password"
        started = await telegram_session.start_login("123456", "hash", "+8613800000000")

        first = await telegram_session.verify_code(started["loginId"], "12345")
        self.assertTrue(first["needPassword"])
        self.assertIn("两步验证", first["message"])
        # 还没拿到 session，会话要留着等密码
        self.assertEqual(telegram_session.pending_login_ids(), [started["loginId"]])

        second = await telegram_session.verify_code(started["loginId"], "12345", "cloud-password")
        self.assertFalse(second["needPassword"])
        self.assertEqual(second["session"], "1BVtsOHwBuibcFAKEsessionSTRING")
        self.assertEqual(_FakeClient.instances[0].signed_in_with["password"], "cloud-password")

    async def test_verify_code_reports_invalid_code_in_plain_words(self):
        _FakeClient.scenario = "invalid"
        started = await telegram_session.start_login("123456", "hash", "+8613800000000")
        with self.assertRaises(telegram_session.TelegramLoginError) as caught:
            await telegram_session.verify_code(started["loginId"], "99999")
        self.assertIn("验证码不正确", str(caught.exception))
        # 验证码错不算终态，用户可以继续用同一个 loginId 重试
        self.assertEqual(telegram_session.pending_login_ids(), [started["loginId"]])

    async def test_verify_code_rejects_expired_login_id(self):
        with self.assertRaises(telegram_session.TelegramLoginError) as caught:
            await telegram_session.verify_code("no-such-login", "12345")
        self.assertIn("重新获取验证码", str(caught.exception))

    async def test_verify_code_requires_code(self):
        started = await telegram_session.start_login("123456", "hash", "+8613800000000")
        with self.assertRaises(telegram_session.TelegramLoginError) as caught:
            await telegram_session.verify_code(started["loginId"], "  ")
        self.assertIn("验证码", str(caught.exception))

    async def test_cancel_login_drops_client(self):
        started = await telegram_session.start_login("123456", "hash", "+8613800000000")
        result = await telegram_session.cancel_login(started["loginId"])
        self.assertTrue(result["ok"])
        self.assertEqual(telegram_session.pending_login_ids(), [])
        self.assertTrue(_FakeClient.instances[0].disconnected)

    async def test_start_login_failure_does_not_leak_client(self):
        class _BrokenClient(_FakeClient):
            async def connect(self):
                raise ConnectionError("network down")

        with patch.object(telegram_session, "TelegramClient", _BrokenClient):
            with self.assertRaises(telegram_session.TelegramLoginError) as caught:
                await telegram_session.start_login("123456", "hash", "+8613800000000")
        self.assertIn("连接 Telegram 失败", str(caught.exception))
        self.assertEqual(telegram_session.pending_login_ids(), [])


class TelegramSessionHelperTest(unittest.TestCase):
    def test_mask_phone(self):
        self.assertEqual(telegram_session.mask_phone("+8613800000000"), "+861****00")

    def test_normalize_phone_keeps_country_code(self):
        self.assertEqual(telegram_session.normalize_phone(" +86 (138) 0000-0000 "), "+8613800000000")

    def test_describe_error_maps_flood_wait(self):
        error = FloodWaitError(request=None, capture=30)
        self.assertIn("30 秒", str(telegram_session.describe_error(error)))


if __name__ == "__main__":
    unittest.main()
