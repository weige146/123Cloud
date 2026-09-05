from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.pan115 import UPLOAD_APP_VERSION, Pan115Client, Pan115Error, local_item_is_dir
from app.pan115_cipher import AES_IV, AES_KEY, aes_cbc_decrypt
from app.pan123 import Pan123OauthBroker, Pan123OpenAPIClient
from app.session_store import SessionStore
from app.transfer_pipeline_123to115 import Pan123to115Error, TransferPipeline123to115
from app.transfer_service import TransferService


def _sha1_of(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def _helper_cookie_config(store: SessionStore) -> None:
    submission = store.read_submission_config()
    submission["pan115Helper"] = {**submission.get("pan115Helper", {}), "enabled": True, "pan115Cookie": "main|UID=1; CID=2; SEID=3"}
    store.write_submission_config(submission)


class Pan115FastUploadTests(unittest.TestCase):
    def _client(self) -> Pan115Client:
        client = Pan115Client("UID=1; CID=2; SEID=3")
        client.request_json = AsyncMock(return_value={"state": True, "data": {"userkey": "uk", "user_id": 123}})  # type: ignore[method-assign]
        return client

    @staticmethod
    def _body_fields(request: dict) -> dict:
        from urllib.parse import parse_qsl
        body = aes_cbc_decrypt(request["data"], AES_KEY, AES_IV)
        return dict(parse_qsl(body.decode("latin-1")))

    def test_upload_init_fast_reuses_directly_on_status_two(self):
        async def run() -> None:
            client = self._client()
            client._post_upload_init = AsyncMock(  # type: ignore[method-assign]
                return_value=(200, {"state": True, "data": {"status": 2, "file_id": 777}})
            )

            result = await client.upload_init_fast("demo.mkv", "a" * 40, 123, "55")

            self.assertTrue(result["reuse"])
            self.assertEqual(result["fileId"], "777")
            request = client._post_upload_init.await_args.args[0]
            fields = self._body_fields(request)
            self.assertEqual(fields["fileid"], "A" * 40)
            self.assertEqual(fields["target"], "U_1_55")
            self.assertEqual(fields["userid"], "123")
            self.assertEqual(fields["userkey"], "uk")
            self.assertEqual(fields["appversion"], UPLOAD_APP_VERSION)
            self.assertIn("k_ec", request["params"])

        asyncio.run(run())

    def test_upload_init_fast_answers_sign_challenge_with_range_hash(self):
        async def run() -> None:
            client = self._client()
            content = b"abcd"
            client._post_upload_init = AsyncMock(side_effect=[  # type: ignore[method-assign]
                (200, {"state": True, "data": {"status": 7, "sign_key": "k1", "sign_check": "0-3"}}),
                (200, {"state": True, "data": {"status": 2, "file_id": 888}}),
            ])

            async def fetch_range(range_text: str) -> bytes:
                self.assertEqual(range_text, "0-3")
                return content

            result = await client.upload_init_fast("demo.mkv", "a" * 40, 123, "55", fetch_range_bytes=fetch_range)

            self.assertTrue(result["reuse"])
            second_fields = self._body_fields(client._post_upload_init.await_args_list[1].args[0])
            self.assertEqual(second_fields["sign_key"], "k1")
            self.assertEqual(second_fields["sign_val"], _sha1_of(content).upper())
            self.assertEqual(second_fields["fileid"], "A" * 40)

        asyncio.run(run())

    def test_upload_init_fast_gives_up_when_status_is_not_reusable(self):
        async def run() -> None:
            client = self._client()
            client._post_upload_init = AsyncMock(  # type: ignore[method-assign]
                return_value=(200, {"state": True, "data": {"status": 1}})
            )

            result = await client.upload_init_fast("demo.mkv", "a" * 40, 123, "0")

            self.assertFalse(result["reuse"])
            client._post_upload_init.assert_awaited_once()

        asyncio.run(run())

    def test_upload_init_fast_retries_once_on_http_401(self):
        async def run() -> None:
            client = self._client()
            client._post_upload_init = AsyncMock(side_effect=[  # type: ignore[method-assign]
                (401, None),
                (200, {"state": True, "data": {"status": 2, "file_id": 999}}),
            ])

            result = await client.upload_init_fast("demo.mkv", "a" * 40, 123, "0")

            self.assertTrue(result["reuse"])
            self.assertEqual(client._post_upload_init.await_count, 2)

        asyncio.run(run())

    def test_upload_init_fast_rejects_non_sha1(self):
        async def run() -> None:
            client = self._client()
            with self.assertRaises(Pan115Error):
                await client.upload_init_fast("demo.mkv", "not-a-sha1", 123, "0")

        asyncio.run(run())


class Pan115LocalListingTests(unittest.TestCase):
    def test_local_item_is_dir_uses_fc_and_sha_fallbacks(self):
        self.assertTrue(local_item_is_dir({"fc": 0}))
        self.assertFalse(local_item_is_dir({"fc": 1}))
        self.assertTrue(local_item_is_dir({"sha": ""}))
        self.assertFalse(local_item_is_dir({"sha": "abc"}))

    def test_mkdir_parses_top_level_cid(self):
        async def run() -> None:
            client = Pan115Client("UID=1; CID=2; SEID=3")
            client.post_form = AsyncMock(return_value={"state": True, "errno": 0, "cid": 262505})  # type: ignore[method-assign]

            cid = await client.mkdir_local_dir("0", "Season 1")

            self.assertEqual(cid, "262505")
            payload = client.post_form.await_args.args[2]
            self.assertEqual(payload["cname"], "Season 1")
            self.assertEqual(payload["pid"], "0")

        asyncio.run(run())

    def test_mkdir_parses_data_file_id_and_category_id(self):
        async def run() -> None:
            client = Pan115Client("UID=1; CID=2; SEID=3")
            client.post_form = AsyncMock(return_value={"state": True, "data": {"file_id": 111}})  # type: ignore[method-assign]
            self.assertEqual(await client.mkdir_local_dir("5", "A"), "111")
            client.post_form = AsyncMock(return_value={"state": True, "data": {"category_id": 222}})  # type: ignore[method-assign]
            self.assertEqual(await client.mkdir_local_dir("5", "B"), "222")

        asyncio.run(run())

    def test_mkdir_falls_back_to_listing_when_name_exists(self):
        async def run() -> None:
            client = Pan115Client("UID=1; CID=2; SEID=3")
            client.post_form = AsyncMock(  # type: ignore[method-assign]
                return_value={"state": False, "error": "该目录名称已存在。", "errno": 20004}
            )
            client.list_local_entries = AsyncMock(return_value=[  # type: ignore[method-assign]
                {"fid": "262505", "name": "Season 1", "size": 0, "isDir": True},
            ])

            cid = await client.mkdir_local_dir("0", "Season 1")

            self.assertEqual(cid, "262505")

        asyncio.run(run())

    def test_list_local_entries_paginates_and_normalizes(self):
        async def run() -> None:
            client = Pan115Client("UID=1; CID=2; SEID=3")
            page1 = [{"fid": str(i), "n": f"f{i}.mkv", "s": 1, "fc": 1, "sha": "F" * 40} for i in range(1000)]
            page2 = [
                {"cid": 7001, "n": "movies", "fc": 0},  # 目录条目：ID 在 cid，没有 fid
                {"fid": "7002", "n": "a.mkv", "s": 10, "fc": 1, "sha": "A" * 40, "pc": "pc1"},
                {"fid": "7003", "n": "b.mkv", "s": 20, "fc": 1, "sha": "B" * 40},
            ]
            pages = [
                {"state": True, "count": 1003, "data": page1},
                {"state": True, "count": 1003, "data": page2},
            ]
            client.list_local_dir = AsyncMock(side_effect=pages)  # type: ignore[method-assign]

            entries = await client.list_local_entries("55")

            self.assertEqual(len(entries), 1003)
            by_name = {entry["name"]: entry for entry in entries}
            self.assertEqual(by_name["movies"]["fid"], "7001")
            self.assertTrue(by_name["movies"]["isDir"])
            self.assertEqual(by_name["a.mkv"]["size"], 10)
            self.assertEqual(by_name["a.mkv"]["sha"], "a" * 40)
            self.assertEqual(client.list_local_dir.await_args_list[1].args, ("55", 1000, 1000))

        asyncio.run(run())


class Pan123OpenAPIDownloadInfoTests(unittest.TestCase):
    def test_download_info_returns_direct_url(self):
        async def run() -> None:
            client = Pan123OpenAPIClient(broker=Pan123OauthBroker())
            client.request = AsyncMock(return_value={"data": {"downloadUrl": "https://dl.example/x.mkv"}})  # type: ignore[method-assign]

            url = await client.download_info(42)

            self.assertEqual(url, "https://dl.example/x.mkv")
            client.request.assert_awaited_once_with("GET", "/api/v1/file/download_info", query={"fileId": "42"})

        asyncio.run(run())


class _FakeStreamResponse:
    def __init__(self, status_code, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aread(self) -> bytes:
        return self._body


class _FakeStream:
    def __init__(self, response: _FakeStreamResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse, captured: list):
        self._response = response
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, **kwargs):
        self._captured.append({"method": method, "url": url, "headers": headers or {}})
        return _FakeStream(self._response)


class FetchRangeBytesTests(unittest.TestCase):
    def _pipeline(self, pan123: AsyncMock) -> TransferPipeline123to115:
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            service = TransferService(store)
            service._create_pan123_client = AsyncMock(return_value=pan123)  # type: ignore[method-assign]
            task = {
                "id": "t-range",
                "kind": "pan123to115",
                "shareUrl": "123://dir?id=10",
                "shareCode": "123dir:10",
                "sourceDirId": "10",
                "targetDirId": "55",
                "status": "running",
                "files": [],
                "totalFiles": 0,
                "doneFiles": 0,
                "logs": [],
            }
            pipeline = TransferPipeline123to115(service, task, {"pan115TargetCid": "55", "concurrency": 1})
            pipeline.pan123 = pan123  # run() 里才赋值，这里手动挂上
            return pipeline

    def test_fetch_range_bytes_sends_range_header_and_returns_exact_bytes(self):
        async def run() -> None:
            captured: list = []
            response = _FakeStreamResponse(206, {"content-length": "4", "content-type": "application/octet-stream"}, b"abcd")
            pan123 = AsyncMock()
            pan123.download_info = AsyncMock(return_value="https://dl.123pan.cn/file.mkv")
            pipeline = self._pipeline(pan123)
            file = {"fileId": 9, "name": "file.mkv", "size": 1000}

            with patch("app.transfer_pipeline_123to115.httpx.AsyncClient", MagicMock(return_value=_FakeAsyncClient(response, captured))):
                content = await pipeline._fetch_range_bytes(file, "100-103")

            self.assertEqual(content, b"abcd")
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["headers"]["range"], "bytes=100-103")

        asyncio.run(run())

    def test_fetch_range_bytes_rejects_full_body_download(self):
        async def run() -> None:
            captured: list = []
            # 服务器忽略 Range：HTTP 200 + 7GB 的 content-length
            response = _FakeStreamResponse(200, {"content-length": str(7 * 1024**3), "content-type": "application/octet-stream"}, b"")
            pan123 = AsyncMock()
            pan123.download_info = AsyncMock(return_value="https://dl.123pan.cn/file.mkv")
            pipeline = self._pipeline(pan123)
            file = {"fileId": 9, "name": "file.mkv", "size": 7 * 1024**3}

            with patch("app.transfer_pipeline_123to115.httpx.AsyncClient", MagicMock(return_value=_FakeAsyncClient(response, captured))):
                with self.assertRaises(Pan123to115Error) as ctx:
                    await pipeline._fetch_range_bytes(file, "0-1023")

            self.assertIn("拒绝整包下载", str(ctx.exception))

        asyncio.run(run())

    def test_reuse_failure_reports_error_type_when_message_empty(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                _helper_cookie_config(store)
                store.save_transfer_hash("a" * 40, 100, "d" * 32, "known.mkv")
                files = [
                    {"id": "1", "fileId": 1, "name": "known.mkv", "size": 100, "etag": "D" * 32, "s3KeyFlag": "k1", "type": 0, "path": [], "status": "pending"},
                ]

                pan123 = AsyncMock()
                pan123.list_files.return_value = files

                pan115 = MagicMock()
                pan115.list_local_entries = AsyncMock(return_value=[])
                pan115.upload_init_fast = AsyncMock(side_effect=RuntimeError())  # 空消息异常

                pipeline = _make_pipeline(store, pan123, pan115)
                with patch("app.transfer_pipeline_123to115.Pan115Client", MagicMock(return_value=pan115)):
                    await pipeline.run()

                error_text = pipeline.task["files"][0]["error"]
                self.assertIn("RuntimeError", error_text)
                self.assertNotIn("出错：）", error_text)
                self.assertNotIn("出错：(", error_text)

        asyncio.run(run())


class TransferHashReverseLookupTests(unittest.TestCase):
    def test_reverse_lookup_finds_sha1_by_etag_and_size(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.save_transfer_hash("a" * 40, 123, "d" * 32, "demo.mkv")
                store.save_transfer_hash("b" * 40, 456, "e" * 32, "other.mkv")

                hit = store.get_transfer_sha1_by_etag("D" * 32, 123)
                miss = store.get_transfer_sha1_by_etag("d" * 32, 999)

                self.assertIsNotNone(hit)
                assert hit is not None
                self.assertEqual(hit["sha1"], "a" * 40)
                self.assertIsNone(miss)

        asyncio.run(run())


def _make_pipeline(store: SessionStore, pan123_mock: AsyncMock, pan115_mock: MagicMock) -> TransferPipeline123to115:
    service = TransferService(store)
    service._create_pan123_client = AsyncMock(return_value=pan123_mock)  # type: ignore[method-assign]
    service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
    task = {
        "id": "t-1",
        "kind": "pan123to115",
        "shareUrl": "123://dir?id=10",
        "shareCode": "123dir:10",
        "sourceDirId": "10",
        "targetDirId": "55",
        "title": "123 目录 10",
        "status": "running",
        "totalFiles": 0,
        "doneFiles": 0,
        "files": [],
        "logs": [],
    }
    config = {
        "pan115TargetCid": "55",
        "concurrency": 2,
    }
    return TransferPipeline123to115(service, task, config)


class TransferPipeline123to115Tests(unittest.TestCase):
    def test_pipeline_fast_uploads_learned_and_fails_unlearned(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                _helper_cookie_config(store)
                # 文件 A 曾从 115 搬来（学习表有 SHA1）；文件 B 没有
                store.save_transfer_hash("a" * 40, 100, "d" * 32, "known.mkv")
                files = [
                    {"id": "1", "fileId": 1, "name": "known.mkv", "size": 100, "etag": "D" * 32, "s3KeyFlag": "k1", "type": 0, "path": [], "status": "pending"},
                    {"id": "2", "fileId": 2, "name": "unknown.mkv", "size": 200, "etag": "e" * 32, "s3KeyFlag": "k2", "type": 0, "path": [], "status": "pending"},
                ]

                pan123 = AsyncMock()
                pan123.list_files.return_value = files

                pan115 = MagicMock()
                pan115.list_local_entries = AsyncMock(return_value=[])
                pan115.mkdir_local_dir = AsyncMock()
                pan115.upload_init_fast = AsyncMock(return_value={"reuse": True, "fileId": "700"})
                pan115.rename_local_file = AsyncMock()

                pipeline = _make_pipeline(store, pan123, pan115)
                with patch("app.transfer_pipeline_123to115.Pan115Client", MagicMock(return_value=pan115)):
                    await pipeline.run()

                task = pipeline.task
                by_name = {f["name"]: f for f in task["files"]}
                self.assertEqual(by_name["known.mkv"]["status"], "success")
                self.assertEqual(by_name["known.mkv"]["method"], "sha1_fast")
                self.assertEqual(by_name["unknown.mkv"]["status"], "failed")
                self.assertIn("学习表", by_name["unknown.mkv"]["error"])
                self.assertEqual(task["status"], "partial")
                pan115.upload_init_fast.assert_awaited_once()

        asyncio.run(run())

    def test_pipeline_fails_when_115_has_no_such_content(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                _helper_cookie_config(store)
                store.save_transfer_hash("a" * 40, 100, "d" * 32, "known.mkv")
                files = [
                    {"id": "1", "fileId": 1, "name": "known.mkv", "size": 100, "etag": "D" * 32, "s3KeyFlag": "k1", "type": 0, "path": [], "status": "pending"},
                ]

                pan123 = AsyncMock()
                pan123.list_files.return_value = files

                pan115 = MagicMock()
                pan115.list_local_entries = AsyncMock(return_value=[])
                pan115.mkdir_local_dir = AsyncMock()
                pan115.upload_init_fast = AsyncMock(return_value={"reuse": False, "fileId": "", "status": 1})

                pipeline = _make_pipeline(store, pan123, pan115)
                with patch("app.transfer_pipeline_123to115.Pan115Client", MagicMock(return_value=pan115)):
                    await pipeline.run()

                task = pipeline.task
                self.assertEqual(task["status"], "failed")
                self.assertIn("没有相同内容", task["files"][0]["error"])

        asyncio.run(run())

    def test_pipeline_uses_learned_sha1_by_name_size(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                _helper_cookie_config(store)
                # 本地学习表里按"文件名+大小"可反查的记录（同文件名、同大小）
                store.save_transfer_hash("b" * 40, 200, "", "imported.mkv")
                files = [
                    {"id": "1", "fileId": 1, "name": "imported.mkv", "size": 200, "etag": "e" * 32, "s3KeyFlag": "k", "type": 0, "path": [], "status": "pending"},
                ]

                pan123 = AsyncMock()
                pan123.list_files.return_value = files

                pan115 = MagicMock()
                pan115.list_local_entries = AsyncMock(return_value=[])
                pan115.upload_init_fast = AsyncMock(return_value={"reuse": True, "fileId": "8001"})

                pipeline = _make_pipeline(store, pan123, pan115)
                with patch("app.transfer_pipeline_123to115.Pan115Client", MagicMock(return_value=pan115)):
                    await pipeline.run()

                task = pipeline.task
                self.assertEqual(task["files"][0]["status"], "success")
                self.assertEqual(task["files"][0]["method"], "sha1_fast")
                # 反查到的 sha1 是导入的数据
                sha1_arg = pan115.upload_init_fast.await_args.args[1]
                self.assertEqual(sha1_arg.lower(), "b" * 40)

        asyncio.run(run())

    def test_share_source_pipeline_stages_via_md5_then_uploads(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                _helper_cookie_config(store)

                pan123 = AsyncMock()
                pan123.ensure_path = AsyncMock(return_value="7777")
                pan123.create_folder = AsyncMock(return_value="8888")
                pan123.md5_reuse = AsyncMock(return_value="9999")
                pan123.trash_files = AsyncMock()
                # 中转后 phase_inspect 列中转目录：先列根（拿到文件夹），再列文件夹（拿到文件）
                pan123.list_files = AsyncMock(side_effect=[
                    [],  # 旧中转目录清理扫描网盘根目录：无遗留
                    [{"fileId": 8888, "name": "sub", "type": 1, "size": 0, "etag": "", "s3KeyFlag": ""}],
                    [{"fileId": 9999, "name": "s.mkv", "type": 0, "size": 100, "etag": "E" * 32, "s3KeyFlag": "k"}],
                ])

                share_client = AsyncMock()
                share_client.list_share_root_items = AsyncMock(
                    return_value=[{"FileId": 11, "FileName": "sub", "Type": 1, "Size": 0}]
                )
                share_client.list_share_items = AsyncMock(
                    return_value=[{"FileId": 12, "FileName": "s.mkv", "Type": 0, "Size": 100, "Etag": "e" * 32}]
                )

                pan115 = MagicMock()
                pan115.list_local_entries = AsyncMock(return_value=[
                    {"fid": "6001", "name": "s.mkv", "size": 100, "isDir": False},
                ])
                pan115.mkdir_local_dir = AsyncMock(return_value="6100")
                pan115.upload_init_fast = AsyncMock()

                service = TransferService(store)
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
                service._create_pan123_client = AsyncMock(return_value=pan123)  # type: ignore[method-assign]
                service._pan123_share_client = share_client  # type: ignore[method-assign]
                task = {
                    "id": "t-share",
                    "kind": "pan123to115",
                    "shareUrl": "https://www.123pan.com/s/abc123?password=ab12",
                    "shareCode": "abc123",
                    "receiveCode": "ab12",
                    "targetDirId": "55",
                    "title": "123 分享 abc123",
                    "status": "running",
                    "totalFiles": 0,
                    "doneFiles": 0,
                    "files": [],
                    "logs": [],
                }
                with patch("app.transfer_pipeline_123to115.Pan115Client", MagicMock(return_value=pan115)):
                    with patch("app.transfer_pipeline_123to115._delay", new=AsyncMock()):
                        pipeline = TransferPipeline123to115(service, task, {"pan115TargetCid": "55", "concurrency": 1})
                        await pipeline.run()

                # 中转：分享内容 md5 秒传到本机网盘中转目录，任务结束移入回收站
                pan123.ensure_path.assert_awaited_once()
                self.assertEqual(pan123.ensure_path.await_args.args[0], "0")
                self.assertEqual(pan123.ensure_path.await_args.args[1][0], "秒传")
                pan123.create_folder.assert_awaited_once_with("7777", "sub")
                self.assertEqual(pan123.md5_reuse.await_args.args, ("8888", "s.mkv", "e" * 32, 100))
                pan123.trash_files.assert_awaited_once_with([7777])
                # 115 里已有同名同大小 → 跳过秒传
                pan115.upload_init_fast.assert_not_awaited()
                self.assertEqual(task["status"], "success")
                self.assertEqual(task["files"][0]["status"], "skipped")

        asyncio.run(run())

    def test_source_root_id_is_recovered_from_share_url(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            service = TransferService(store)
            task = {"id": "t", "shareUrl": "123://dir?id=42", "targetDirId": "55", "files": [], "logs": []}
            pipeline = TransferPipeline123to115(service, task, {"pan115TargetCid": "55"})
            self.assertEqual(pipeline.source_root_id, "42")
            self.assertEqual(pipeline.target_root_cid, "55")


class EnqueuePan123to115Tests(unittest.TestCase):
    def test_enqueue_validates_dir_id_helper_cookie_and_dedupes(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})
                store.write_session({"refreshToken": "rt", "user": "demo"})
                service = TransferService(store)
                service._run_task_safely_wrapper = AsyncMock()  # type: ignore[method-assign]

                with self.assertRaises(RuntimeError):
                    await service.enqueue_pan123to115("abc", "admin")

                # 未配置 115 助手 Cookie 时拒绝
                with self.assertRaises(RuntimeError):
                    await service.enqueue_pan123to115("42", "admin")

                _helper_cookie_config(store)
                task = await service.enqueue_pan123to115("42", "admin")
                self.assertEqual(task["kind"], "pan123to115")
                self.assertEqual(task["shareCode"], "123dir:42")
                self.assertIsNotNone(store.get_transfer_task(task["id"]))

                with self.assertRaises(RuntimeError):
                    await service.enqueue_pan123to115("42", "admin")

        asyncio.run(run())

    def test_enqueue_requires_123_authorization(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True}})  # 没有 123 授权
                service = TransferService(store)
                _helper_cookie_config(store)

                with self.assertRaises(RuntimeError):
                    await service.enqueue_pan123to115("42", "admin")

        asyncio.run(run())

    def test_enqueue_share_to_115_parses_link_and_titles(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"enabled": True, "pan115TargetCid": "88"}})
                store.write_session({"refreshToken": "rt", "user": "demo"})
                service = TransferService(store)
                service._run_task_safely_wrapper = AsyncMock()  # type: ignore[method-assign]
                _helper_cookie_config(store)
                service._pan123_share_client.get_share_info = AsyncMock(  # type: ignore[method-assign]
                    return_value={"shareName": "合集", "shareKey": "abc123"}
                )

                task = await service.enqueue_pan123_share_to_115(
                    "https://www.123pan.com/s/abc123?password=ab12", "ab12", "telegram", chat_id=1, user_id=2
                )

                self.assertEqual(task["kind"], "pan123to115")
                self.assertEqual(task["shareCode"], "abc123")
                self.assertEqual(task["receiveCode"], "ab12")
                self.assertEqual(task["targetDirId"], "88")
                self.assertIn("合集", task["title"])
                self.assertFalse(task["shareUrl"].startswith("123://"))
                self.assertIsNotNone(store.get_transfer_task(task["id"]))

        asyncio.run(run())
