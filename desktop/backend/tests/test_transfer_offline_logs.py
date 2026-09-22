"""离线等待阶段的日志：只保留成功/失败，不再有每轮一次的心跳提示。

后台"离线轮询间隔"默认 15 秒，等待阶段每轮打印一条"离线下载进行中…15 秒后再检查"
会把每个文件的成功/失败日志全部淹掉，这里把这条日志去掉，同时保留 60 秒一次的静默
存档（用来及时感知任务被删除/取消）。
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from app import transfer_pipeline as tp
from app.transfer_pipeline import OfflineDownloadManager


class _StopLoop(Exception):
    pass


class _FakeDirCache:
    async def list_dir(self, pan123, dir_id, force=False):
        return {}

    def invalidate_dir(self, dir_id):
        return None


class _FakeService:
    def __init__(self) -> None:
        self.saved = 0

    async def _save_transfer_task(self, task):
        self.saved += 1
        return True


class _FakePipeline:
    def __init__(self) -> None:
        self.task = {"id": "task-1", "files": [], "logs": []}
        self.config = {"offlinePollMs": 15000, "offlineMaxPolls": 240}
        self.pan123 = None
        self.dir_cache = _FakeDirCache()
        self.service = _FakeService()
        self.target_root_id = "0"

    async def recover_offline_file(self, file, target_dir_id, target_root_id, before_ids):
        return None


class _QuietManager(OfflineDownloadManager):
    async def _find_suspicious_artifact(self, item):
        return None


class OfflineWaitLoggingTest(unittest.IsolatedAsyncioTestCase):
    async def test_wait_all_does_not_log_progress_heartbeat(self):
        pipeline = _FakePipeline()
        manager = _QuietManager(pipeline, max_inflight=5)
        file = {"name": "movie.mkv", "size": 4 * 1024 * 1024 * 1024, "targetDirId": "123", "method": "offline"}
        item = tp.OfflineItem(file, "123", "0")
        item.submitted = True  # 已提交、未完成：模拟一直等 123 离线落盘
        manager.items.append(item)

        logs: list = []

        async def fake_delay(ms):
            # 直接跳出等待循环：一轮就够验证"没有心跳日志"
            raise _StopLoop()

        def record(task, level, message):
            logs.append(message)

        with patch.object(tp, "_delay", fake_delay), patch.object(
            tp, "_offline_wait_deadline_ms", lambda total: 10**9
        ), patch.object(tp, "_add_task_log", record):
            with self.assertRaises(_StopLoop):
                await manager.wait_all()

        self.assertEqual(logs, [], "等待阶段不应再打印任何日志，成功/失败之外的一律去掉")
        # 静默存档还在：任务被删除/取消时 wait_all 仍能及时抛出 TaskCancelled
        self.assertEqual(pipeline.service.saved, 1)


class _SlotCountingService(_FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.released = 0

    async def release_offline_slot(self):
        self.released += 1


class OfflineFillNeverBlocksTest(unittest.IsolatedAsyncioTestCase):
    """名额满时 fill() 必须立即返回、绝不等待：等待循环停在补交上就没人在轮询
    落盘，在飞文件完成了也检测不到、名额永不释放，两个任务并发互抢会把彼此
    全部冻死（2026-09-22 实测：两任务并发搬运，落盘 40 分钟无人认领、
    23 个排队文件不前进，只能重启）。"""

    async def test_fill_returns_without_submitting_when_slot_pool_full(self):
        pipeline = _FakePipeline()

        class _FullService(_FakeService):
            def __init__(self) -> None:
                super().__init__()
                self.acquire_calls = 0

            async def try_acquire_offline_slot(self):
                self.acquire_calls += 1
                return False

        service = _FullService()
        pipeline.service = service
        manager = _QuietManager(pipeline, max_inflight=5)
        manager.queue(tp.OfflineItem({"name": "a.mkv", "size": 10, "targetDirId": "123", "method": "offline"}, "123", "0"))
        manager.queue(tp.OfflineItem({"name": "b.mkv", "size": 10, "targetDirId": "123", "method": "offline"}, "123", "0"))

        await asyncio.wait_for(manager.fill(), timeout=1)

        self.assertEqual(len(manager.pending), 2, "抢不到名额时文件应留在排队里")
        self.assertEqual(len(manager.items), 2)
        self.assertFalse(manager.items[0].submitted, "没抢到名额绝不能提交")
        self.assertFalse(manager.items[0].slot_held)
        self.assertEqual(service.acquire_calls, 1, "试一次就收手，不空转")


class OfflineSlotBookkeepingTest(unittest.IsolatedAsyncioTestCase):
    """全局离线名额的簿记：到终态（完成/失败）或任务退出时要归还，没占名额不能多还。"""

    async def test_mark_failed_releases_held_slot(self):
        pipeline = _FakePipeline()
        service = _SlotCountingService()
        pipeline.service = service
        manager = _QuietManager(pipeline)
        item = tp.OfflineItem({"name": "a.mkv", "size": 10, "method": "offline"}, "123", "0")
        item.slot_held = True
        manager.items.append(item)

        await manager._mark_failed(item, "测试失败")

        self.assertTrue(item.failed)
        self.assertFalse(item.slot_held)
        self.assertEqual(service.released, 1)

    async def test_mark_failed_without_slot_does_not_release(self):
        pipeline = _FakePipeline()
        service = _SlotCountingService()
        pipeline.service = service
        manager = _QuietManager(pipeline)
        item = tp.OfflineItem({"name": "a.mkv", "size": 10, "method": "offline"}, "123", "0")
        manager.items.append(item)

        await manager._mark_failed(item, "测试失败")

        self.assertEqual(service.released, 0, "没占名额不应多还")

    async def test_release_all_slots_only_releases_held(self):
        pipeline = _FakePipeline()
        service = _SlotCountingService()
        pipeline.service = service
        manager = _QuietManager(pipeline)
        held = tp.OfflineItem({"name": "held.mkv", "size": 10}, "123", "0")
        held.slot_held = True
        pending = tp.OfflineItem({"name": "pending.mkv", "size": 10}, "123", "0")
        manager.items.extend([held, pending])

        await manager.release_all_slots()

        self.assertEqual(service.released, 1)
        self.assertFalse(held.slot_held)
        self.assertFalse(pending.slot_held)


if __name__ == "__main__":
    unittest.main()
