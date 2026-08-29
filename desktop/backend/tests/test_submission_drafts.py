import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.session_store import SessionStore
from app.defaults import DEFAULT_SUBMISSION_CONFIG
from app.submission import (
    build_submission_draft,
    build_share_markup_row,
    collect_release_group,
    append_submission_draft,
    database_note_mode,
    extract_release_group,
    is_completed_media,
    save_share_media_cache,
    save_submission_draft,
    select_submission_channel,
    send_submission_preview,
    send_submission_preview_result,
    share_media_cache_key,
    handle_submission_telegram_update,
    inspect_web_share,
    build_submission_resource_name,
    recognize_submission_metadata,
    publish_submission_draft,
    render_submission_caption,
    send_telegram_rich_message,
    submission_publication_identity,
    submit_submission_links,
    submission_channel_candidates,
    telegram_submission_allowed,
    schedule_published_submission_history_cleanup,
    _detect_multi_version_note,
)


class SubmissionDraftTests(unittest.TestCase):
    def test_submission_prefers_douban_rating_from_source_text(self):
        draft = asyncio.run(
            build_submission_draft(
                {"templates": {"caption": "🍿 豆瓣评分：{doubanRating}"}},
                {
                    "url": "123FLCPV2$%f#1#demo.mkv",
                    "cleanUrl": "123FLCPV2$%f#1#demo.mkv",
                    "provider": "123fastlink",
                    "sourceText": "哪吒之魔童闹海 (2025) ⭐豆瓣 8.5",
                },
                {"title": "哪吒之魔童闹海 (2025)", "fileNames": ["Ne.Zha.2.2025.2160p.mkv"]},
                "秒传链接",
                {"tmdbId": 980477, "mediaType": "movie", "title": "哪吒之魔童闹海", "year": "2025"},
            )
        )

        self.assertEqual(draft["media"]["doubanRating"], 8.5)
        self.assertIn("🍿 豆瓣评分：8.5/10", draft["caption"])

    def test_cached_media_is_enriched_with_douban_rating(self):
        cached_media = {
            "tmdbId": 1292052,
            "imdbId": "tt0111161",
            "mediaType": "movie",
            "title": "肖申克的救赎",
            "year": "1994",
        }
        fetch = AsyncMock(
            return_value={
                "doubanRating": 9.7,
                "doubanUrl": "https://movie.douban.com/subject/1292052/",
                "doubanId": "1292052",
            }
        )

        with patch("app.tmdb.fetch_douban_rating", fetch):
            draft = asyncio.run(
                build_submission_draft(
                    {"tmdbToken": "token", "templates": {"caption": "{doubanRating}"}},
                    {"url": "https://www.123pan.com/s/demo", "cleanUrl": "https://www.123pan.com/s/demo"},
                    {"title": "肖申克的救赎 (1994)", "fileNames": []},
                    "Telegram 投稿",
                    cached_media,
                )
            )

        self.assertEqual(draft["media"]["doubanRating"], 9.7)
        self.assertEqual(draft["caption"], '<a href="https://movie.douban.com/subject/1292052/">9.7/10</a>')
        fetch.assert_awaited_once_with("tt0111161", "肖申克的救赎", "1994", "movie")

    def test_collaborator_post_uses_its_telegram_identity_instead_of_global_share_name(self):
        config = {
            "telegramAdminUserIds": [101],
            "templates": {
                "shareName": "管理员名称",
                "shareUrl": "https://example.test/admin-share",
                "caption": "👤 分享：{shareLink}",
            },
        }
        draft = asyncio.run(
            build_submission_draft(
                config,
                {"url": "https://www.123pan.com/s/demo", "cleanUrl": "https://www.123pan.com/s/demo", "title": "Demo"},
                {"title": "Demo", "fileNames": []},
                "Telegram 投稿",
                {"title": "Demo", "mediaType": "movie", "year": "2026"},
                owner_user_id=202,
                submitter={"id": 202, "username": "friend_submitter", "first_name": "朋友"},
            )
        )

        self.assertEqual(draft["submitter"]["username"], "friend_submitter")
        self.assertIn("👤 分享：@friend_submitter", draft["caption"])
        self.assertNotIn("管理员名称", draft["caption"])
        self.assertEqual(build_share_markup_row(draft, config)[0]["text"], "@friend_submitter网盘")

    def test_collaborator_only_sees_granted_channels_and_cannot_use_foreign_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            config = store.write_submission_config({"botToken": "telegram-token", "telegramAdminUserIds": [101], "channelOwnerUserIds": [101]})
            store.write_user_channel_config(
                101,
                {"channels": [{"id": "allowed", "title": "Allowed", "chatId": "-1001", "allowedUserIds": [202]}, {"id": "hidden", "title": "Hidden", "chatId": "-1002", "allowedUserIds": [303]}], "routing": {}},
            )
            self.assertTrue(telegram_submission_allowed(config, 202, store))
            self.assertTrue(telegram_submission_allowed(config, 303, store))
            self.assertFalse(telegram_submission_allowed(config, 404, store))
            self.assertEqual([item["channel"]["title"] for item in submission_channel_candidates(store, 202)], ["Allowed"])

            save_submission_draft(
                store,
                {
                    "id": "collab-draft",
                    "ownerChatId": 202,
                    "ownerUserId": 202,
                    "routeOwnerUserId": 101,
                    "channelId": "allowed",
                    "share": {"cleanUrl": "https://www.123pan.com/s/demo"},
                },
            )
            update = {"callback_query": {"id": "forged", "data": "sub:collab-draft:publish", "from": {"id": 303}, "message": {"message_id": 1, "chat": {"id": 303, "type": "private"}}}}
            with patch("app.submission.answer_callback_query", AsyncMock()):
                result = asyncio.run(handle_submission_telegram_update(store, update))
            self.assertEqual(result["reason"], "draft_owner_mismatch")

    def test_channel_owner_can_manage_only_their_own_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config({"botToken": "telegram-token", "channelOwnerUserIds": [101]})
            add_update = {"message": {"message_id": 1, "text": "/channel_add main | Main | -1001 | private", "from": {"id": 101}, "chat": {"id": 101, "type": "private"}}}
            allow_update = {"message": {"message_id": 2, "text": "/channel_allow main | 202", "from": {"id": 101}, "chat": {"id": 101, "type": "private"}}}
            denied_update = {"message": {"message_id": 3, "text": "/channels", "from": {"id": 202}, "chat": {"id": 202, "type": "private"}}}
            with patch("app.submission.send_telegram_text", AsyncMock()):
                asyncio.run(handle_submission_telegram_update(store, add_update))
                asyncio.run(handle_submission_telegram_update(store, allow_update))
                result = asyncio.run(handle_submission_telegram_update(store, denied_update))
            self.assertTrue(store.channel_user_allowed(101, "main", 202))
            self.assertEqual(result["reason"], "channel_owner_required")

    def test_any_private_user_can_get_their_own_uid_without_being_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config({"botToken": "telegram-token"})
            update = {"message": {"message_id": 1, "text": "/myid", "from": {"id": 202}, "chat": {"id": 202, "type": "private"}}}
            send = AsyncMock()
            with patch("app.submission.send_telegram_text", send):
                result = asyncio.run(handle_submission_telegram_update(store, update))
            self.assertEqual(result["action"], "myid")
            self.assertIn("202", send.await_args.args[2])

    def test_channels_command_opens_the_one_page_configuration_card(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config({
                "botToken": "telegram-token",
                "channelOwnerUserIds": [101],
                "channelSettingsUrl": "https://example.test/admin/channel-settings",
            })
            store.write_user_channel_config(101, {"channels": [{"id": "main", "title": "Main", "chatId": "-1001"}], "routing": {"fallbackChannelId": "main"}})
            update = {"message": {"message_id": 1, "text": "/channels", "from": {"id": 101}, "chat": {"id": 101, "type": "private"}}}
            send = AsyncMock()
            with patch("app.submission.send_telegram_text", send):
                result = asyncio.run(handle_submission_telegram_update(store, update))

            self.assertEqual(result["action"], "channels")
            self.assertEqual(send.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"], "https://example.test/admin/channel-settings")
            self.assertIn("一张卡片", send.await_args.args[2])

    def test_shared_channel_skips_telethon_history_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_user_channel_config(101, {"channels": [{"id": "a", "title": "A", "chatId": "-100-shared"}], "routing": {}})
            store.write_user_channel_config(202, {"channels": [{"id": "b", "title": "B", "chatId": "-100-shared"}], "routing": {}})

            async def run() -> None:
                with patch("app.submission.cleanup_published_submission_history", AsyncMock()) as cleanup:
                    schedule_published_submission_history_cleanup(
                        store,
                        {"telegramApi": {"apiId": "1", "apiHash": "hash", "session": "session"}},
                        {"id": "draft"},
                        "-100-shared",
                        1,
                    )
                    await asyncio.sleep(0)
                    cleanup.assert_not_awaited()

            asyncio.run(run())
    def test_fastlink_generates_legacy_style_submission_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "channels": [
                        {"id": "private", "title": "私有", "chatId": "-1001", "enabled": True, "isDefault": True, "role": "private"}
                    ],
                    "routing": {"fallbackChannelId": "private", "releaseGroupChannelId": "private"},
                }
            )
            send_mock = AsyncMock()
            with patch("app.submission.send_telegram_text", send_mock):
                result = asyncio.run(
                    submit_submission_links(
                        store,
                        [
                            {
                                "url": "123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                                "cleanUrl": "123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                                "provider": "123fastlink",
                                "title": "仙逆 (2023) {tmdb-223911}",
                                "sourceText": "🎬：仙逆 (2023) {tmdb-223911}\n💾：1 GB\n📄：仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv\n🔗：123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                                "inspection": {
                                    "title": "仙逆 (2023) {tmdb-223911}",
                                    "fileNames": ["仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv"],
                                    "size": "1GB",
                                    "rawText": "仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv",
                                },
                            }
                        ],
                        "秒传链接",
                        123456,
                    )
                )

            self.assertEqual(result["draftCount"], 1)
            self.assertEqual(result["sentCount"], 1)
            draft = result["drafts"][0]
            self.assertEqual(draft["channelTitle"], "私有")
            self.assertEqual(draft["channelId"], "")
            self.assertEqual(draft["share"]["provider"], "123fastlink")
            self.assertTrue(draft["sent"])
            self.assertEqual(draft["sentCount"], 1)
            self.assertIn("📺 TMDB: 223911", draft["caption"])
            self.assertIn("📣 路由：私有", draft["caption"])
            self.assertIn("🖥️ 画质：2160p", draft["caption"])
            self.assertIn("💽 视频：WEB-DL", draft["caption"])
            self.assertNotIn("🔗：123FLCPV2", draft["caption"])
            send_mock.assert_awaited_once()
            token, chat_id, text = send_mock.await_args.args
            self.assertEqual(token, "telegram-token")
            self.assertEqual(chat_id, 123456)
            self.assertIn("📺 TMDB: 223911", text)
            self.assertEqual(send_mock.await_args.kwargs.get("parse_mode"), "HTML")
            self.assertIn("reply_markup", send_mock.await_args.kwargs)

    def test_fastlink_json_preview_does_not_send_seed_document_to_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "templates": {"caption": "🎬 <b>{title}</b>\n{shareUrl}\n📣 路由：{routeChannel}"},
                    "channels": [
                        {"id": "private", "title": "私有", "chatId": "-1001", "enabled": True, "isDefault": True, "role": "private"}
                    ],
                }
            )
            send_text = AsyncMock(return_value={"message_id": 88})
            send_document = AsyncMock(return_value={"message_id": 89})
            link = {
                "url": "123fastlink-json://abc/Season.123fastlink.json",
                "cleanUrl": "123fastlink-json://abc/Season.123fastlink.json",
                "provider": "123fastlink",
                "title": "仙逆 (2023) {tmdb-223911}",
                "sourceText": "🎬：仙逆 (2023) {tmdb-223911}\n📄：仙逆.S01E01.2160p.WEB-DL.mkv\n📎：Season.123fastlink.json",
                "inspection": {
                    "title": "仙逆 (2023) {tmdb-223911}",
                    "fileNames": ["仙逆.S01E01.2160p.WEB-DL.mkv"],
                    "size": "1GB",
                    "rawText": "仙逆.S01E01.2160p.WEB-DL.mkv",
                },
                "documents": [
                    {
                        "type": "fastlink_json",
                        "fileName": "Season.123fastlink.json",
                        "mimeType": "application/json",
                        "content": '{"files":[]}',
                    }
                ],
            }

            with patch("app.submission.send_telegram_text", send_text), patch("app.submission.send_telegram_document", send_document):
                result = asyncio.run(submit_submission_links(store, [link], "秒传链接", 123456))

            draft = result["drafts"][0]
            self.assertEqual(draft["documents"][0]["fileName"], "Season.123fastlink.json")
            self.assertNotIn("123fastlink-json://", draft["caption"])
            send_document.assert_not_called()
            rows = send_text.await_args.kwargs["reply_markup"]["inline_keyboard"]
            self.assertFalse(any(row[0].get("text") == "秒传链接" for row in rows))
            self.assertTrue(any(row[0].get("text") == "📣 发布到频道" for row in rows))

    def test_submission_resubmitting_same_link_cleans_stale_draft_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "channels": [
                        {"id": "private", "title": "私有", "chatId": "-1001", "enabled": True, "isDefault": True, "role": "private"}
                    ],
                    "routing": {"fallbackChannelId": "private", "releaseGroupChannelId": "private"},
                }
            )
            message_id = 100
            deleted = []

            async def fake_send_text(*args, **kwargs):
                nonlocal message_id
                message_id += 1
                return {"message_id": message_id}

            async def fake_delete_messages(_token, chat_id, ids):
                deleted.append((chat_id, list(ids)))

            link = {
                "url": "123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                "cleanUrl": "123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                "provider": "123fastlink",
                "title": "仙逆 (2023) {tmdb-223911}",
                "sourceText": "🎬：仙逆 (2023) {tmdb-223911}\n💾：1 GB\n📄：仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv\n🔗：123FLCPV2$%f#1024#Renegade.Immortal.S01E01.mkv",
                "inspection": {
                    "title": "仙逆 (2023) {tmdb-223911}",
                    "fileNames": ["仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv"],
                    "size": "1GB",
                    "rawText": "仙逆.2023.S01E01.2160p.WEB-DL.H265.mkv",
                },
            }
            with patch("app.submission.send_telegram_text", side_effect=fake_send_text), patch("app.submission.delete_telegram_messages", side_effect=fake_delete_messages):
                first = asyncio.run(submit_submission_links(store, [link], "秒传链接", 123456))
                second = asyncio.run(submit_submission_links(store, [link], "秒传链接", 123456))

            drafts = store.read_value("submission_drafts")
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["id"], second["drafts"][0]["id"])
            self.assertNotEqual(first["drafts"][0]["id"], second["drafts"][0]["id"])
            self.assertEqual(deleted, [(123456, [0, 101, 0])])

    def test_web_share_inspection_keeps_api_file_info_and_size(self):
        async def fake_fetch(_link, _share_key, parent_file_id):
            if parent_file_id == 0:
                return [{"FileId": 10, "FileName": "京城奇探 (2026)", "Type": 1, "Size": 1073741824}]
            return [{"FileId": 11, "FileName": "Cases.Between.Us.S01E01.2026.2160p.WEB-DL.H.265.AAC-HiveWeb.mp4", "Type": 0, "Size": 1073741824}]

        with patch("app.submission.fetch_share_items", side_effect=fake_fetch):
            result = asyncio.run(inspect_web_share({"cleanUrl": "https://1813278387.share.123pan.cn/123pan/test?pwd=ABCD", "password": "ABCD"}))

        self.assertEqual(result["title"], "京城奇探 (2026)")
        self.assertEqual(result["size"], "1 GB")
        self.assertEqual(
            result["fileNames"],
            [
                "京城奇探 (2026)",
                "京城奇探 (2026)/Cases.Between.Us.S01E01.2026.2160p.WEB-DL.H.265.AAC-HiveWeb.mp4",
            ],
        )

    def test_submission_source_label_mapping_matches_legacy_display_rules(self):
        config = {
            "ruleConfig": {
                "display": {
                    "sourceLabels": [
                        {"enabled": True, "source": "UHD BluRay Remux", "template": "{{resolution4k}}蓝光原盘REMUX", "order": 100},
                        {"enabled": True, "source": "BluRay Remux", "template": "{{resolution}}蓝光原盘REMUX", "order": 90},
                    ]
                }
            }
        }

        name = build_submission_resource_name(
            {"quality": "2160p", "source": "UHD BluRay Remux", "videoCodec": "HEVC", "audioCodec": "TrueHD", "releaseGroup": "Group"},
            [],
            config,
        )

        self.assertIn("4K蓝光原盘REMUX", name)
        self.assertIn("HEVC", name)
        self.assertIn("[Group]", name)

    def test_submission_reuses_legacy_share_media_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            link = "123FLCPV2$%f#1024#Cached.Movie.2026.2160p.WEB-DL-HiveWeb.mkv"
            cached_media = {
                "tmdbId": 301345,
                "mediaType": "tv",
                "title": "京城奇探",
                "year": "2026",
                "overview": "缓存简介",
                "posterUrl": "https://image.tmdb.org/t/p/w780/poster.jpg",
                "backdropUrl": "https://image.tmdb.org/t/p/w1280/backdrop.jpg",
                "voteAverage": 8.0,
                "genres": ["剧情"],
                "status": "Ended",
                "tmdbUrl": "https://www.themoviedb.org/tv/301345",
            }
            store.write_submission_config(
                {
                    "allowedUserIds": [123456],
                    "channels": [
                        {"id": "private", "title": "私有", "chatId": "-1001", "enabled": True, "isDefault": True, "role": "private"}
                    ]
                }
            )
            save_share_media_cache(store, link, cached_media, "manual")

            result = asyncio.run(
                submit_submission_links(
                    store,
                    [
                        {
                            "url": link,
                            "cleanUrl": link,
                            "provider": "123fastlink",
                            "title": "京城奇探 (2026)",
                            "sourceText": "🎬：京城奇探 (2026)\n📄：Cached.Movie.2026.2160p.WEB-DL-HiveWeb.mkv\n🔗：" + link,
                            "inspection": {
                                "title": "京城奇探 (2026)",
                                "fileNames": ["Cached.Movie.2026.2160p.WEB-DL-HiveWeb.mkv"],
                                "size": "1GB",
                                "rawText": "Cached.Movie.2026.2160p.WEB-DL-HiveWeb.mkv",
                            },
                        }
                    ],
                    "秒传链接",
                )
            )

            draft = result["drafts"][0]
            self.assertEqual(draft["media"]["tmdbId"], 301345)
            self.assertEqual(draft["media"]["posterUrl"], "https://image.tmdb.org/t/p/w780/poster.jpg")
            self.assertIn("缓存简介", draft["caption"])

    def test_preview_uses_photo_when_media_image_exists(self):
        draft = {
            "id": "draft1",
            "caption": "🎬 <b>京城奇探 (2026)</b>",
            "share": {"provider": "123fastlink", "cleanUrl": "123FLCPV2$%f#1#a.mkv"},
            "media": {"backdropUrl": "https://image.tmdb.org/t/p/w1280/backdrop.jpg", "posterUrl": ""},
        }
        photo_mock = AsyncMock()
        text_mock = AsyncMock()
        edit_caption_mock = AsyncMock()
        with patch("app.submission.send_telegram_photo", photo_mock), \
             patch("app.submission.send_telegram_text", text_mock), \
             patch("app.submission.edit_telegram_message_caption", edit_caption_mock):
            sent = asyncio.run(send_submission_preview("telegram-token", 123456, draft, {"templates": {"shareName": "123"}}))

        self.assertEqual(sent, 1)
        photo_mock.assert_awaited_once()
        text_mock.assert_not_called()
        edit_caption_mock.assert_awaited_once()
        self.assertEqual(photo_mock.await_args[0][:3], ("telegram-token", 123456, "https://image.tmdb.org/t/p/w1280/backdrop.jpg"))
        self.assertEqual(photo_mock.await_args[0][3], "")
        self.assertEqual(edit_caption_mock.await_args[0][3], "🎬 <b>京城奇探 (2026)</b>")
        self.assertIsNotNone(edit_caption_mock.await_args[0][4])

    def test_release_group_and_route_match_legacy_project_rules(self):
        config = {
            "channels": [
                {"id": "private", "title": "私有", "enabled": True, "role": "private"},
                {"id": "updating", "title": "公开-更新", "enabled": True, "role": "public_updating"},
                {"id": "completed", "title": "公开-完结", "enabled": True, "role": "public_completed"},
            ],
            "routing": {
                "publicReleaseGroups": ["HiveWeb"],
                "releaseGroupChannelId": "private",
                "noReleaseGroupCompletedChannelId": "completed",
                "noReleaseGroupUpdatingChannelId": "updating",
            },
            "ruleConfig": {"recognition": {"releaseGroups": ["Mo Cuishle"]}},
        }
        self.assertEqual(extract_release_group("Cases.Between.Us.S01E01.2026.2160p.WEB-DL.60FPS.H.265.AAC-HiveWeb.mp4", config), "HiveWeb")
        self.assertEqual(extract_release_group("Movie.Name.2026.2160p.WEB-DL-Mo Cuishle.mkv", config), "Mo Cuishle")
        self.assertEqual(extract_release_group("Movie.Name.2026.1080p.WEB-DL-10.mkv", config), "10")
        self.assertEqual(
            collect_release_group(
                [
                    "航海王 (1999) {tmdb-37854}/Season 10",
                    "航海王 (1999) {tmdb-37854}/Season 1/One Piece.1999.S01E01.1080p.NF.H264.DDP 2.0-HHWEB.mkv",
                ],
                config,
            ),
            "HHWEB",
        )
        self.assertEqual(
            collect_release_group(
                [
                    "Example (2026)/Season 10",
                    "Example (2026)/Season 10/Example.S10E01.1080p.WEB-DL-10.mkv",
                ],
                config,
            ),
            "10",
        )
        no_group = recognize_submission_metadata(
            "Example (2026)",
            {
                "title": "Example (2026)",
                "fileNames": [
                    "Example (2026)/Season 10",
                    "Example (2026)/Season 10/Example.S10E01.1080p.WEB-DL.mkv",
                ],
                "rawText": "",
            },
            config,
        )
        self.assertNotIn("releaseGroup", no_group)

        draft = {
            "media": {"mediaType": "tv", "status": "Ended", "seasons": [{"seasonNumber": 1, "episodeCount": 12}]},
            "metadata": {"seasonEpisode": "S01E01-E07", "releaseGroup": "HiveWeb"},
            "inspection": {
                "title": "京城奇探 (2026) 完结",
                "fileNames": [f"Cases.Between.Us.S01E{episode:02d}.2026.2160p.WEB-DL.60FPS.H.265.AAC-HiveWeb.mp4" for episode in range(1, 8)],
            },
        }
        self.assertFalse(is_completed_media(draft))
        self.assertEqual(select_submission_channel(config, draft)["id"], "updating")

        completed_draft = {
            **draft,
            "media": {"mediaType": "tv", "status": "Returning Series", "seasons": [{"seasonNumber": 1, "episodeCount": 12}]},
            "metadata": {"seasonEpisode": "S01E01-E12", "releaseGroup": "HiveWeb"},
            "inspection": {
                "title": "京城奇探 (2026)",
                "fileNames": [f"Cases.Between.Us.S01E{episode:02d}.2026.2160p.WEB-DL.60FPS.H.265.AAC-HiveWeb.mp4" for episode in range(1, 13)],
            },
        }
        self.assertTrue(is_completed_media(completed_draft))
        self.assertEqual(select_submission_channel(config, completed_draft)["id"], "completed")

        mixed_version_files = [
            *[f"Season 1 2160p/Show.S01E{episode:02d}.2160p.WEB-DL.HiveWeb.mp4" for episode in range(1, 13)],
            *[f"Season 1 1080p/Show.S01E{episode:02d}.1080p.WEB-DL.PTer.mp4" for episode in range(1, 8)],
        ]
        mixed_version_draft = {
            **completed_draft,
            "inspection": {
                "title": "京城奇探 (2026)",
                "fileNames": mixed_version_files,
            },
        }
        self.assertFalse(is_completed_media(mixed_version_draft))
        self.assertEqual(select_submission_channel(config, mixed_version_draft)["id"], "updating")
        mixed_note = _detect_multi_version_note(mixed_version_files, config, completed_draft["media"])
        mixed_lines = mixed_note.splitlines()
        self.assertEqual(len(mixed_lines), 2)
        self.assertIn("2160p", mixed_lines[0])
        self.assertIn("完结", mixed_lines[0])
        self.assertIn("1080p", mixed_lines[1])
        self.assertIn("更新至 E07", mixed_lines[1])

        private_draft = {
            **draft,
            "metadata": {"seasonEpisode": "S01E01-E07", "releaseGroup": "Mo Cuishle"},
        }
        self.assertEqual(select_submission_channel(config, private_draft)["id"], "private")

    def test_share_and_fastlink_use_tmdb_episode_count_for_completed_season(self):
        config = {
            "templates": {"caption": "{resourceBlock}"},
            "channels": [
                {"id": "updating", "title": "公开-更新", "enabled": True, "role": "public_updating"},
                {"id": "completed", "title": "公开-完结", "enabled": True, "role": "public_completed"},
            ],
            "routing": {
                "noReleaseGroupCompletedChannelId": "completed",
                "noReleaseGroupUpdatingChannelId": "updating",
            },
        }
        media = {
            "tmdbId": 312541,
            "mediaType": "tv",
            "title": "大明暗影三百忠魂",
            "year": "2026",
            "status": "Ended",
            "seasons": [{"seasonNumber": 1, "episodeCount": 12}],
            "genres": [],
        }
        files = [f"Season 1/Show.S01E{episode:02d}.2160p.WEB-DL.HDR.60FPS.HEVC.AAC.mkv" for episode in range(1, 13)]
        inspection = {"title": "大明暗影三百忠魂 (2026) {tmdb-312541}", "fileNames": files, "size": "51.4 GB", "rawText": "\n".join(files)}

        for provider, clean_url in (
            ("123pan", "https://example.123pan.com/s/share"),
            ("123fastlink", "123fastlink-json://seed/Season.123fastlink.json"),
        ):
            with self.subTest(provider=provider):
                draft = asyncio.run(
                    build_submission_draft(
                        config,
                        {"provider": provider, "cleanUrl": clean_url, "sourceText": inspection["title"]},
                        inspection,
                        provider,
                        media,
                    )
                )
                self.assertEqual(draft["metadata"]["seasonEpisode"], "S01")
                self.assertIn("S01 2160p WEB-DL HDR 60FPS HEVC AAC", draft["caption"])
                self.assertNotIn("S01E01-E12", draft["caption"])
                self.assertEqual(draft["routeChannelId"], "completed")

    def test_fastlink_share_media_cache_key_keeps_full_link(self):
        link = "123FLCPV2$%f#1024#Cached.Movie.2026.2160p.WEB-DL-HiveWeb.mkv"
        self.assertEqual(share_media_cache_key(link), link.lower())

    def test_telegram_publish_callback_sends_channel_post_and_deletes_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "templates": {"shareName": "123", "caption": "🎬 <b>{title}</b>\n📣 路由：{routeChannel}\n👤 分享：{shareLink}"},
                    "channels": [
                        {"id": "pub", "title": "公开", "chatId": "-1002", "enabled": True, "isDefault": True, "role": "public_completed"}
                    ],
                }
            )
            draft = save_submission_draft(
                store,
                {
                    "id": "draft1",
                    "status": "draft",
                    "ownerChatId": 123456,
                    "ownerUserId": 123456,
                    "sourceMessageId": 10,
                    "previewMessageId": 20,
                    "interactionMessageIds": [30],
                    "share": {"provider": "123pan", "cleanUrl": "https://www.123pan.com/s/abc?pwd=ONWA"},
                    "inspection": {"title": "电影 (2026)", "fileNames": ["Movie.2026.2160p.WEB-DL-HiveWeb.mkv"]},
                    "metadata": {"title": "电影", "year": "2026", "mediaType": "movie", "quality": "2160p", "source": "WEB-DL"},
                    "media": {"title": "电影", "year": "2026", "mediaType": "movie", "tmdbId": 1, "genres": [], "overview": "", "posterUrl": "https://image.example/poster.jpg"},
                    "caption": "预览",
                    "text": "预览",
                },
            )
            self.assertEqual(draft["id"], "draft1")
            calls = []

            async def fake_telegram_post(token, method, payload, timeout=20.0):
                calls.append((method, payload))
                if method in {"sendMessage", "sendPhoto"}:
                    return {"message_id": 99 if payload.get("chat_id") == "-1002" else 51}
                return {}

            update = {
                "callback_query": {
                    "id": "cb1",
                    "data": "sub:draft1:publish",
                    "from": {"id": 123456},
                    "message": {"message_id": 20, "chat": {"id": 123456, "type": "private"}},
                }
            }
            with patch("app.submission.telegram_post", side_effect=fake_telegram_post):
                result = asyncio.run(handle_submission_telegram_update(store, update))

            self.assertTrue(result["handled"])
            self.assertTrue(result["ok"])
            self.assertTrue(any(method == "getChat" and payload["chat_id"] == "-1002" for method, payload in calls))
            sent = [payload for method, payload in calls if method in {"sendMessage", "sendPhoto"} and payload.get("chat_id") == "-1002"]
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["caption"], "")
            edit = next(payload for method, payload in calls if method == "editMessageCaption" and payload["chat_id"] == "-1002")
            self.assertNotIn("路由：", edit["caption"])
            self.assertEqual(edit["reply_markup"]["inline_keyboard"][0][0]["text"], "123网盘")
            self.assertEqual(store.read_value("submission_drafts"), [])

    def test_photo_caption_edit_failure_deletes_blank_photo_without_text_fallback(self):
        calls = []

        async def fake_telegram_post(token, method, payload, timeout=20.0):
            calls.append((method, payload))
            if method == "sendPhoto":
                return {"message_id": 99}
            if method == "editMessageCaption":
                raise ValueError("caption edit failed")
            return {}

        with patch("app.submission.telegram_post", side_effect=fake_telegram_post), self.assertLogs("app.submission", level="WARNING") as logs:
            with self.assertRaisesRegex(ValueError, "caption edit failed"):
                asyncio.run(
                    send_telegram_rich_message(
                        "telegram-token",
                        "-1002",
                        "<blockquote>4K蓝光原盘REMUX</blockquote>",
                        "https://image.example/poster.jpg",
                        {"inline_keyboard": [[{"text": "123网盘", "url": "https://www.123pan.com/s/abc"}]]},
                    )
                )

        self.assertIn("Telegram caption edit failed; removing blank photo", "\n".join(logs.output))
        self.assertEqual([method for method, _payload in calls], ["sendPhoto", "editMessageCaption", "deleteMessage"])
        self.assertEqual(calls[0][1]["caption"], "")
        self.assertEqual(calls[2][1], {"chat_id": "-1002", "message_id": 99})
        self.assertNotIn("sendMessage", [method for method, _payload in calls])

    def test_telegram_publish_callback_cleanup_survives_expired_callback_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "templates": {"shareName": "123", "caption": "🎬 <b>{title}</b>\n👤 分享：{shareLink}"},
                    "channels": [
                        {"id": "pub", "title": "公开", "chatId": "-1002", "enabled": True, "isDefault": True, "role": "public_completed"}
                    ],
                }
            )
            save_submission_draft(
                store,
                {
                    "id": "draft1",
                    "status": "draft",
                    "ownerChatId": 123456,
                    "ownerUserId": 123456,
                    "sourceMessageId": 10,
                    "previewMessageId": 20,
                    "interactionMessageIds": [30],
                    "share": {"provider": "123pan", "cleanUrl": "https://www.123pan.com/s/abc?pwd=ONWA"},
                    "inspection": {"title": "电影 (2026)", "fileNames": ["Movie.2026.2160p.WEB-DL-HiveWeb.mkv"]},
                    "metadata": {"title": "电影", "year": "2026", "mediaType": "movie", "quality": "2160p", "source": "WEB-DL"},
                    "media": {"title": "电影", "year": "2026", "mediaType": "movie", "tmdbId": 1, "genres": [], "overview": ""},
                    "databaseNote": {
                        "noteContent": '<span style="font-weight: bold;">频道富文本备注</span>',
                        "plainText": "频道富文本备注",
                    },
                    "caption": "预览",
                    "text": "预览",
                },
            )
            calls = []

            async def fake_telegram_post(token, method, payload, timeout=20.0):
                calls.append((method, payload))
                if method == "answerCallbackQuery":
                    raise ValueError("query is too old and response timeout expired")
                if method in {"sendMessage", "sendPhoto"}:
                    return {"message_id": 99 if payload.get("chat_id") == "-1002" else 51}
                return {}

            update = {
                "callback_query": {
                    "id": "cb1",
                    "data": "sub:draft1:publish",
                    "from": {"id": 123456},
                    "message": {"message_id": 20, "chat": {"id": 123456, "type": "private"}},
                }
            }
            with patch("app.submission.telegram_post", side_effect=fake_telegram_post), self.assertLogs("app.submission", level="WARNING") as logs:
                result = asyncio.run(handle_submission_telegram_update(store, update))

            self.assertTrue(result["ok"])
            self.assertTrue(any("Answering Telegram callback failed" in item for item in logs.output))
            deleted_ids = [payload["message_id"] for method, payload in calls if method == "deleteMessage"]
            self.assertEqual(deleted_ids, [10, 20, 30])
            channel_message = next(payload for method, payload in calls if method == "sendMessage" and payload.get("chat_id") == "-1002")
            self.assertIn("<b>频道富文本备注</b>", channel_message["text"])
            self.assertEqual(store.read_value("submission_drafts"), [])

    def test_telegram_publish_schedules_channel_history_cleanup_after_message_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "telegramApi": {"apiId": "100", "apiHash": "hash", "session": "session"},
                    "templates": {"shareName": "123", "caption": "🎬 <b>{title}</b>\n👤 分享：{shareLink}"},
                    "channels": [
                        {"id": "pub", "title": "公开", "chatId": "-1002", "enabled": True, "isDefault": True, "role": "public_completed"}
                    ],
                }
            )
            save_submission_draft(
                store,
                {
                    "id": "draft1",
                    "status": "draft",
                    "ownerChatId": 123456,
                    "ownerUserId": 123456,
                    "sourceMessageId": 10,
                    "previewMessageId": 20,
                    "interactionMessageIds": [30],
                    "share": {"provider": "123pan", "cleanUrl": "https://www.123pan.com/s/abc?pwd=ONWA"},
                    "inspection": {"title": "电影 (2026)", "fileNames": ["Movie.2026.2160p.WEB-DL-HiveWeb.mkv"]},
                    "metadata": {"title": "电影", "year": "2026", "mediaType": "movie", "quality": "2160p", "source": "WEB-DL"},
                    "media": {"title": "电影", "year": "2026", "mediaType": "movie", "tmdbId": 1, "genres": [], "overview": ""},
                    "caption": "预览",
                    "text": "预览",
                },
            )
            events = []

            async def fake_telegram_post(token, method, payload, timeout=20.0):
                if method == "deleteMessage":
                    events.append(("delete", payload["message_id"]))
                if method in {"sendMessage", "sendPhoto"}:
                    return {"message_id": 99 if payload.get("chat_id") == "-1002" else 51}
                return {}

            async def fake_cleanup(config, draft, chat_id, message_id):
                events.append(("history", message_id))
                return ""

            async def run_update():
                update = {
                    "callback_query": {
                        "id": "cb1",
                        "data": "sub:draft1:publish",
                        "from": {"id": 123456},
                        "message": {"message_id": 20, "chat": {"id": 123456, "type": "private"}},
                    }
                }
                result = await handle_submission_telegram_update(store, update)
                await asyncio.sleep(0)
                return result

            with patch("app.submission.telegram_post", side_effect=fake_telegram_post), patch("app.submission.cleanup_published_submission_history", side_effect=fake_cleanup):
                result = asyncio.run(run_update())

            self.assertTrue(result["ok"])
            self.assertEqual(events[:3], [("delete", 10), ("delete", 20), ("delete", 30)])
            self.assertIn(("history", 99), events)

    def test_telegram_publish_deletes_previous_channel_post_from_database_history_even_when_fastlink_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            config = store.write_submission_config(
                {
                    "botToken": "telegram-token",
                    "allowedUserIds": [123456],
                    "templates": {"shareName": "123", "caption": "🎬 <b>{title}</b>\n{tmdbMarker}\n{resourceBlock}\n👤 分享：{shareLink}"},
                    "channels": [
                        {"id": "pub", "title": "公开", "chatId": "-1002", "enabled": True, "isDefault": True, "role": "public_completed"}
                    ],
                }
            )
            draft = save_submission_draft(
                store,
                {
                    "id": "draft-history",
                    "status": "draft",
                    "ownerChatId": 123456,
                    "ownerUserId": 123456,
                    "share": {"provider": "123fastlink", "cleanUrl": "123fastlink-json://new/Season.123fastlink.json"},
                    "inspection": {"title": "电影 (2026)", "fileNames": ["Movie.2026.2160p.WEB-DL.H265.mkv"]},
                    "metadata": {"title": "电影", "year": "2026", "mediaType": "movie", "quality": "2160p", "source": "WEB-DL", "videoCodec": "H265"},
                    "media": {"title": "电影", "year": "2026", "mediaType": "movie", "tmdbId": 363093, "genres": [], "overview": ""},
                    "caption": "预览",
                    "text": "预览",
                    "documents": [
                        {
                            "type": "fastlink_json",
                            "fileName": "Season.123fastlink.json",
                            "mimeType": "application/json",
                            "content": '{"files":[]}',
                        }
                    ],
                },
            )
            identity = submission_publication_identity(draft, config)
            store.record_submission_publication(
                {
                    **identity,
                    "channelChatId": "-1002",
                    "channelId": "pub",
                    "messageId": 50,
                    "shareUrl": "123FLCPV2$%old#1#Season.123fastlink.json",
                    "seedMessageIds": [51],
                    "fastLink": True,
                    "draftId": "old-draft",
                }
            )
            deleted = []

            async def fake_telegram_post(token, method, payload, timeout=20.0):
                if method == "deleteMessage":
                    deleted.append(payload)
                return {}

            send_document = AsyncMock(return_value={"message_id": 100})
            with patch("app.submission.check_telegram_chat_access", AsyncMock(return_value="")), patch(
                "app.submission.send_telegram_rich_message", AsyncMock(return_value={"message_id": 99})
            ), patch("app.submission.send_telegram_document", send_document), patch("app.submission.telegram_post", side_effect=fake_telegram_post):
                result = asyncio.run(publish_submission_draft(store, "telegram-token", config, draft))

            self.assertTrue(result["ok"])
            self.assertEqual(result["seedMessageIds"], [100])
            self.assertEqual(send_document.await_args.kwargs["reply_to_message_id"], 99)
            self.assertEqual(deleted, [{"chat_id": "-1002", "message_id": 51}, {"chat_id": "-1002", "message_id": 50}])
            publications = store.find_submission_publications("-1002", identity["identityKey"], 0)
            self.assertEqual([item["messageId"] for item in publications], [99])
            self.assertEqual(publications[0]["seedMessageIds"], [100])


class RecognitionCollisionTests(unittest.TestCase):
    def test_legacy_season_rule_does_not_match_audio_channels(self):
        config = {
            **DEFAULT_SUBMISSION_CONFIG,
            "recognitionRules": [
                {
                    "enabled": True,
                    "pattern": r"(?<seasonEpisode>S\d{1,2}(?:(?:E|EP)\d{1,4})?)",
                    "flags": "i",
                },
                *DEFAULT_SUBMISSION_CONFIG["recognitionRules"],
            ],
        }

        for audio in ("DTS5.1", "DTS7.1"):
            metadata = recognize_submission_metadata(
                audio,
                {"title": audio, "fileNames": [audio], "rawText": ""},
                config,
            )
            self.assertNotIn("seasonEpisode", metadata)
            self.assertEqual(metadata["audioCodec"], audio)

    def test_media_field_collision_matrix(self):
        cases = [
            (
                "Movie.2026.2160p.NF.WEB-DL.HDR.H265.10bit.DTS5.1-HiveWeb.mkv",
                {
                    "quality": "2160p",
                    "source": "WEB-DL",
                    "resourceType": "WEB-DL",
                    "webSource": "NF",
                    "effect": "HDR",
                    "videoCodec": "HEVC",
                    "bitDepth": "10bit",
                    "audioCodec": "DTS5.1",
                    "releaseGroup": "HiveWeb",
                },
            ),
            (
                "Movie.2026.2160p.WEB-DL.H.265.60FPS.DDP5.1.Atmos-HiveWeb.mkv",
                {"videoCodec": "HEVC", "fps": "60FPS", "audioCodec": "DDP5.1 Atmos"},
            ),
            (
                "Movie.2026.2160p.WEB-DL.TrueHD7.1.Atmos-HiveWeb.mkv",
                {"audioCodec": "TrueHD7.1 Atmos"},
            ),
            (
                "Movie.2026.1080p.WEB-DL.Atmos-HiveWeb.mkv",
                {"audioCodec": "Atmos"},
            ),
            (
                "Show.S01-S03.2026.1080p.WEB-DL.AAC-HiveWeb.mkv",
                {"seasonEpisode": "S01-S03", "releaseGroup": "HiveWeb"},
            ),
        ]

        for file_name, expected in cases:
            metadata = recognize_submission_metadata(
                file_name,
                {"title": file_name, "fileNames": [file_name], "size": "12 GB", "rawText": ""},
                DEFAULT_SUBMISSION_CONFIG,
            )
            for field, value in expected.items():
                self.assertEqual(metadata.get(field), value, (file_name, field))
            self.assertEqual(metadata["size"], "12 GB")
            self.assertNotEqual(metadata.get("fps"), "265.60FPS")
            self.assertNotIn(metadata.get("releaseGroup"), {"S01", "S03", "S01-S03"})


class MultiVersionNoteTests(unittest.TestCase):
    """Test _detect_multi_version_note for TV season folders and multi-version movies."""

    def test_single_version_tv_returns_empty(self):
        """Single version TV should return empty (let resource_block handle it)."""
        files = ["Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv"]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_single_version_movie_returns_empty(self):
        """Single movie file should return empty."""
        files = ["Movie.2024.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv"]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_empty_file_list_returns_empty(self):
        self.assertEqual(_detect_multi_version_note([]), "")

    def test_multi_version_tv_season_folders(self):
        """Multi-version TV with two in-place season folders (version info in name) should generate two note lines."""
        files = [
            "Season 1 2160p/Show.S01E01.2160p.WEB-DL.DV.HEVC.AAC-HiveWeb.mkv",
            "Season 1 2160p/Show.S01E02.2160p.WEB-DL.DV.HEVC.AAC-HiveWeb.mkv",
            "Season 1 1080p/Show.S01E01.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
            "Season 1 1080p/Show.S01E02.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
        ]
        note = _detect_multi_version_note(files)
        lines = note.split("\n")
        self.assertEqual(len(lines), 2)
        # First line: Season 1 with 2160p HiveWeb
        self.assertIn("S01", lines[0])
        self.assertIn("2160p", lines[0])
        self.assertIn("HiveWeb", lines[0])
        # Second line: Season 1 with 1080p PTer
        self.assertIn("S01", lines[1])
        self.assertIn("1080p", lines[1])
        self.assertIn("PTer", lines[1])

    def test_different_versions_across_seasons_do_not_generate_multi_version_note(self):
        files = [
            "Season 1/Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Season 2/Show.S02E01.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
        ]

        self.assertEqual(_detect_multi_version_note(files), "")

    def test_multi_version_tv_ignores_completed_folder_text_without_tmdb_counts(self):
        files = [
            "Season 1 完结 2160p/Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Season 1 完结 2160p/Show.S01E08.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Season 1 完结 1080p/Show.S01E01.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
            "Season 1 完结 1080p/Show.S01E08.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
        ]
        note = _detect_multi_version_note(files)
        self.assertIn("S01", note)
        self.assertIn("含 E01、E08", note)
        self.assertNotIn(" 完结", note)

    def test_multi_version_tv_uses_tmdb_episode_count_for_completed_status(self):
        files = [
            *[f"Season 1 2160p/Show.S01E{episode:02d}.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv" for episode in range(1, 9)],
            *[f"Season 1 1080p/Show.S01E{episode:02d}.1080p.WEB-DL.HEVC.AAC-PTer.mkv" for episode in range(1, 9)],
        ]
        media = {"mediaType": "tv", "status": "Returning Series", "seasons": [{"seasonNumber": 1, "episodeCount": 8}]}
        note = _detect_multi_version_note(files, media=media)
        self.assertIn("S01", note)
        self.assertIn("完结", note)
        self.assertNotIn("更新至", note)

    def test_multi_version_tv_with_episode_progress(self):
        """Multi-version TV season folders should not call gapped episodes an update."""
        files = [
            "Season 1 2160p/Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Season 1 2160p/Show.S01E05.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Season 1 1080p/Show.S01E01.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
            "Season 1 1080p/Show.S01E05.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
        ]
        note = _detect_multi_version_note(files)
        self.assertIn("含 E01、E05", note)

    def test_historical_max_and_hmax_folders_merge_into_one_semantic_version(self):
        files = [
            *[f"Season 1 2160p MAX WEB-DL H265 AAC Group/Show.S01E{episode:02d}.mkv" for episode in range(1, 4)],
            "Season 1 4K HMAX WEB DL HEVC AAC Group/Show.S01E04.mkv",
        ]

        self.assertEqual(_detect_multi_version_note(files), "")

    def test_historical_max_and_hmax_episodes_are_merged_before_progress(self):
        files = [
            *[f"Season 1 2160p MAX WEB-DL H265 AAC Group/Show.S01E{episode:02d}.mkv" for episode in range(1, 4)],
            "Season 1 4K HMAX WEB DL HEVC AAC Group/Show.S01E04.mkv",
            "Season 1 1080p NF WEB-DL AVC AAC Other/Show.S01E01.mkv",
        ]

        note = _detect_multi_version_note(files)

        self.assertEqual(len(note.splitlines()), 2)
        self.assertIn("2160p MAX WEB-DL HEVC AAC [Group] 更新至 E04", note)
        self.assertIn("1080p NF WEB-DL AVC AAC [Other] 更新至 E01", note)

    def test_gapped_episode_status_compresses_multiple_ranges(self):
        files = [
            *[
                f"Season 1 2160p MAX WEB-DL HEVC AAC Group/Show.S01E{episode:02d}.mkv"
                for episode in (1, 3, 4, 5)
            ],
            "Season 1 1080p NF WEB-DL AVC AAC Other/Show.S01E01.mkv",
        ]

        note = _detect_multi_version_note(files)

        self.assertIn("含 E01、E03-E05", note)
        self.assertNotIn("更新至 E05", note)

    def test_multi_version_movie_two_versions(self):
        """Two distinct movie versions should generate two note lines."""
        files = [
            "Movie.2024.2160p.WEB-DL.DV.HEVC.Atmos-HiveWeb.mkv",
            "Movie.2024.1080p.BluRay.HEVC.DTS-PTer.mkv",
        ]
        note = _detect_multi_version_note(files)
        lines = note.split("\n")
        self.assertEqual(len(lines), 2)
        # Check both versions are present
        all_text = note
        self.assertIn("2160p", all_text)
        self.assertIn("1080p", all_text)
        self.assertIn("HiveWeb", all_text)
        self.assertIn("PTer", all_text)

    def test_multi_version_movie_keeps_each_file_fields_on_its_own_line(self):
        folder = "消失的人 (2026) {tmdb-1658653}"
        files = [
            folder,
            "Vanishing Point.2026.2160p.WEB-DL.50fps.H265.AAC-HiveWeb.mp4",
            f"{folder}/Vanishing Point.2026.2160p.WEB-DL.50fps.H265.AAC-HiveWeb.mp4",
            f"{folder}/Vanishing Point.2026.2160p.WEB-DL.HDR.60fps.H265.FLAC-HiveWeb.mp4",
            f"{folder}/Vanishing Point.2026.2160p.WEB-DL.HQ.DV.60fps.H265.DTS5.1-HiveWeb.mp4",
            f"{folder}/Vanishing Point.2026.2160p.WEB-DL.HQ.DV.H265.DTS5.1-HiveWeb.mp4",
        ]

        self.assertEqual(
            _detect_multi_version_note(files),
            "\n".join(
                [
                    "2160p WEB-DL 50FPS HEVC AAC [HiveWeb]",
                    "2160p WEB-DL HDR 60FPS HEVC FLAC [HiveWeb]",
                    "2160p WEB-DL HQ DV 60FPS HEVC DTS5.1 [HiveWeb]",
                    "2160p WEB-DL HQ DV HEVC DTS5.1 [HiveWeb]",
                ]
            ),
        )

    def test_multi_version_movie_same_version_returns_empty(self):
        """Multiple files with same version signature should return empty."""
        files = [
            "Movie.2024.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Movie.2024.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_multi_version_tv_with_dv_effect(self):
        """Multi-version TV with DV effect should include it in note."""
        files = [
            "Season 1 2160p DV/Show.S01E01.2160p.WEB-DL.DV.60FPS.HEVC.AAC-HiveWeb.mkv",
            "Season 1 2160p DV/Show.S01E02.2160p.WEB-DL.DV.60FPS.HEVC.AAC-HiveWeb.mkv",
            "Season 1 1080p/Show.S01E01.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
            "Season 1 1080p/Show.S01E02.1080p.WEB-DL.HEVC.AAC-PTer.mkv",
        ]
        note = _detect_multi_version_note(files)
        self.assertIn("DV", note)
        self.assertIn("HiveWeb", note)

    def test_single_version_tv_season_folder_returns_empty(self):
        """Single version TV with season folder should return empty (use resource_block)."""
        files = [
            "Season 1/Show.S01E01.2160p.WEB-DL.DV.60FPS.HEVC.AAC-HiveWeb.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_plain_season_folder_with_multiple_versions_returns_empty(self):
        """Plain 'Season N' folders (no version info) should not trigger multi-version notes."""
        files = [
            "Season 1/Show.S01E01.2160p.WEB-DL.HEVC.AAC-HHWEB.mkv",
            "Season 1/Show.S01E02.2160p.WEB-DL.HEVC.AAC-HHWEB.mkv",
            "Season 1/Show.S01E01.2160p.WEB-DL.HEVC.AAC-ADWeb.mkv",
            "Season 1/Show.S01E02.2160p.WEB-DL.HEVC.AAC-ADWeb.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_inplace_season_folder_with_multiple_versions_generates_note(self):
        """In-place season folders (with version info in name) should trigger multi-version notes."""
        files = [
            "Season 1 2160p WEB-DL HEVC AAC HHWEB/Show.S01E01.2160p.WEB-DL.HEVC.AAC-HHWEB.mkv",
            "Season 1 2160p WEB-DL HEVC AAC HHWEB/Show.S01E02.2160p.WEB-DL.HEVC.AAC-HHWEB.mkv",
            "Season 1 2160p WEB-DL HEVC AAC ADWeb/Show.S01E01.2160p.WEB-DL.HEVC.AAC-ADWeb.mkv",
            "Season 1 2160p WEB-DL HEVC AAC ADWeb/Show.S01E02.2160p.WEB-DL.HEVC.AAC-ADWeb.mkv",
        ]
        note = _detect_multi_version_note(files)
        self.assertNotEqual(note, "")
        lines = note.splitlines()
        self.assertEqual(len(lines), 2)

    def test_multi_version_same_season_flat_files(self):
        """Flat TV files (no season folders) should not generate multi-version notes (in-place only)."""
        files = [
            "Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E02.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E01.1080p.BluRay.HEVC.DTS-PTer.mkv",
            "Show.S01E02.1080p.BluRay.HEVC.DTS-PTer.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_cross_season_different_versions_flat_files_returns_empty(self):
        """Cross-season different versions with flat files should return empty."""
        files = [
            "Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E02.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Show.S02E01.1080p.BluRay.HEVC.DTS-PTer.mkv",
            "Show.S02E02.1080p.BluRay.HEVC.DTS-PTer.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_cross_season_7_seasons_different_attributes_returns_empty(self):
        """Cross-season 7 seasons with different attributes should return empty (screenshot scenario)."""
        files = [
            *[f"Show.S0{i}E01.2160p.WEB-DL.HEVC.AAC-StarfallWeb.mkv" for i in range(1, 7)],
            "Show.S07E01.2160p.HDR10.10bit.HEVC.DDP-AiiMUpScale.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_single_version_flat_tv_files_returns_empty(self):
        """Single version TV with flat files should return empty."""
        files = [
            "Show.S01E01.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E02.2160p.WEB-DL.HEVC.AAC-HiveWeb.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_same_quality_different_effect_season_folders(self):
        """Same quality/release group but different HDR/DV effect in season folders should be multi-version."""
        files = [
            "Season 3 2160p DV HMAX WEB-DL HDR H265 DDP5.1.Atmos HiveWeb/Show.S03E01.mkv",
            "Season 3 2160p DV HMAX WEB-DL HDR H265 DDP5.1.Atmos HiveWeb/Show.S03E02.mkv",
            "Season 3 2160p DV MAX HMAX WEB-DL H265 DDP5.1.Atmos HiveWeb/Show.S03E01.mkv",
            "Season 3 2160p DV MAX HMAX WEB-DL H265 DDP5.1.Atmos HiveWeb/Show.S03E02.mkv",
        ]
        note = _detect_multi_version_note(files)
        self.assertNotEqual(note, "")
        lines = note.splitlines()
        self.assertEqual(len(lines), 2)
        # One line should have HDR, the other should not
        hdr_lines = [l for l in lines if "HDR" in l]
        no_hdr_lines = [l for l in lines if "HDR" not in l]
        self.assertEqual(len(hdr_lines), 1)
        self.assertEqual(len(no_hdr_lines), 1)

    def test_same_quality_different_effect_flat_files(self):
        """Flat TV files (no season folders) should not generate multi-version notes even with different effects."""
        files = [
            "Show.S01E01.2160p.WEB-DL.DV.HDR.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E02.2160p.WEB-DL.DV.HDR.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E01.2160p.WEB-DL.DV.HEVC.AAC-HiveWeb.mkv",
            "Show.S01E02.2160p.WEB-DL.DV.HEVC.AAC-HiveWeb.mkv",
        ]
        self.assertEqual(_detect_multi_version_note(files), "")

    def test_database_rich_note_is_snapshotted_as_safe_telegram_html(self):
        draft = asyncio.run(
            build_submission_draft(
                {"templates": {"caption": "{title}\n{resourceBlock}"}},
                {
                    "url": "https://www.123pan.com/s/demo",
                    "cleanUrl": "https://www.123pan.com/s/demo",
                    "title": "Demo",
                    "databaseNote": {
                        "noteContent": '<p>第一行 <strong>加粗</strong><img src="x"></p><script>bad()</script>',
                        "plainText": "第一行 加粗 bad()",
                    },
                },
                {"title": "Demo", "fileNames": []},
                "分享",
            )
        )

        self.assertEqual(draft["databaseNote"]["plainText"], "第一行 加粗 bad()")
        self.assertIn("<b>加粗</b>", draft["databaseNote"]["telegramHtml"])
        self.assertNotIn("<img", draft["databaseNote"]["telegramHtml"])
        self.assertNotIn("<script", draft["databaseNote"]["telegramHtml"])
        self.assertIn("📝 数据库备注", draft["caption"])

    def test_harmony_css_note_keeps_rich_text_in_preview_and_channel_caption(self):
        # This is the exact style shape currently persisted by
        # StyledString.toHtml on HarmonyOS. Telegram does not support CSS, so
        # the submission converter must turn it into its HTML entities.
        harmony_note = (
            '<div ><p><span style="font-size: 16.00px;font-style: normal;font-weight: bold;'
            'color: #172033FF;font-family: sans-serif;stroke-width: 0.00px;'
            'stroke-color: #172033FF;font-superscript: normal;line-height: 25.00px;">'
            '粗体备注</span></p><span style="font-style: italic;text-decoration: underline;">'
            '斜体下划线</span><span style="text-decoration: line-through;">删除线</span>'
            '<span style="font-family: monospace;">代码</span></div>'
        )
        config = {"templates": {"caption": "{title}"}}
        draft = asyncio.run(
            build_submission_draft(
                config,
                {
                    "url": "https://www.123pan.com/s/demo",
                    "cleanUrl": "https://www.123pan.com/s/demo",
                    "title": "Demo",
                    "databaseNote": {"noteContent": harmony_note, "plainText": "粗体备注\n斜体下划线删除线代码"},
                },
                {"title": "Demo", "fileNames": []},
                "分享",
                {"title": "Demo", "posterUrl": "https://image.example/poster.jpg"},
            )
        )

        telegram_html = draft["databaseNote"]["telegramHtml"]
        self.assertIn("<b>粗体备注</b>", telegram_html)
        self.assertIn("<i><u>斜体下划线</u></i>", telegram_html)
        self.assertIn("<s>删除线</s>", telegram_html)
        self.assertIn("<code>代码</code>", telegram_html)
        # Preview and publish both start from this same rich caption; only the
        # route line differs for a channel post.
        self.assertIn("<b>粗体备注</b>", draft["caption"])
        self.assertIn("<b>粗体备注</b>", render_submission_caption(draft, config, include_route=False))
        with patch("app.submission.send_telegram_photo_then_edit_caption", AsyncMock(return_value={"message_id": 9})) as send:
            result = asyncio.run(send_submission_preview_result("token", 1, draft, config))
        self.assertEqual(result["sentCount"], 1)
        self.assertIn("<b>粗体备注</b>", send.await_args.args[3])

    def test_macos_note_does_not_render_document_styles_as_database_note_text(self):
        macos_note = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<style type="text/css">
p.p1 {margin: 0.0px 0.0px 12.0px 0.0px; font: 16.0px 'PingFang SC'; color: #000000; -webkit-text-stroke: #0f121c}
p.p2 {margin: 0.0px 0.0px 0.0px 0.0px; font: 16.0px 'PingFang SC'; color: #000000; -webkit-text-stroke: #0f121c}
span.s1 {font-kerning: none}
</style>
</head>
<body>
<p class="p1"><span class="s1">国台粤日四语</span></p>
<p class="p1"><span class="s1">内封简繁特效字幕</span></p>
<p class="p2"><span class="s1">新增超分版本</span></p>
</body>
</html>"""
        draft = asyncio.run(
            build_submission_draft(
                {"templates": {"caption": "{title}"}},
                {
                    "url": "https://www.123pan.com/s/demo",
                    "cleanUrl": "https://www.123pan.com/s/demo",
                    "title": "Demo",
                    "databaseNote": {
                        "noteContent": macos_note,
                        "plainText": "国台粤日四语\n内封简繁特效字幕\n新增超分版本",
                    },
                },
                {"title": "Demo", "fileNames": []},
                "分享",
            )
        )

        telegram_html = draft["databaseNote"]["telegramHtml"]
        self.assertEqual(telegram_html, "国台粤日四语\n内封简繁特效字幕\n新增超分版本")
        self.assertNotIn("p.p1", draft["caption"])
        self.assertNotIn("font-kerning", draft["caption"])
        self.assertIn("📝 数据库备注：\n国台粤日四语\n内封简繁特效字幕\n新增超分版本", draft["caption"])

    def test_database_note_survives_draft_storage_for_channel_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            draft = asyncio.run(
                build_submission_draft(
                    {"templates": {"caption": "{title}\n{resourceBlock}"}},
                    {
                        "url": "https://www.123pan.com/s/demo",
                        "cleanUrl": "https://www.123pan.com/s/demo",
                        "title": "Demo",
                        "databaseNote": {"noteContent": "<p><strong>频道备注</strong></p>", "plainText": "频道备注"},
                    },
                    {"title": "Demo", "fileNames": []},
                    "分享",
                )
            )

            restored = append_submission_draft(store, draft)

            self.assertEqual(restored["databaseNote"]["plainText"], "频道备注")
            self.assertIn("<b>频道备注</b>", restored["databaseNote"]["telegramHtml"])
            self.assertEqual(restored["databaseNote"]["mode"], "rich")
            self.assertIn("📝 数据库备注", render_submission_caption(restored, {"templates": {"caption": "{title}"}}, include_route=False))

    def test_rich_note_parse_failure_retries_plaintext_and_remembers_token_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            config = {"templates": {"caption": "{title}\n{resourceBlock}"}}
            draft = asyncio.run(
                build_submission_draft(
                    config,
                    {
                        "url": "https://www.123pan.com/s/demo",
                        "cleanUrl": "https://www.123pan.com/s/demo",
                        "title": "Demo",
                        "databaseNote": {"noteContent": "<b>富文本</b>", "plainText": "富文本"},
                    },
                    {"title": "Demo", "fileNames": []},
                    "分享",
                )
            )
            with patch(
                "app.submission.send_telegram_text",
                AsyncMock(side_effect=[ValueError("Can't parse entities"), ValueError("Can't parse entities"), {"message_id": 9}]),
            ) as send:
                result = asyncio.run(send_submission_preview_result("token-a", 1, draft, config, store=store))

            self.assertEqual(result["sentCount"], 1)
            self.assertEqual(draft["databaseNote"]["mode"], "plain")
            self.assertIn("【数据库的备注信息】", draft["caption"])
            self.assertEqual(database_note_mode(store, "token-a"), "plain")
            self.assertEqual(database_note_mode(store, "token-b"), "rich")
            self.assertEqual(send.await_count, 3)


if __name__ == "__main__":
    unittest.main()
