from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.session_store import SessionStore
from app.pan115_transfer import PAN123_OFFLINE_USER_AGENT, Pan115TransferClient
from app.pan123 import Pan123Error
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

    def test_successful_telegram_share_and_local_tasks_cleanup_user_and_bot_messages(self):
        async def run(share_code: str) -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                cleaned = []

                async def cleanup(payload):
                    cleaned.append(payload)

                service.set_cleanup_notifier(cleanup)
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
                await service._notify_task({
                    "id": f"task-{share_code}",
                    "status": "success",
                    "source": "telegram",
                    "shareCode": share_code,
                    "chatId": 1001,
                    "messageId": 2001,
                    "transferNoticeMessageIds": [2002, 2003],
                })

                self.assertEqual(len(cleaned), 1)
                self.assertEqual(cleaned[0]["chatId"], 1001)
                self.assertEqual(set(cleaned[0]["messageIds"]), {2001, 2002, 2003})

        for share_code in ("share-code", "local:/云下载"):
            with self.subTest(share_code=share_code):
                asyncio.run(run(share_code))

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

    def test_multiple_files_each_create_an_openapi_offline_task(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"downloadMinIntervalMs": 0}})
                service = TransferService(store)
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
                service._notify_cookie_use = AsyncMock()  # type: ignore[method-assign]
                service._get_pan115_download_url = AsyncMock(  # type: ignore[method-assign]
                    side_effect=lambda task, file, client, account=None: f"https://115.invalid/{file['id']}"
                )
                service._snapshot_candidate_file_ids = AsyncMock(return_value={})  # type: ignore[method-assign]

                async def wait_for_file(task, file, pan123, target_root_id, target_dir_id, before_ids, dir_cache=None):
                    return {
                        "fileId": int(file["id"]),
                        "filename": file["name"],
                        "size": file["size"],
                    }

                service._wait_for_offline_file = AsyncMock(side_effect=wait_for_file)  # type: ignore[method-assign]

                pan123 = AsyncMock()
                pan123.ensure_path.return_value = "99"
                pan123.find_same_file.return_value = None
                pan123.create_offline_download.side_effect = [101, 102, 103, 104, 105]

                async def limiter(job):
                    return await job()

                task = {"id": "task-5", "status": "running", "logs": []}
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

                await asyncio.gather(*[
                    service._process_file(
                        task,
                        file,
                        {"name": "pool", "cookie": "UID=1; CID=x; SEID=y"},
                        pan123,
                        "0",
                        limiter,
                    )
                    for file in files
                ])

                self.assertEqual(pan123.create_offline_download.await_count, 5)
                submitted_names = {
                    call.args[2] for call in pan123.create_offline_download.await_args_list
                }
                self.assertEqual(submitted_names, {file["name"] for file in files})
                self.assertTrue(all(file["status"] == "success" for file in files))
                self.assertEqual({file["offlineTaskId"] for file in files}, {101, 102, 103, 104, 105})

        asyncio.run(run())

    def test_offline_submission_persists_intent_and_remote_id_before_polling(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                snapshots = []

                async def save(task):
                    snapshots.append([
                        {
                            "method": item.get("method"),
                            "offlineStatus": item.get("offlineStatus"),
                            "offlineTaskId": item.get("offlineTaskId"),
                            "sourceUrl": item.get("sourceUrl"),
                        }
                        for item in task.get("files", [])
                    ])
                    return True

                service._save_transfer_task = AsyncMock(side_effect=save)  # type: ignore[method-assign]
                service._notify_cookie_use = AsyncMock()  # type: ignore[method-assign]
                service._get_pan115_download_url = AsyncMock(return_value="https://115.invalid/episode")  # type: ignore[method-assign]
                service._snapshot_candidate_file_ids = AsyncMock(return_value={})  # type: ignore[method-assign]

                async def create_offline(_url, _dir_id, _filename):
                    self.assertTrue(any(
                        item.get("method") == "offline"
                        and item.get("offlineStatus") == "submitting"
                        and item.get("sourceUrl")
                        and not item.get("offlineTaskId")
                        for snapshot in snapshots for item in snapshot
                    ))
                    return 777

                async def wait_for_file(_task, file, _pan123, _root_id, _dir_id, _before_ids, _dir_cache=None):
                    self.assertTrue(any(
                        item.get("offlineTaskId") == 777
                        for snapshot in snapshots for item in snapshot
                    ))
                    return {"fileId": 99, "filename": file["name"], "size": file["size"]}

                pan123 = AsyncMock()
                pan123.ensure_path.return_value = "9"
                pan123.find_same_file.return_value = None
                pan123.create_offline_download.side_effect = create_offline
                service._wait_for_offline_file = AsyncMock(side_effect=wait_for_file)  # type: ignore[method-assign]

                file = {"id": "1", "name": "episode.mkv", "size": 1024, "path": [], "status": "pending"}
                task = {"id": "task", "status": "running", "files": [file], "logs": []}

                async def limiter(job):
                    return await job()

                await service._process_file(
                    task, file, {"name": "pool", "cookie": "cookie"}, pan123, "0", limiter
                )

                self.assertEqual(file["status"], "success")
                self.assertEqual(file["offlineTaskId"], 777)

        asyncio.run(run())

    def test_restart_with_submission_intent_never_creates_a_duplicate_offline_task(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                service._save_transfer_task = AsyncMock(return_value=True)  # type: ignore[method-assign]
                service._recover_offline_file = AsyncMock(return_value=None)  # type: ignore[method-assign]
                service._find_offline_task = AsyncMock(return_value=None)  # type: ignore[method-assign]

                pan123 = AsyncMock()
                pan123.ensure_path.return_value = "9"
                pan123.find_same_file.return_value = None
                file = {
                    "id": "1",
                    "name": "episode.mkv",
                    "size": 1024,
                    "path": [],
                    "status": "pending",
                    "method": "offline",
                    "offlineStatus": "submitting",
                    "sourceUrl": "https://115.invalid/episode",
                }
                task = {"id": "task", "status": "running", "files": [file], "logs": []}

                async def limiter(job):
                    return await job()

                await service._process_file(
                    task, file, {"name": "pool", "cookie": "cookie"}, pan123, "0", limiter
                )

                pan123.create_offline_download.assert_not_awaited()
                self.assertEqual(file["status"], "failed")
                self.assertIn("停止自动重复添加", file["error"])

        asyncio.run(run())

    def test_openapi_offline_process_is_used_for_status(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                pan123 = AsyncMock()
                pan123.clientKind = "openapi"
                pan123.get_offline_process.return_value = {"status": 1, "process": 42}

                result = await service._find_offline_task(pan123, 123, ["episode.mkv"])

                self.assertEqual(result["id"], 123)
                self.assertEqual(result["status"], "running")
                self.assertEqual(result["progress"], 42)
                pan123.get_offline_process.assert_awaited_once_with(123)
                pan123.list_offline_tasks.assert_not_awaited()

        asyncio.run(run())

    def test_account_cooling_skips_and_recovers(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                service = TransferService(SessionStore(Path(directory)))
                task = {"id": "t", "logs": []}
                accounts = [
                    {"name": "A", "cookie": "UID=1; CID=a; SEID=x"},
                    {"name": "B", "cookie": "UID=2; CID=b; SEID=y"},
                ]

                self.assertEqual(service._filter_live_accounts(accounts, task), accounts)
                marked = await service._mark_account_cooling(task, accounts[0], "[errno 990001] 需要登录")
                self.assertTrue(marked)
                live = service._filter_live_accounts(accounts, task)
                self.assertEqual([a["name"] for a in live], ["B"])
                # 冷却期内重复标记不重复通知
                again = await service._mark_account_cooling(task, accounts[0], "[errno 990001] 需要登录")
                self.assertFalse(again)
                # 冷却到期恢复
                fingerprint = service._account_fingerprint(accounts[0]["cookie"])
                service._account_health[fingerprint]["coolUntilMs"] = 0
                self.assertEqual(service._filter_live_accounts(accounts, task), accounts)

        asyncio.run(run())

    def test_expired_download_url_rotates_to_next_account(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SessionStore(Path(directory))
                store.write_config({"transfer": {"pan115Cookies": ["B|UID=2; CID=b; SEID=y"], "downloadMinIntervalMs": 0}})
                service = TransferService(store)
                service._remember_config(store.read_config().get("transfer"))
                task = {"id": "t", "shareCode": "abc", "receiveCode": "pass", "logs": []}
                file = {"id": "9", "name": "movie.mkv", "size": 1, "sourceType": "115_share"}

                primary = {"name": "A", "cookie": "UID=1; CID=a; SEID=x"}
                good_client = AsyncMock()
                good_client.get_download_url.return_value = "https://115.invalid/ok"
                # 主账号客户端返回未登录错误，验证池内换号
                bad_client = AsyncMock()
                bad_client.get_download_url.side_effect = ValueError("[errno 990001] 需要登录账号")
                service._validate_pan115_download_url = AsyncMock()  # type: ignore[method-assign]

                with patch("app.transfer_service.Pan115TransferClient", side_effect=lambda cookie: good_client):
                    url = await service._get_pan115_download_url(task, file, bad_client, primary)

                self.assertEqual(url, "https://115.invalid/ok")
                # 主账号被标记冷却
                self.assertTrue(service._is_account_cooling(primary))
                self.assertTrue(any("115 账号失效" in item["message"] for item in task["logs"]))

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
