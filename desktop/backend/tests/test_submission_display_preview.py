import unittest

from app.submission import build_submission_display_preview


class SubmissionDisplayPreviewTests(unittest.TestCase):
    def test_preview_renders_caption_and_configured_source_label(self):
        preview = build_submission_display_preview(
            {
                "templates": {
                    "shareName": "123",
                    "shareUrl": "https://example.test/share",
                    "caption": "<b>{title}</b>\n{source}\n{mpName}\n{shareLink}\n{overviewBlock}",
                },
                "ruleConfig": {
                    "display": {
                        "sourceLabels": [
                            {
                                "enabled": True,
                                "source": "UHD BluRay Remux",
                                "template": "{{resolution4k}}蓝光原盘REMUX",
                                "order": 100,
                            }
                        ]
                    }
                },
            },
            {
                "title": "仙逆",
                "year": "2023",
                "mediaType": "tv",
                "quality": "2160p",
                "source": "UHD BluRay Remux",
                "videoCodec": "HEVC",
                "audioCodec": "DDP2.0",
                "releaseGroup": "HiveWeb",
                "seasonEpisode": "S01E01",
                "overview": "示例简介",
                "fileNames": ["Renegade.Immortal.S01E01.2023.2160p.mkv"],
            },
        )

        self.assertIn("<b>仙逆 (2023)</b>", preview["caption"])
        self.assertIn("4K蓝光原盘REMUX", preview["resourceName"])
        self.assertEqual(preview["sourceLabel"], "4K蓝光原盘REMUX")
        self.assertEqual(preview["shareLink"], '<a href="https://example.test/share">123</a>')
        self.assertIn("示例简介", preview["overviewBlock"])
        self.assertEqual(preview["routeChannel"], "未匹配")

    def test_preview_escapes_sample_values_and_has_defaults(self):
        preview = build_submission_display_preview(
            {"templates": {"caption": "{title}\n{overviewBlock}"}},
            {"title": "<script>alert(1)</script>", "overview": "<b>not html</b>"},
        )

        self.assertNotIn("<script>", preview["caption"])
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", preview["caption"])
        self.assertIn("&lt;b&gt;not html&lt;/b&gt;", preview["caption"])
        self.assertIn("<script>alert(1)</script>", preview["text"])

        default_preview = build_submission_display_preview({}, None)
        self.assertEqual(default_preview["resourceName"], "")
        self.assertIn("示例媒体", default_preview["caption"])


if __name__ == "__main__":
    unittest.main()
