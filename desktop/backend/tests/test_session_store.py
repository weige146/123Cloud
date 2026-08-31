import tempfile
import unittest
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.session_store import SessionStore, normalize_rule_config


class SessionStoreTests(unittest.TestCase):
    def test_default_web_sources_expose_single_max_rule_with_hmax_alias(self):
        rules = normalize_rule_config({})

        max_rules = [rule for rule in rules["webSource"] if rule["id"] in {"web_hmax", "web_max"}]
        self.assertEqual(len(max_rules), 1)
        self.assertEqual(max_rules[0]["id"], "web_max")
        self.assertEqual(max_rules[0]["value"], "MAX")
        self.assertEqual(max_rules[0]["aliases"], ["MAX", "HMAX"])

    def test_legacy_max_web_source_rules_are_merged_without_touching_custom_rules(self):
        rules = normalize_rule_config(
            {
                "webSource": [
                    {"id": "web_hmax", "enabled": True, "value": "HMAX", "aliases": ["HMAX", "HBO Max"], "order": 94},
                    {"id": "custom_service", "enabled": True, "value": "Custom", "aliases": ["CSTM"], "order": 50},
                    {"id": "web_max", "enabled": True, "value": "MAX", "aliases": ["MAX"], "order": 93},
                ]
            }
        )

        max_rules = [rule for rule in rules["webSource"] if rule["id"] == "web_max"]
        self.assertEqual(len(max_rules), 1)
        self.assertTrue(max_rules[0]["enabled"])
        self.assertEqual(max_rules[0]["value"], "MAX")
        self.assertEqual(max_rules[0]["aliases"][:2], ["MAX", "HMAX"])
        self.assertIn("HBO Max", max_rules[0]["aliases"])
        self.assertTrue(any(rule["id"] == "custom_service" and rule["value"] == "Custom" for rule in rules["webSource"]))

    def test_next_queued_transfer_task_can_exclude_pan123_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.save_transfer_task({
                "id": "copy-1",
                "kind": "pan123_share_copy",
                "status": "queued",
                "createdAt": "2026-07-30T01:00:00+00:00",
            })
            store.save_transfer_task({
                "id": "pan115-1",
                "kind": "pan115_share",
                "status": "queued",
                "createdAt": "2026-07-30T01:00:01+00:00",
            })

            task = store.next_queued_transfer_task(exclude_kind="pan123_share_copy")

            self.assertIsNotNone(task)
            self.assertEqual(task["id"], "pan115-1")

    def test_transfer_task_persists_pan123_copy_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.save_transfer_task({
                "id": "copy-1",
                "kind": "pan123_share_copy",
                "status": "running",
                "remoteTaskId": 123,
                "targetDirId": "456",
                "shareOwnerUserId": 789,
            })

            task = store.get_transfer_task("copy-1")

            assert task is not None
            self.assertEqual(task["kind"], "pan123_share_copy")
            self.assertEqual(task["remoteTaskId"], 123)
            self.assertEqual(task["targetDirId"], "456")
            self.assertEqual(task["shareOwnerUserId"], 789)

    def test_submission_config_drops_removed_pan115_share_helper_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            saved = store.write_submission_config({
                "pan115Helper": {
                    "enabled": True,
                    "pan115Cookie": "cookie",
                    "botMode": "off",
                    "linkTargetDirId": "123",
                    "cleanTargetDirIds": "456",
                    "offlineRequestIntervalMs": 1000,
                    "offlineTargetDirId": "789",
                }
            })

            helper = saved["pan115Helper"]
            self.assertEqual(helper["offlineTargetDirId"], "789")
            self.assertNotIn("botMode", helper)
            self.assertNotIn("linkTargetDirId", helper)
            self.assertNotIn("cleanTargetDirIds", helper)
            self.assertNotIn("offlineRequestIntervalMs", helper)

    def test_user_channel_grants_are_normalized_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_user_channel_config(
                101,
                {
                    "channels": [
                        {"id": "owner-main", "title": "Owner main", "chatId": "-1001", "allowedUserIds": [202, 202]},
                        {"id": "owner-private", "title": "Private", "chatId": "-1002", "allowedUserIds": []},
                    ],
                    "routing": {"fallbackChannelId": "owner-main"},
                },
            )
            store.write_user_channel_config(303, {"channels": [{"id": "other", "title": "Other", "chatId": "-1003"}], "routing": {}})

            owner = store.read_user_channel_config(101)
            self.assertEqual(owner["channels"][0]["allowedUserIds"], [202])
            self.assertEqual([item["channel"]["id"] for item in store.granted_submission_channels(202)], ["owner-main"])
            self.assertEqual(store.granted_submission_channels(303), [])
            self.assertTrue(store.channel_user_allowed(101, "owner-main", 202))
            self.assertFalse(store.channel_user_allowed(101, "owner-private", 202))

            self.assertTrue(store.delete_user_channel_config(101))
            self.assertEqual(store.granted_submission_channels(202), [])

    def test_legacy_global_channels_migrate_without_deleting_publication_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.record_submission_publication({"id": "legacy-post", "channelChatId": "-1009", "channelId": "legacy", "messageId": 9, "identityKey": "legacy"})
            store.write_submission_config(
                {
                    "allowedUserIds": [101],
                    "channels": [{"id": "legacy", "title": "Legacy", "chatId": "-1009", "enabled": True, "isDefault": True}],
                    "routing": {"fallbackChannelId": "legacy"},
                }
            )

            migrated = store.read_user_channel_config(101)
            self.assertEqual(migrated["channels"][0]["chatId"], "-1009")
            self.assertEqual(migrated["routing"]["fallbackChannelId"], "legacy")
            self.assertEqual(store.read_submission_config()["channels"], [])
            records = store.find_submission_publications("-1009", "legacy", route_owner_user_id=101)
            self.assertEqual([record["id"] for record in records], ["legacy-post"])

    def test_publication_history_is_scoped_to_route_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            for owner in (101, 202):
                store.write_user_channel_config(owner, {"channels": [{"id": "main", "title": str(owner), "chatId": "-100-shared"}], "routing": {}})
                store.record_submission_publication(
                    {
                        "id": f"post-{owner}",
                        "channelChatId": "-100-shared",
                        "channelId": "main",
                        "routeOwnerUserId": owner,
                        "messageId": owner,
                        "identityKey": "tmdb:movie:1:resource",
                    }
                )

            self.assertEqual(store.channel_owner_count("-100-shared"), 2)
            self.assertEqual(
                [row["id"] for row in store.find_submission_publications("-100-shared", "tmdb:movie:1:resource", route_owner_user_id=101)],
                ["post-101"],
            )

    def test_session_store_matches_same_user_and_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            session = store.build_session("user@example.com", "secret", "token", "uuid")
            store.write_session(session)
            loaded = store.read_session()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(store.credentials_match(loaded, "user@example.com", "secret"))
            self.assertFalse(store.credentials_match(loaded, "user@example.com", "wrong"))
            self.assertFalse(store.credentials_match(loaded, "other@example.com", "secret"))

    def test_admin_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({"transfer": {"enabled": True, "pan123ClientId": "cid"}})
            saved = store.write_config({"transfer": {"enabled": False, "pan123ClientId": "cid", "pan123ClientSecret": "secret"}})
            self.assertEqual(saved["transfer"]["pan123ClientId"], "cid")
            self.assertEqual(saved["transfer"]["pan123ClientSecret"], "secret")
            self.assertEqual(store.read_config()["transfer"]["pan123ClientId"], "cid")

    def test_legacy_openapi_credentials_migrated_into_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root)
            store.write_value("admin_config", {
                "gatewayName": "旧网关",
                "pan123ClientMode": "openapi",
                "pan123OpenApiClientId": "legacy-cid",
                "pan123OpenApiClientSecret": "legacy-secret",
                "transfer": {"enabled": True},
            })

            migrated = SessionStore(root)

            config = migrated.read_value("admin_config")
            assert isinstance(config, dict)
            self.assertNotIn("gatewayName", config)
            self.assertNotIn("pan123ClientMode", config)
            self.assertNotIn("pan123OpenApiClientId", config)
            self.assertNotIn("pan123OpenApiClientSecret", config)
            self.assertEqual(config.get("transfer", {}).get("pan123ClientId"), "legacy-cid")
            self.assertEqual(config.get("transfer", {}).get("pan123ClientSecret"), "legacy-secret")
            self.assertTrue(config.get("transfer", {}).get("enabled"))

    def test_legacy_openapi_migration_keeps_existing_transfer_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root)
            store.write_value("admin_config", {
                "pan123OpenApiClientId": "legacy-cid",
                "transfer": {"pan123ClientId": "new-cid", "pan123ClientSecret": "new-secret"},
            })

            migrated = SessionStore(root)

            config = migrated.read_value("admin_config")
            assert isinstance(config, dict)
            self.assertEqual(config.get("transfer", {}).get("pan123ClientId"), "new-cid")
            self.assertEqual(config.get("transfer", {}).get("pan123ClientSecret"), "new-secret")

    def test_legacy_json_is_migrated_and_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"transfer":{"enabled":true}}\n', encoding="utf-8")
            (root / "pan123-session.json").write_text('{"user":"u","token":"t","loginUuid":"d"}\n', encoding="utf-8")

            store = SessionStore(root)

            self.assertEqual(store.read_config()["transfer"]["enabled"], True)
            self.assertEqual(store.read_session()["token"], "t")
            self.assertFalse((root / "config.json").exists())
            self.assertFalse((root / "pan123-session.json").exists())
            self.assertTrue((root / "cloud123.db").exists())

if __name__ == "__main__":
    unittest.main()
