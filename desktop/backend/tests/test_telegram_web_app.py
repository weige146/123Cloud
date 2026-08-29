from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import main
from app.session_store import SessionStore


def signed_init_data(token: str, user_id: int, auth_date: int | None = None) -> str:
    pairs = [
        ("auth_date", str(auth_date or int(time.time()))),
        ("query_id", "test-query"),
        ("user", json.dumps({"id": user_id, "first_name": "Tester"}, separators=(",", ":"))),
    ]
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode([*pairs, ("hash", signature)])


class TelegramWebAppTests(unittest.TestCase):
    def test_signed_web_app_identity_can_only_write_its_own_channel_config(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            token = "bot-token"
            store.write_submission_config({"botToken": token, "channelOwnerUserIds": [101]})
            init_data = signed_init_data(token, 101)
            payload = main.OwnUserChannelConfigRequest(
                channels=[{"id": "mine", "title": "Mine", "chatId": "-1001", "allowedUserIds": [202]}],
                routing={"fallbackChannelId": "mine"},
            )
            with patch.object(main, "store", store):
                saved = asyncio.run(main.write_my_channel_config(payload, init_data))

            self.assertEqual(saved["config"]["ownerUserId"], 101)
            self.assertEqual(store.read_user_channel_config(101)["channels"][0]["allowedUserIds"], [202])
            self.assertEqual(store.read_user_channel_config(202)["channels"], [{"id": "private", "title": "私有", "chatId": "", "enabled": True, "isDefault": True, "role": "private", "allowedUserIds": []}])

    def test_bot_admin_can_authorize_a_channel_owner_from_the_channel_card(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            token = "bot-token"
            store.write_submission_config({"botToken": token, "telegramAdminUserIds": [101], "channelOwnerUserIds": [101]})
            payload = main.OwnUserChannelConfigRequest(channels=[{"id": "mine", "title": "Mine", "chatId": "-1001"}], routing={}, channelOwnerUserIds=[101, 202])
            with patch.object(main, "store", store):
                saved = asyncio.run(main.write_my_channel_config(payload, signed_init_data(token, 101)))

            self.assertTrue(saved["config"]["canManageChannelOwners"])
            self.assertEqual(saved["config"]["channelOwnerUserIds"], [101, 202])
            self.assertIn(202, store.read_submission_config()["channelOwnerUserIds"])

    def test_bot_admin_can_open_the_card_to_authorize_the_first_channel_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            token = "bot-token"
            store.write_submission_config({"botToken": token, "telegramAdminUserIds": [101], "channelOwnerUserIds": []})
            payload = main.OwnUserChannelConfigRequest(channels=[], routing={}, channelOwnerUserIds=[202])
            with patch.object(main, "store", store):
                saved = asyncio.run(main.write_my_channel_config(payload, signed_init_data(token, 101)))

            self.assertTrue(saved["config"]["canManageChannelOwners"])
            self.assertEqual(store.read_submission_config()["channelOwnerUserIds"], [202])

    def test_tampered_or_expired_web_app_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            token = "bot-token"
            store.write_submission_config({"botToken": token, "channelOwnerUserIds": [101]})
            with patch.object(main, "store", store):
                with self.assertRaises(HTTPException) as tampered:
                    main.telegram_web_app_user_id(signed_init_data(token, 101).replace("test-query", "other-query"))
                self.assertEqual(tampered.exception.status_code, 401)
                with self.assertRaises(HTTPException) as expired:
                    main.telegram_web_app_user_id(signed_init_data(token, 101, int(time.time()) - 86_401))
                self.assertEqual(expired.exception.status_code, 401)
