from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.session_store import SessionStore
from app.pan115_transfer import PAN123_OFFLINE_USER_AGENT, Pan115TransferClient
from app.pan123 import Pan123Error
from app.transfer_pipeline import TransferPipeline
from app.transfer_service import TransferService


class TransferServiceTests(unittest.TestCase):
    def test_pan123_share_copy_scheduler_runs_only_one_copy_task(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"concurrency": 5}})
                for task_id, created_at in (("copy-1", "2026-07-30T01:00:00+00:00"), ("copy-2", "2026-07-30T01:00:01+00:00")):
                    store.save_transfer_task({
                        "id": task_id,
                        "kind": "pan123_share_copy",
                        "status": "queued",
                        "shareCode": task_id,
                        "createdAt": created_at,
                    })
                service = TransferService(store)
                service._run_task_safely_wrapper = AsyncMock()  # type: ignore[method-assign]
                service._notify_task_progress = AsyncMock()  # type: ignore[method-assign]

                await service._process_loop()
                await asyncio.sleep(0)

                self.assertEqual(store.get_transfer_task("copy-1")["status"], "running")
                self.assertEqual(store.get_transfer_task("copy-2")["status"], "queued")
                self.assertEqual(service._active_pan123_share_copy_task_id, "copy-1")
                service._run_task_safely_wrapper.assert_awaited_once()  # type: ignore[attr-defined]
                await service.close()

        asyncio.run(run())

    def test_pan123_share_copy_submit_retries_rate_limit(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                service._pan123_web_client.create_share_copy_task = AsyncMock(  # type: ignore[method-assign]
                    side_effect=[Pan123Error("请勿频繁操作，请稍后再试", code=429), 12345]
                )
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
                task = {
                    "id": "copy-1",
                    "shareUrl": "https://www.123pan.com/s/demo",
                    "receiveCode": "",
                    "targetDirId": "456",
                    "logs": [],
                }
                with patch("app.transfer_service._delay", new=AsyncMock()) as delay:
                    task_id = await service._submit_pan123_share_copy_with_retry(
                        task,
                        {"token": "token", "loginUuid": "uuid"},
                        [{"FileId": 1, "FileName": "Demo", "Type": 1}],
                        {
                            "pan123ShareCopyMinIntervalMs": 1,
                            "pan123ShareCopyMaxAttempts": 2,
                            "pan123ShareCopyRetryBaseMs": 1,
                        },
                    )

                self.assertEqual(task_id, 12345)
                self.assertEqual(service._pan123_web_client.create_share_copy_task.await_count, 2)  # type: ignore[attr-defined]
                self.assertGreaterEqual(delay.await_count, 1)
                self.assertTrue(any("操作频繁" in log["message"] for log in task["logs"]))
                await service.close()

        asyncio.run(run())

    def test_pan123_share_copy_status_retries_rate_limit_result(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_session({"token": "token", "loginUuid": "uuid"})
                service = TransferService(store)
                service._pan123_web_client.get_share_copy_task = AsyncMock(  # type: ignore[method-assign]
                    side_effect=[
                        {"status": 1, "errorCode": 429, "reason": "请勿频繁操作，请稍后再试", "progress": ""},
                        {"status": 2, "errorCode": 0, "reason": "", "progress": ""},
                    ]
                )
                service._notify_task = AsyncMock()  # type: ignore[method-assign]
                task = {
                    "id": "copy-1",
                    "kind": "pan123_share_copy",
                    "status": "running",
                    "shareUrl": "https://www.123pan.com/s/demo",
                    "remoteTaskId": 99,
                    "targetDirId": "123",
                    "files": [{"id": "1", "name": "Folder", "status": "pending"}],
                    "totalFiles": 1,
                    "logs": [],
                }
                with patch("app.transfer_service._delay", new=AsyncMock()) as delay:
                    await service._run_pan123_share_copy(
                        task,
                        {"offlineMaxPolls": 3, "offlinePollMs": 2000, "pan123ShareCopyRetryBaseMs": 1000},
                    )

                self.assertEqual(task["status"], "success")
                self.assertEqual(service._pan123_web_client.get_share_copy_task.await_count, 2)  # type: ignore[attr-defined]
                delay.assert_awaited_once()
                await service.close()

        asyncio.run(run())

    def test_pan123_share_copy_resumes_remote_task_without_resubmitting(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_session({"token": "token", "loginUuid": "uuid"})
                service = TransferService(store)
                service._pan123_web_client.list_share_root_items = AsyncMock()  # type: ignore[method-assign]
                service._pan123_web_client.create_share_copy_task = AsyncMock()  # type: ignore[method-assign]
                service._pan123_web_client.get_share_copy_task = AsyncMock(  # type: ignore[method-assign]
                    return_value={"status": 2, "errorCode": 0, "reason": "", "progress": ""}
                )
                service._notify_task = AsyncMock()  # type: ignore[method-assign]
                task = {
                    "id": "copy-1",
                    "kind": "pan123_share_copy",
                    "status": "running",
                    "shareUrl": "https://www.123pan.com/s/demo",
                    "remoteTaskId": 99,
                    "targetDirId": "123",
                    "files": [{"id": "1", "name": "Folder", "status": "pending"}],
                    "totalFiles": 1,
                    "logs": [],
                }

                await service._run_pan123_share_copy(task, {"offlineMaxPolls": 2, "offlinePollMs": 2000})

                service._pan123_web_client.list_share_root_items.assert_not_awaited()  # type: ignore[attr-defined]
                service._pan123_web_client.create_share_copy_task.assert_not_awaited()  # type: ignore[attr-defined]
                self.assertEqual(task["status"], "success")
                self.assertEqual(task["doneFiles"], 1)
                await service.close()

        asyncio.run(run())

    def test_enqueue_pan123_share_copy_uses_existing_target_dir_without_transfer_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({"transfer": {"enabled": False, "targetDirId": "456"}})
            service = TransferService(store)
            service.kick = lambda: None  # type: ignore[method-assign]

            task = asyncio.run(service.enqueue_pan123_share_copy(
                "https://www.123pan.com/s/demo",
                "ABCD",
                {"shareKey": "demo", "shareName": "Demo", "userId": 789},
                "test",
            ))

            self.assertEqual(task["kind"], "pan123_share_copy")
            self.assertEqual(task["targetDirId"], "456")
            self.assertEqual(task["shareOwnerUserId"], 789)

    def test_enqueue_share_requires_receive_code_before_creating_task(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({"transfer": {"enabled": True}})
            service = TransferService(store)
            service.kick = lambda: None  # type: ignore[method-assign]

            with self.assertRaisesRegex(RuntimeError, "缺少提取码"):
                asyncio.run(service.enqueue_from_text("https://115.com/s/demo-share", "test"))

            self.assertEqual(store.list_transfer_tasks(10), [])

    def test_enqueue_local_path_uses_existing_task_queue_and_dedupes_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({
                "transfer": {
                    "enabled": True,
                    "pan115Cookie": "UID=1; CID=x; SEID=y",
                    "targetDirId": "0",
                    "pauseEnabled": False,
                }
            })
            service = TransferService(store)
            service.kick = lambda: None  # type: ignore[method-assign]

            task = asyncio.run(service.enqueue_local_path("/云下载/待搬运", "test"))

            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["sourceText"], "/云下载/待搬运")
            self.assertEqual(task["shareCode"], "local:/云下载/待搬运")
            self.assertTrue(task["shareUrl"].startswith("115://local?"))
            self.assertEqual(task["files"], [])

            with self.assertRaisesRegex(RuntimeError, "已有未完成任务"):
                asyncio.run(service.enqueue_local_path("/云下载/待搬运", "test"))

    def test_enqueue_local_path_accepts_cid_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({
                "transfer": {
                    "enabled": True,
                    "pan115Cookie": "UID=1; CID=x; SEID=y",
                    "targetDirId": "0",
                    "pauseEnabled": False,
                }
            })
            service = TransferService(store)
            service.kick = lambda: None  # type: ignore[method-assign]

            task = asyncio.run(service.enqueue_local_path("123456", "test"))

            self.assertEqual(task["sourceText"], "cid:123456")
            self.assertEqual(task["shareCode"], "local:cid:123456")
            self.assertEqual(task["shareUrl"], "115://local?cid=123456")
            self.assertEqual(task["title"], "115 本地盘 CID 123456")

    def test_local_source_account_comes_from_pan115_helper_config(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({
                "transfer": {
                    "enabled": True,
                    "pan115Cookie": "搬运池|UID=pool; CID=x; SEID=y",
                    "targetDirId": "0",
                }
            })
            store.write_submission_config({
                "pan115Helper": {
                    "pan115Cookie": "助手源账号|UID=helper; CID=a; SEID=b",
                }
            })
            service = TransferService(store)

            account = service._local_source_pan115_account()

            self.assertEqual(account["name"], "115 助手：助手源账号")
            self.assertEqual(account["cookie"], "UID=helper; CID=a; SEID=b")

    def test_local_source_account_requires_pan115_helper_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.write_config({
                "transfer": {
                    "enabled": True,
                    "pan115Cookie": "搬运池|UID=pool; CID=x; SEID=y",
                    "targetDirId": "0",
                }
            })
            service = TransferService(store)

            with self.assertRaisesRegex(RuntimeError, "115 助手 Cookie"):
                service._local_source_pan115_account()

    def test_pan115_local_dir_id_accepts_cid_without_path_lookup(self):
        async def run() -> None:
            client = Pan115TransferClient("UID=1; CID=x; SEID=y")
            try:
                self.assertEqual(await client.get_local_dir_id("cid:98765"), "98765")
                self.assertEqual(await client.get_local_dir_id("98765"), "98765")
            finally:
                await client.close()

        asyncio.run(run())

    def test_local_download_url_is_signed_for_pan123_offline_user_agent(self):
        async def run() -> None:
            client = Pan115TransferClient("UID=1; CID=x; SEID=y")
            client.get_user_id = AsyncMock(return_value="1")  # type: ignore[method-assign]
            client._client.post = AsyncMock(return_value=httpx.Response(  # type: ignore[method-assign]
                200,
                json={"state": True, "data": {"url": {"url": "https://cdn.invalid/file.mkv"}}},
            ))
            try:
                url = await client.get_local_download_url("pickcode")
            finally:
                await client.close()

            self.assertEqual(url, "https://cdn.invalid/file.mkv")
            headers = client._client.post.await_args.kwargs["headers"]  # type: ignore[attr-defined]
            self.assertEqual(headers["user-agent"], PAN123_OFFLINE_USER_AGENT)

        asyncio.run(run())

    def test_reset_running_transfer_tasks_restores_running_files_to_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            store.save_transfer_task({
                "id": "task-1",
                "source": "test",
                "sourceText": "/云下载",
                "shareUrl": "115://local?path=%2F%E4%BA%91%E4%B8%8B%E8%BD%BD",
                "shareCode": "local:/云下载",
                "status": "running",
                "totalFiles": 2,
                "doneFiles": 1,
                "files": [
                    {"id": "1", "name": "done.mkv", "status": "success", "offlineTaskId": 11},
                    {"id": "2", "name": "running.mkv", "status": "running", "offlineTaskId": 12},
                ],
                "logs": [],
                "createdAt": "2026-07-24T00:00:00Z",
                "updatedAt": "2026-07-24T00:00:00Z",
            })

            self.assertEqual(store.reset_running_transfer_tasks(), 1)
            task = store.get_transfer_task("task-1")

            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["files"][0]["status"], "success")
            self.assertEqual(task["files"][1]["status"], "pending")
            self.assertEqual(task["files"][1]["offlineTaskId"], 12)
            self.assertEqual(task["files"][1]["error"], "服务重启后重试")

    def test_pan115_request_queue_serializes_concurrent_jobs_without_self_waiting(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"downloadMinIntervalMs": 0}})
                service = TransferService(store)
                completed = []

                async def job(index: int) -> int:
                    completed.append(index)
                    return index

                results = await asyncio.wait_for(
                    asyncio.gather(*[service._enqueue_pan115_download(lambda index=index: job(index)) for index in range(5)]),
                    timeout=1,
                )

                self.assertEqual(results, [0, 1, 2, 3, 4])
                self.assertEqual(completed, [0, 1, 2, 3, 4])

        asyncio.run(run())

    def test_local_download_url_submits_after_a_403_preflight(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"downloadMaxAttempts": 1, "downloadMinIntervalMs": 0}})
                service = TransferService(store)
                service._validate_pan115_download_url = AsyncMock(side_effect=RuntimeError("115 直链预检返回 HTTP 403"))  # type: ignore[method-assign]

                pan115 = AsyncMock()
                pan115.get_local_download_url.return_value = "https://example.invalid/download"
                task = {"shareCode": "local:cid:1", "logs": []}
                file = {"id": "1", "name": "episode.mkv", "sourceType": "115_local", "pickCode": "pickcode"}

                download_url = await service._get_pan115_download_url(task, file, pan115)

                self.assertEqual(download_url, "https://example.invalid/download")
                pan115.get_local_download_url.assert_awaited_once_with("pickcode")
                self.assertTrue(any("交由 123 离线验证" in item["message"] for item in task["logs"]))

        asyncio.run(run())

    # ------------------------------------------------------------------
    # 管线测试（解析→规划→秒传→离线→等待→收尾）
    # ------------------------------------------------------------------
    def _make_pipeline(self, files, pan123, saved_tasks=None, share_code="abc", concurrency=5):
        """构建带桩服务的 TransferPipeline。files 是"分享解析结果"。"""

        class PipelineServiceStub:
            def __init__(self):
                self.saved_tasks = saved_tasks if saved_tasks is not None else []

            async def _get_transfer_config(self):
                return {"targetDirId": "0", "offlinePollMs": 2000, "offlineMaxPolls": 60, "concurrency": concurrency}

            def _max_offline_slots(self, config):
                return 3

            async def _create_pan123_client(self):
                return pan123

            def _filter_live_accounts(self, accounts, task=None):
                return accounts or [{"name": "Cookie 1", "cookie": "UID=1; CID=x; SEID=y"}]

            def _local_source_pan115_account(self):
                raise RuntimeError("115 助手未配置")

            def _local_source_account_or_none(self):
                return None

            def _fallback_account(self):
                return {"name": "默认 Cookie", "cookie": "UID=1; CID=x; SEID=y"}

            async def _save_transfer_task(self, task):
                self.saved_tasks.append([
                    {
                        "method": item.get("method"),
                        "offlineStatus": item.get("offlineStatus"),
                        "offlineTaskId": item.get("offlineTaskId"),
                        "sourceUrl": item.get("sourceUrl"),
                    }
                    for item in task.get("files", [])
                ])
                return True

            async def _remember_transfer_hash(self, file, pan123_file):
                return None

            async def _remember_known_transfer_hash(self, file, etag):
                return None

            async def _delete_115_source_after_success_if_needed(self, task, file, account):
                return None

            async def _get_pan115_download_url(self, task, file, client, account=None):
                return f"https://115.invalid/{file['id']}"

            def _get_offline_submit_semaphore(self):
                return asyncio.Semaphore(3)

            async def _inspect_pan115_share(self, task, link, accounts, fallback_cookie):
                return {"accountIndex": 0, "inspection": {"title": "Demo", "files": files}}

        stub = PipelineServiceStub()
        task = {
            "id": "task-1",
            "source": "test",
            "sourceText": "https://115.com/s/abc",
            "shareUrl": "https://115.com/s/abc",
            "shareCode": share_code,
            "receiveCode": "pass",
            "title": "Demo",
            "status": "running",
            "totalFiles": 0,
            "doneFiles": 0,
            "files": [],
            "logs": [],
        }
        pipeline = TransferPipeline(stub, task, {"targetDirId": "0", "offlinePollMs": 2000, "offlineMaxPolls": 60, "concurrency": concurrency})
        return pipeline, stub, task

    def test_pipeline_submits_one_offline_task_per_file(self):
        async def run() -> None:
            files = [
                {
                    "id": str(index),
                    "name": f"episode-{index}.mkv",
                    "size": index * 1024,
                    "path": ["Season 1"],
                    "status": "pending",
                    "sourceType": "115_share",
                }
                for index in range(1, 6)
            ]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "99"
            state = {"downloaded": False}
            task_ids = iter([101, 102, 103, 104, 105])

            async def list_files(dir_id):
                if state["downloaded"] and str(dir_id) == "99":
                    return [
                        {"fileId": int(item["id"]), "filename": item["name"], "size": item["size"], "type": 0}
                        for item in files
                    ]
                return []

            async def create_offline(_url, _dir_id, _filename):
                state["downloaded"] = True
                return next(task_ids)

            pan123.list_files.side_effect = list_files
            pan123.create_offline_download.side_effect = create_offline
            pan123.find_file_by_size.return_value = None

            pipeline, _stub, task = self._make_pipeline(files, pan123)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()):
                await pipeline.run()

            self.assertEqual(pan123.create_offline_download.await_count, 5)
            submitted_names = {call.args[2] for call in pan123.create_offline_download.await_args_list}
            self.assertEqual(submitted_names, {file["name"] for file in files})
            self.assertEqual({file["offlineTaskId"] for file in task["files"]}, {101, 102, 103, 104, 105})
            self.assertTrue(all(file["status"] == "success" for file in task["files"]))
            self.assertEqual(task["status"], "success")

        asyncio.run(run())

    def test_pipeline_persists_offline_intent_before_creating_remote_task(self):
        async def run() -> None:
            files = [{"id": "1", "name": "episode.mkv", "size": 1024, "path": [], "status": "pending", "sourceType": "115_share"}]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "9"
            state = {"downloaded": False}
            saved_tasks = []

            async def list_files(dir_id):
                if state["downloaded"]:
                    return [{"fileId": 99, "filename": "episode.mkv", "size": 1024, "type": 0}]
                return []

            async def create_offline(_url, _dir_id, _filename):
                # 创建远端任务时，"准备提交"的意图必须已经落库
                assert any(
                    item.get("method") == "offline"
                    and item.get("offlineStatus") == "submitting"
                    and item.get("sourceUrl")
                    and not item.get("offlineTaskId")
                    for snapshot in saved_tasks for item in snapshot
                )
                state["downloaded"] = True
                return 777

            pan123.list_files.side_effect = list_files
            pan123.create_offline_download.side_effect = create_offline
            pan123.find_file_by_size.return_value = None

            pipeline, _stub, task = self._make_pipeline(files, pan123, saved_tasks=saved_tasks)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()):
                await pipeline.run()

            self.assertTrue(any(
                item.get("offlineTaskId") == 777 for snapshot in saved_tasks for item in snapshot
            ))
            self.assertEqual(task["files"][0]["status"], "success")
            self.assertEqual(task["files"][0]["offlineTaskId"], 777)

        asyncio.run(run())

    def test_pipeline_restart_with_submission_intent_never_creates_a_duplicate_offline_task(self):
        async def run() -> None:
            files = [{
                "id": "1",
                "name": "episode.mkv",
                "size": 1024,
                "path": [],
                "status": "pending",
                "method": "offline",
                "offlineStatus": "submitting",
                "sourceUrl": "https://115.invalid/episode",
            }]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "9"
            pan123.list_files.return_value = []
            pan123.find_file_by_size.return_value = None
            pan123.list_offline_tasks.return_value = []

            pipeline, _stub, task = self._make_pipeline(files, pan123)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()):
                await pipeline.run()

            pan123.create_offline_download.assert_not_awaited()
            self.assertEqual(task["files"][0]["status"], "failed")
            self.assertIn("停止自动重复添加", task["files"][0]["error"])

        asyncio.run(run())

    def test_offline_wait_never_queries_status_api_and_completes_via_listing(self):
        """等待阶段不查询 123 离线进度接口（OpenAPI 侧不稳定，只刷警告），完成判定只看目录落盘。"""
        async def run() -> None:
            files = [{"id": "1", "name": "episode.mkv", "size": 1024, "path": [], "status": "pending", "sourceType": "115_share"}]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "9"
            state = {"downloaded": False}

            async def list_files(dir_id):
                if state["downloaded"]:
                    return [{"fileId": 99, "filename": "episode.mkv", "size": 1024, "type": 0}]
                return []

            async def create_offline(_url, _dir_id, _filename):
                state["downloaded"] = True
                return 123

            pan123.list_files.side_effect = list_files
            pan123.create_offline_download.side_effect = create_offline
            pan123.find_file_by_size.return_value = None

            pipeline, _stub, task = self._make_pipeline(files, pan123)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()):
                await pipeline.run()

            pan123.get_offline_process.assert_not_awaited()
            pan123.list_offline_tasks.assert_not_awaited()
            self.assertEqual(task["files"][0]["status"], "success")
            self.assertEqual(task["files"][0]["offlineStatusText"], "成功")

        asyncio.run(run())

    def test_offline_timeout_resubmits_up_to_limit_then_fails(self):
        async def run() -> None:
            files = [{
                "id": "1", "name": "episode.mkv", "size": 1024, "path": [],
                "status": "pending", "sourceType": "115_share", "sha1": None,
            }]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "9"
            pan123.list_files.return_value = []  # 永远等不到落盘
            pan123.find_file_by_size.return_value = None

            pipeline, _stub, task = self._make_pipeline(files, pan123)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()), \
                    patch("app.transfer_pipeline._offline_wait_deadline_ms", return_value=30):
                await pipeline.run()

            # 首次提交 1 次 + 超时重提交 3 次
            self.assertEqual(pan123.create_offline_download.await_count, 4)
            self.assertEqual(task["files"][0]["status"], "failed")
            self.assertIn("超时", task["files"][0]["error"])
            self.assertTrue(any("自动重新提交离线任务" in log["message"] for log in task["logs"]))

        asyncio.run(run())

    def test_offline_inflight_never_exceeds_concurrency_cap(self):
        """同时提交到 123 的离线任务不得超过"并发"配置（完成一个补一个）。"""
        async def run() -> None:
            files = [
                {
                    "id": str(index),
                    "name": f"episode-{index}.mkv",
                    "size": index * 1024,
                    "path": ["Season 1"],
                    "status": "pending",
                    "sourceType": "115_share",
                }
                for index in range(1, 6)
            ]
            pan123 = AsyncMock()
            pan123.clientKind = "openapi"
            pan123.ensure_path.return_value = "99"
            state = {"downloaded": False, "next_id": 1}
            observed_inflight = []

            async def list_files(dir_id):
                if state["downloaded"] and str(dir_id) == "99":
                    return [
                        {"fileId": int(item["id"]), "filename": item["name"], "size": item["size"], "type": 0}
                        for item in files
                    ]
                return []

            async def create_offline(_url, _dir_id, _filename):
                state["downloaded"] = True
                # 每次提交时观察"已提交且未完成"的数量：不能超过并发上限
                observed_inflight.append(len(pipeline.offline.inflight_items()))
                task_id = state["next_id"]
                state["next_id"] += 1
                return task_id

            pan123.list_files.side_effect = list_files
            pan123.create_offline_download.side_effect = create_offline
            pan123.find_file_by_size.return_value = None

            pipeline, _stub, task = self._make_pipeline(files, pan123, concurrency=2)
            with patch("app.transfer_pipeline.Pan115TransferClient", MagicMock()), \
                    patch("app.transfer_pipeline._delay", new=AsyncMock()):
                await pipeline.run()

            self.assertTrue(observed_inflight)
            self.assertLessEqual(max(observed_inflight), 2)
            self.assertTrue(all(file["status"] == "success" for file in task["files"]))
            self.assertEqual(task["status"], "success")
            self.assertEqual({file["offlineTaskId"] for file in task["files"]}, {1, 2, 3, 4, 5})

        asyncio.run(run())

    def test_success_cleans_up_queued_and_user_messages_keeps_final(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                service = TransferService(store)
                cleaned = []
                service.set_cleanup_notifier(lambda payload: cleaned.append(dict(payload)) or asyncio.sleep(0))
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]

                # 入队通知返回引用 → 记录到任务上
                service.set_queued_notifier(AsyncMock(return_value=[
                    {"taskId": "task-1", "chatId": 123, "messageId": 100},
                ]))
                task = {"id": "task-1", "status": "queued", "chatId": 123, "messageId": 99, "logs": []}
                await service._notify_queued_tasks([task])
                self.assertEqual(task["transferNoticeChatId"], 123)
                self.assertEqual(task["transferNoticeMessageIds"], [100])

                # 成功终态：删除排队消息 100 和用户链接消息 99；最终结果消息 101 保留
                service.set_notifier(AsyncMock(return_value={"chatId": 123, "messageId": 101}))
                task["status"] = "success"
                task["kind"] = "pan115_share"
                await service._notify_task(task)

                self.assertEqual(len(cleaned), 1)
                self.assertEqual(sorted(cleaned[0]["messageIds"]), [99, 100])
                self.assertEqual(cleaned[0]["chatId"], 123)
                # 最终结果消息保留：其引用记录在任务上（任务记录随后会被清理）
                self.assertEqual(task["transferFinalMessageId"], 101)

        asyncio.run(run())

    def test_offline_wait_deadline_adapts_to_file_size(self):
        from app.transfer_service import _offline_wait_deadline_ms

        configured = 60 * 60_000
        self.assertEqual(_offline_wait_deadline_ms(100 * 1024 * 1024, configured), configured)
        self.assertEqual(_offline_wait_deadline_ms(600 * 1024 * 1024, configured), 90 * 60_000)
        self.assertEqual(_offline_wait_deadline_ms(3 * 1024 * 1024 * 1024, configured), 2 * 60 * 60_000)
        self.assertEqual(_offline_wait_deadline_ms(20 * 1024 * 1024 * 1024, configured), 4 * 60 * 60_000)
        # 配置更长时以配置为准
        self.assertEqual(_offline_wait_deadline_ms(20 * 1024 * 1024 * 1024, 5 * 60 * 60_000), 5 * 60 * 60_000)


if __name__ == "__main__":
    unittest.main()
