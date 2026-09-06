from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.panlink import browse_pan123_dir, build_pan123_link, build_index, pan123_source_files, submit_pan123_offline


class _FakeRequestURL:
    def __init__(self, netloc: str = "hub.example.com", scheme: str = "https"):
        self.netloc = netloc
        self.scheme = scheme


class _FakeRequest:
    def __init__(self):
        self.url = _FakeRequestURL()


def _helper_config() -> dict:
    return {
        "enabled": True,
        "pan115Cookie": "账号1|cookie=abc",
        "offlineTargetDirId": "42",
        "publicBaseUrl": "https://dl.example.com",
        "pan123SourceDirId": "100",
    }


def _mock_pan_client(files_by_parent=None):
    files_by_parent = files_by_parent or {
        "100": [
            {"fileId": 1, "name": "a.mkv", "type": 0, "size": 10},
            {"fileId": 2, "name": "剧集", "type": 1, "size": 0},
        ],
        "2": [{"fileId": 3, "name": "b.mkv", "type": 0, "size": 20}],
    }
    client = MagicMock()
    client.list_files = AsyncMock(side_effect=lambda parent: files_by_parent.get(str(parent), []))
    return client


class PanLinkTests(unittest.TestCase):
    def test_build_pan123_link(self):
        self.assertEqual(build_pan123_link("https://dl.example.com", 7), "https://dl.example.com/dpan/123/7")

    def test_list_sources_flattens_recursively(self):
        async def run() -> None:
            client = _mock_pan_client()
            files = await pan123_source_files(client, 100)
            rels = {item["rel"]: item["fileId"] for item in files}
            self.assertEqual(set(rels), {"a.mkv", "剧集/b.mkv"})
            self.assertEqual(rels["剧集/b.mkv"], 3)

        asyncio.run(run())

    def test_build_index_by_file_id(self):
        index = build_index([{"fileId": 5, "name": "x"}])
        self.assertIn(5, index)
        self.assertEqual(index[5]["name"], "x")

    def test_submit_pan123_offline(self):
        async def run() -> None:
            client = _mock_pan_client()
            helper_client = MagicMock()
            helper_client.add_offline_urls = AsyncMock(return_value="已提交")
            with patch("app.panlink.create_helper_client", return_value=helper_client):
                result = await submit_pan123_offline(client, _helper_config(), ["3"], _FakeRequest())
            helper_client.add_offline_urls.assert_awaited_once_with(["https://dl.example.com/dpan/123/3"], "42")
            self.assertTrue(result["ok"])
            self.assertEqual(result["success"], 1)
            self.assertEqual(result["results"][0]["label"], "剧集/b.mkv")

        asyncio.run(run())

    def test_submit_pan123_offline_rejects_unknown_file(self):
        async def run() -> None:
            client = _mock_pan_client()
            with patch("app.panlink.create_helper_client", return_value=MagicMock()):
                result = await submit_pan123_offline(client, _helper_config(), ["999"], _FakeRequest())
            self.assertFalse(result["ok"])
            self.assertEqual(result["failed"], 1)
            self.assertIn("不在源目录", result["results"][0]["message"])

        asyncio.run(run())

    def test_browse_root_and_descend(self):
        async def run() -> None:
            client = _mock_pan_client()
            root = await browse_pan123_dir(client, 0)
            self.assertTrue(root["ok"])
            self.assertEqual(root["parentId"], 0)
            self.assertEqual([item["name"] for item in root["directories"]], [])
            self.assertEqual([item["name"] for item in root["files"]], [])
            inside = await browse_pan123_dir(client, 2)
            self.assertEqual([item["name"] for item in inside["files"]], ["b.mkv"])
            self.assertEqual(inside["directories"], [])

        asyncio.run(run())

    def test_browse_separates_directories(self):
        async def run() -> None:
            client = _mock_pan_client(
                {"0": [{"fileId": 1, "name": "a.mkv", "type": 0, "size": 10}, {"fileId": 2, "name": "剧集", "type": 1, "size": 0}]}
            )
            data = await browse_pan123_dir(client, 0)
            self.assertEqual([item["name"] for item in data["directories"]], ["剧集"])
            self.assertEqual([item["name"] for item in data["files"]], ["a.mkv"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()