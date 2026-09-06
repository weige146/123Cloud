import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import telegram_history
from app.telegram_history import (
    TelegramHistoryCleaner,
    publication_button_matches,
    publication_button_value,
    tmdb_publication_matches,
)
from telethon.errors import AuthKeyNotFound, FloodWaitError, SessionRevokedError


class TextWithEntitiesLike:
    def __init__(self, text):
        self.text = text


class KeyboardButtonCopyLike:
    def __init__(self, text):
        self.copy_text = TextWithEntitiesLike(text)


class KeyboardButtonUrlLike:
    def __init__(self, url):
        self.url = url


class ButtonRowLike:
    def __init__(self, *buttons):
        self.buttons = list(buttons)


class ReplyMarkupLike:
    def __init__(self, *rows):
        self.rows = list(rows)


class MessageLike:
    def __init__(self, message, reply_markup):
        self.message = message
        self.reply_markup = reply_markup


class TelegramHistoryTests(unittest.TestCase):
    def test_publication_button_value_reads_telethon_copy_text_object(self):
        markup = ReplyMarkupLike(ButtonRowLike(KeyboardButtonCopyLike("123FLCPV2$%f#1024#Movie.mkv")))

        self.assertEqual(publication_button_value(markup, True), "123FLCPV2$%f#1024#Movie.mkv")

    def test_tmdb_publication_match_supports_fastlink_copy_button(self):
        fastlink = "123FLCPV2$%f#1024#Movie.mkv"
        message = MessageLike("🎬 TMDB: 363093\n电影", ReplyMarkupLike(ButtonRowLike(KeyboardButtonCopyLike(fastlink))))

        self.assertTrue(
            tmdb_publication_matches(
                message,
                {"mediaType": "movie", "tmdbId": 363093, "shareUrl": fastlink, "fastLink": True},
            )
        )

    def test_tmdb_publication_match_supports_resource_name_when_fastlink_changes(self):
        message = MessageLike(
            "🎬 TMDB: 363093\n📦 2160p WEB-DL H265",
            ReplyMarkupLike(ButtonRowLike(KeyboardButtonCopyLike("123FLCPV2$%old#1#Season.123fastlink.json"))),
        )

        self.assertTrue(
            tmdb_publication_matches(
                message,
                {
                    "mediaType": "movie",
                    "tmdbId": 363093,
                    "shareUrl": "123FLCPV2$%new#2#Season.123fastlink.json",
                    "fastLink": True,
                    "resourceName": "2160p WEB-DL H265",
                },
            )
        )

    def test_publication_button_value_reads_url_button_and_bot_api_shape(self):
        url = "https://www.123pan.com/s/abc?pwd=ONWA"

        self.assertEqual(publication_button_value(ReplyMarkupLike(ButtonRowLike(KeyboardButtonUrlLike(url))), False), url)
        self.assertEqual(publication_button_value({"inline_keyboard": [[{"text": "秒传链接", "copy_text": {"text": "123FLCPV2$%f#x"}}]]}, True), "123FLCPV2$%f#x")

    def test_publication_button_matches_equivalent_123_share_links(self):
        post = {
            "mediaType": "movie",
            "tmdbId": 270855,
            "shareUrl": "https://www.123pan.com/s/abc.html?pwd=ABCD&from=bot",
            "fastLink": False,
        }

        self.assertTrue(publication_button_matches("https://1813278387.share.123pan.cn/123pan/abc?foo=1&pwd=abcd", post))
        self.assertTrue(
            tmdb_publication_matches(
                {
                    "message": "🎬 TMDB: 270855",
                    "replyMarkup": {"rows": [{"buttons": [{"className": "KeyboardButtonUrl", "url": "https://www.123pan.com/s/abc?pwd=ABCD"}]}]},
                },
                post,
            )
        )
        self.assertFalse(publication_button_matches("https://www.123pan.com/s/abc?pwd=EFGH", post))


CLEANUP_CONFIG = {"apiId": 123456, "apiHash": "api-hash", "session": "session-string"}
CLEANUP_POST = {"chatId": "-1001234567890", "messageId": 500, "mediaType": "movie", "tmdbId": 363093}
CLEANUP_EMPTY = {"deletedMessageIds": [], "failedMessageIds": []}


def _raise_once_then_return(exception, result):
    """首次调用抛异常、之后返回 result 的桩函数。"""
    state = {"calls": 0}

    async def stub(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise exception
        return result

    return stub, state


class TelegramHistoryCleanupResilienceTests(unittest.IsolatedAsyncioTestCase):
    """频道旧帖清理的容错行为。

    背景：session 被服务端吊销（AuthKeyNotFound）是永久性故障，原先每次投稿后都会
    新建 client 重试并刷两条 WARNING。改造后应"只记一次 ERROR、之后静默"。
    """

    def setUp(self):
        telegram_history.reset_telegram_client_state()

    def tearDown(self):
        telegram_history.reset_telegram_client_state()

    async def test_auth_key_not_found_marks_invalid_and_logs_once(self):
        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(side_effect=AuthKeyNotFound())):
            with patch.object(telegram_history.logger, "error") as error_mock:
                first = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)
                second = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(first, CLEANUP_EMPTY)
        self.assertEqual(second, CLEANUP_EMPTY)
        self.assertTrue(telegram_history.is_session_permanently_invalid)
        # 去重关键：连续两次失败只应记录一条 ERROR
        error_mock.assert_called_once()

    async def test_session_revoked_marks_invalid(self):
        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(side_effect=SessionRevokedError(None))):
            result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(result, CLEANUP_EMPTY)
        self.assertTrue(telegram_history.is_session_permanently_invalid)

    async def test_flood_wait_over_threshold_skips_without_sleep(self):
        async def raise_flood(*args, **kwargs):
            raise FloodWaitError(None, 120)

        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(return_value=object())):
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(side_effect=raise_flood)):
                with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
                    result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(result, CLEANUP_EMPTY)
        sleep_mock.assert_not_called()  # 等待过久，直接放弃本次
        self.assertFalse(telegram_history.is_session_permanently_invalid)  # 限流不等于失效

    async def test_flood_wait_within_threshold_retries_once(self):
        succeeded = {"deletedMessageIds": [7], "failedMessageIds": []}
        stub, state = _raise_once_then_return(FloodWaitError(None, 5), succeeded)

        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(return_value=object())):
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(side_effect=stub)):
                with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
                    result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(result, succeeded)
        self.assertEqual(state["calls"], 2)
        sleep_mock.assert_called_once_with(5)  # 尊重服务端要求的等待秒数

    async def test_transient_connection_error_gives_up_after_retries(self):
        async def always_fail(*args, **kwargs):
            raise ConnectionError("connection reset")

        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(return_value=object())):
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(side_effect=always_fail)):
                with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
                    result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(result, CLEANUP_EMPTY)
        self.assertEqual(sleep_mock.call_count, telegram_history.TRANSIENT_RETRIES)
        self.assertFalse(telegram_history.is_session_permanently_invalid)  # 瞬时故障不应判定为失效

    async def test_successful_cleanup_passes_through_results(self):
        succeeded = {"deletedMessageIds": [1, 2], "failedMessageIds": [3]}

        with patch.object(telegram_history, "get_telegram_client", new=AsyncMock(return_value=object())):
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(return_value=succeeded)) as cleanup_mock:
                result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(result, succeeded)
        cleanup_mock.assert_awaited_once()

    async def test_connected_client_is_reused_between_cleanups(self):
        """单例生效：连续两次清理只建立一次连接（改造前每次清理都新建 client）。"""
        fake_client = MagicMock()
        fake_client.is_connected.return_value = True

        with patch.object(telegram_history, "connect_telegram_client", new=AsyncMock(return_value=fake_client)) as connect_mock:
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(return_value=CLEANUP_EMPTY)):
                await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)
                await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        connect_mock.assert_awaited_once()

    async def test_disconnected_client_is_released_and_replaced(self):
        stale = MagicMock()
        stale.is_connected.return_value = False
        stale.disconnect = AsyncMock()
        fresh = MagicMock()
        fresh.is_connected.return_value = True
        fresh.disconnect = AsyncMock()

        with patch.object(telegram_history, "connect_telegram_client", new=AsyncMock(side_effect=[stale, fresh])) as connect_mock:
            with patch.object(telegram_history, "_do_cleanup", new=AsyncMock(return_value=CLEANUP_EMPTY)):
                await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)
                await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertEqual(connect_mock.await_count, 2)
        stale.disconnect.assert_awaited_once()  # 断线的旧 client 被释放，不泄漏

    async def test_skipped_cleanup_returns_empty_dict_not_none(self):
        """固化与 submission.py 的契约。

        cleanup_published_submission_history 用 isinstance(result, dict) 校验返回值，
        cleanup 返回 None 会让它落进 except 分支、继续刷那条 WARNING——与"静默"目标相反。
        """
        telegram_history.is_session_permanently_invalid = True

        result = await TelegramHistoryCleaner().cleanup(CLEANUP_CONFIG, CLEANUP_POST)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["deletedMessageIds"], [])
        self.assertEqual(result["failedMessageIds"], [])


if __name__ == "__main__":
    unittest.main()
