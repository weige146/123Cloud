"""桌面端「投稿路由」管理接口测试：/api/submission/channel-owners。

- 写入读回：PUT 后 GET 返回相同频道与路由
- 非候选账号：不在 管理员 ∪ 频道主 ∪ 已有配置 中 → 403
- 重复频道：同一账号内频道 ID 重复 → 400
- 默认账号：defaultOwnerUserId 取 channelOwnerUserIds[0]，回退第一个管理员
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import main
from app.session_store import SessionStore


class ChannelOwnerAdminApiTests(unittest.TestCase):
    def _store(self) -> SessionStore:
        return SessionStore(Path(self._directory.name))

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.store = self._store()
        self.store.write_submission_config({
            "botToken": "bot-token",
            "telegramAdminUserIds": [1001],
            "channelOwnerUserIds": [2001],
        })

    def test_write_then_read_back_roundtrip(self):
        payload = main.OwnUserChannelConfigRequest(
            channels=[{"id": "movies", "title": "电影频道", "chatId": "-100100", "enabled": True}],
            routing={"fallbackChannelId": "movies"},
        )
        with patch.object(main, "store", self.store):
            saved = asyncio.run(main.write_channel_owner_config(2001, payload))
            loaded = asyncio.run(main.read_channel_owner_config(2001))

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["config"]["ownerUserId"], 2001)
        self.assertEqual(loaded["config"]["channels"][0]["id"], "movies")
        self.assertEqual(loaded["config"]["routing"]["fallbackChannelId"], "movies")

    def test_non_candidate_user_id_is_forbidden(self):
        with patch.object(main, "store", self.store):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.read_channel_owner_config(9999))
        self.assertEqual(caught.exception.status_code, 403)

        # 已有配置记录的账号即使不在候选里也可以读到（历史数据兜底）
        self.store.write_user_channel_config(3001, {"channels": [], "routing": {}})
        with patch.object(main, "store", self.store):
            loaded = asyncio.run(main.read_channel_owner_config(3001))
        self.assertEqual(loaded["config"]["ownerUserId"], 3001)

    def test_duplicate_channel_id_returns_400(self):
        payload = main.OwnUserChannelConfigRequest(
            channels=[
                {"id": "movies", "title": "电影", "chatId": "-100100"},
                {"id": "movies", "title": "重复", "chatId": "-100200"},
            ],
            routing={},
        )
        with patch.object(main, "store", self.store):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.write_channel_owner_config(2001, payload))
        self.assertEqual(caught.exception.status_code, 400)

    def test_delete_channel_owner_config(self):
        self.store.write_user_channel_config(2001, {"channels": [], "routing": {}})
        with patch.object(main, "store", self.store):
            result = asyncio.run(main.delete_channel_owner_config(2001))
            self.assertTrue(result["deleted"])
            # 账号仍是候选频道主，读回为默认空配置；非候选账号才 403
            loaded = asyncio.run(main.read_channel_owner_config(2001))
        self.assertEqual(loaded["config"]["channels"], self.store.read_user_channel_config(2001)["channels"])

    def test_default_owner_prefers_channel_owner_then_admin(self):
        with patch.object(main, "store", self.store):
            listing = asyncio.run(main.list_channel_owners())
        self.assertEqual(listing["owners"], [1001, 2001])
        self.assertEqual(listing["defaultOwnerUserId"], 2001)

        self.store.write_submission_config({"channelOwnerUserIds": []})
        with patch.object(main, "store", self.store):
            listing = asyncio.run(main.list_channel_owners())
        self.assertEqual(listing["defaultOwnerUserId"], 1001)


if __name__ == "__main__":
    unittest.main()
