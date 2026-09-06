from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.directlink import (
    build_direct_link,
    get_dl_root,
    list_directlink_files,
    resolve_dl_path,
)
from app.pan115 import Pan115Error


class _FakeRequestURL:
    def __init__(self, netloc: str = "hub.example.com", scheme: str = "https"):
        self.netloc = netloc
        self.scheme = scheme


class _FakeRequest:
    def __init__(self, netloc: str = "hub.example.com", scheme: str = "https"):
        self.url = _FakeRequestURL(netloc, scheme)


def _helper_config(root: str) -> dict:
    return {
        "enabled": True,
        "pan115Cookie": "账号1|cookie=abc",
        "offlineTargetDirId": "42",
        "directLinkRoot": root,
        "publicBaseUrl": "https://dl.example.com",
    }


class DirectLinkTests(unittest.TestCase):
    def test_get_dl_root_creates_configured_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "mylinks"
            self.assertFalse(root.exists())
            resolved = get_dl_root({"directLinkRoot": str(root)})
            self.assertTrue(resolved.is_dir())
            self.assertEqual(resolved, root)

    def test_resolve_dl_path_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            config = _helper_config(directory)
            with self.assertRaises(Pan115Error):
                resolve_dl_path(config, "../../etc/passwd")

    def test_list_directlink_files_flattens_rel_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sub").mkdir()
            (root / "a.mkv").write_bytes(b"aaa")
            (root / "sub" / "b.mkv").write_bytes(b"bb")
            config = _helper_config(directory)
            files = list_directlink_files(config)
            rels = {item["rel"] for item in files}
            self.assertIn("a.mkv", rels)
            self.assertIn("sub/b.mkv", rels)
            sizes = {item["rel"]: item["size"] for item in files}
            self.assertEqual(sizes["a.mkv"], 3)
            self.assertEqual(sizes["sub/b.mkv"], 2)

    def test_build_direct_link_normalizes_slashes(self):
        self.assertEqual(build_direct_link("https://dl.example.com/", "a/b.mkv"), "https://dl.example.com/dlink/a/b.mkv")
        self.assertEqual(build_direct_link("https://dl.example.com", "/x/y.mkv"), "https://dl.example.com/dlink/x/y.mkv")

    def test_submit_directlink_offline_builds_urls_and_submits(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "m.mkv").write_bytes(b"data")
                config = _helper_config(directory)
                client = MagicMock()
                client.add_offline_urls = AsyncMock(return_value="已提交 1 个离线任务")
                with patch("app.directlink.create_helper_client", return_value=client):
                    from app.directlink import submit_directlink_offline

                    result = await submit_directlink_offline(config, ["m.mkv"], _FakeRequest())
                client.add_offline_urls.assert_awaited_once_with(["https://dl.example.com/dlink/m.mkv"], "42")
                self.assertTrue(result["ok"])
                self.assertEqual(result["success"], 1)
                self.assertEqual(result["results"][0]["label"], "m.mkv")
                self.assertEqual(result["results"][0]["ok"], True)

        asyncio.run(run())

    def test_submit_directlink_offline_missing_file_reports_failure(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                config = _helper_config(directory)
                with patch("app.directlink.create_helper_client", return_value=MagicMock()):
                    from app.directlink import submit_directlink_offline

                    result = await submit_directlink_offline(config, ["nope.mkv"], _FakeRequest())
                self.assertFalse(result["ok"])
                self.assertEqual(result["failed"], 1)
                self.assertIn("不存在", result["results"][0]["message"])

        asyncio.run(run())

    def test_submit_arbitrary_urls_offline_filters_non_http(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                config = _helper_config(directory)
                client = MagicMock()
                client.add_offline_urls = AsyncMock(return_value="已提交")
                with patch("app.directlink.create_helper_client", return_value=client):
                    from app.directlink import submit_arbitrary_urls_offline

                    result = await submit_arbitrary_urls_offline(config, ["https://a.com/f.mkv", "not-a-url"])
                self.assertTrue(result["success"] == 1 and result["failed"] == 1)
                client.add_offline_urls.assert_awaited_once_with(["https://a.com/f.mkv"], "42")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()