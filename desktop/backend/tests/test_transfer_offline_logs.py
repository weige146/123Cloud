"""离线等待阶段的日志：只保留成功/失败，不再有每轮一次的心跳提示。

后台"离线轮询间隔"默认 15 秒，等待阶段每轮打印一条"离线下载进行中…15 秒后再检查"
会把每个文件的成功/失败日志全部淹掉，这里把这条日志去掉，同时保留 60 秒一次的静默
存档（用来及时感知任务被删除/取消）。
"""

from __future__ import annotations

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
            tp, "_offline_wait_deadline_ms", lambda size, total: 10**9
        ), patch.object(tp, "_add_task_log", record):
            with self.assertRaises(_StopLoop):
                await manager.wait_all()

        self.assertEqual(logs, [], "等待阶段不应再打印任何日志，成功/失败之外的一律去掉")
        # 静默存档还在：任务被删除/取消时 wait_all 仍能及时抛出 TaskCancelled
        self.assertEqual(pipeline.service.saved, 1)


if __name__ == "__main__":
    unittest.main()
