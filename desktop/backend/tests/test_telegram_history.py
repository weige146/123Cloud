import unittest

from app.telegram_history import publication_button_matches, publication_button_value, tmdb_publication_matches


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


if __name__ == "__main__":
    unittest.main()
