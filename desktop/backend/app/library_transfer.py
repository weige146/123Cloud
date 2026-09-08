"""影库转存：把影库里的作品/分类通过 123 官方 OpenAPI 秒传（md5_reuse）进用户网盘。

复用客户端既有的 123 授权登录态（transfer_service.create_status_pan123_client），
目录逐级 ensure_path，文件逐条 md5_reuse，单线程限速轮询进度。对应参考项目
Pan123Client.import_json 的能力，但走官方 OpenAPI、无需账密登录。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .movie_library import etag_hex

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MS = 200
MIN_INTERVAL_MS = 0
DEFAULT_CONCURRENCY = 5
MAX_CONCURRENCY = 10
MAX_LOG_LINES = 200


class LibraryTransferManager:
    """影库转存任务管理：task_id -> {status, progress, result, error}。"""

    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}

    def start_task(
        self,
        pan123_client: Any,
        works: List[Dict[str, Any]],
        target_path: str = "",
        target_dir_id: str = "0",
        interval_ms: int = DEFAULT_INTERVAL_MS,
        label: str = "",
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> str:
        """创建后台转存任务。

        works: [{"dir": "电影/华语/海王 (2018) {tmdb-297802}",
                 "files": [{"fileName","size","etag"}]}]
        目标目录：target_dir_id 优先，否则按 target_path 在网盘根目录逐级创建。
        每个作品保持影库里的目录层级，挂在目标目录之下。
        """
        tid = uuid.uuid4().hex[:12]
        interval = max(MIN_INTERVAL_MS, int(interval_ms or DEFAULT_INTERVAL_MS)) / 1000.0
        self.tasks[tid] = {
            "taskId": tid,
            "status": "running",
            "label": label,
            "cancelRequested": False,
            "progress": {
                "step": "排队中…",
                "done": 0,
                "total": sum(len(w.get("files") or []) for w in works),
                "success": 0,
                "missed": 0,
                "failed": 0,
                "workIndex": 0,
                "workCount": len(works),
                "currentFile": "",
                "targetPath": target_path,
                "targetDirId": str(target_dir_id or "0"),
                "log": [],
            },
            "result": None,
            "error": None,
            "createdAt": time.time(),
        }
        concurrency = max(1, min(MAX_CONCURRENCY, int(concurrency or DEFAULT_CONCURRENCY)))
        coro = self._run(tid, pan123_client, works, str(target_path or ""), str(target_dir_id or "0"), interval, concurrency)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self.tasks[tid]["_task"] = loop.create_task(coro)
        else:
            # 无事件循环上下文（测试/同步线程）：独立线程里跑一个 loop，取消仍靠协作标志
            threading.Thread(target=lambda: asyncio.run(coro), name="library-transfer", daemon=True).start()
        return tid

    def _log(self, task: Dict[str, Any], line: str) -> None:
        log: List[str] = task["progress"]["log"]
        log.append(line)
        if len(log) > MAX_LOG_LINES:
            del log[:-MAX_LOG_LINES]

    async def _run(self, tid: str, pan123_client: Any, works: List[Dict[str, Any]],
                   target_path: str, target_dir_id: str, interval: float, concurrency: int) -> None:
        """并发秒传（对齐 123 助手 importFastlink 的并发模型）：
        目录先逐级建好，文件按并发数分 worker 秒传，单 worker 间隔 interval 秒。"""
        task = self.tasks[tid]
        progress = task["progress"]
        try:
            if not works:
                raise ValueError("没有可转存的作品")
            progress["startedAt"] = time.time()
            progress["concurrency"] = concurrency
            # 1. 目标根目录
            if target_dir_id and target_dir_id != "0":
                root_id = target_dir_id
                self._log(task, f"目标目录 ID：{root_id}")
            elif target_path:
                parts = [p.strip() for p in target_path.strip("/").split("/") if p.strip()]
                progress["step"] = f"创建目标目录：{target_path}"
                root_id = await pan123_client.ensure_path("0", parts)
                self._log(task, f"目标目录：{target_path}（ID {root_id}）")
            else:
                root_id = "0"
                self._log(task, "目标目录：根目录")
            progress["targetDirId"] = str(root_id)

            # 2. 逐作品建目录（串行，数量少）
            counters = {"success": 0, "missed": 0, "failed": 0, "done": 0}
            abort: Optional[str] = None
            queue: asyncio.Queue = asyncio.Queue()
            total_files = int(progress["total"])
            for work_index, work in enumerate(works):
                if task["cancelRequested"]:
                    abort = "已取消"
                    break
                work_dir = str(work.get("dir") or "").strip("/")
                files = work.get("files") or []
                progress["workIndex"] = work_index + 1
                progress["step"] = f"（{work_index + 1}/{len(works)}）准备目录：{work_dir or '(根)'}"
                if work_dir:
                    parent_id = await pan123_client.ensure_path(
                        root_id, [p for p in work_dir.split("/") if p.strip()],
                    )
                else:
                    parent_id = root_id
                self._log(task, f"→ {work_dir or '(根)'}（目录 ID {parent_id}，{len(files)} 个文件）")
                for f in files:
                    queue.put_nowait((parent_id, f))

            # 3. 并发 worker 秒传
            async def worker() -> None:
                nonlocal abort
                while not (task["cancelRequested"] or abort):
                    try:
                        parent_id, f = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    name = str(f.get("fileName") or str(f.get("path") or "").rsplit("/", 1)[-1] or "未知文件")
                    size = int(f.get("size") or 0)
                    hex_md5 = etag_hex(f.get("etag"))
                    progress["currentFile"] = name
                    if not hex_md5 or not size:
                        counters["failed"] += 1
                        counters["done"] += 1
                        self._log(task, f"✗ 跳过（etag 无效或大小为 0）：{name}")
                    else:
                        try:
                            file_id = await pan123_client.md5_reuse(str(parent_id), name, hex_md5, size)
                            if file_id:
                                counters["success"] += 1
                                self._log(task, f"✓ {name}（ID {file_id}）")
                            else:
                                counters["missed"] += 1
                                self._log(task, f"○ 未命中秒传（123 没有这个文件）：{name}")
                        except Exception as error:
                            code = int(getattr(error, "code", 0) or 0)
                            if code in (401, 403):
                                counters["failed"] += 1
                                abort = f"授权失效（{error}），任务终止"
                                self._log(task, f"✗ {abort}")
                            else:
                                counters["failed"] += 1
                                self._log(task, f"✗ {name}：{error}")
                    counters["done"] += 1
                    progress["done"] = counters["done"]
                    progress["success"] = counters["success"]
                    progress["missed"] = counters["missed"]
                    progress["failed"] = counters["failed"]
                    progress["step"] = f"[{counters['done']}/{total_files}] {name[:40]}"
                    if interval > 0:
                        await asyncio.sleep(interval)

            worker_count = max(1, min(concurrency, total_files or 1))
            await asyncio.gather(*(worker() for _ in range(worker_count)))

            task["cancelRequested"] = False
            success, missed, failed = counters["success"], counters["missed"], counters["failed"]
            done = counters["done"]
            if abort and done < total_files:
                task["status"] = "error" if abort.startswith("授权") else "cancelled"
                progress["step"] = f"{abort}：已成功 {success} · 未命中 {missed} · 失败 {failed}"
                task["error"] = abort
                logger.info("影库转存：%s（%s）", abort, task["label"])
                return
            task["status"] = "done"
            elapsed = max(0.001, time.time() - float(progress.get("startedAt") or time.time()))
            progress["step"] = f"完成！成功 {success} · 未命中 {missed} · 失败 {failed}（{elapsed:.0f} 秒，均速 {done / elapsed:.1f} 个/秒）"
            task["result"] = {
                "total": total_files,
                "success": success,
                "missed": missed,
                "failed": failed,
                "works": len(works),
                "targetDirId": str(progress.get("targetDirId") or root_id),
                "targetPath": target_path,
                "elapsed": round(elapsed, 1),
            }
            logger.info(
                "影库转存：完成 %s — %d 个作品、成功 %d、未命中 %d、失败 %d、耗时 %.0f 秒",
                task["label"] or "", len(works), success, missed, failed, elapsed,
            )
        except Exception as error:
            task["status"] = "error"
            task["error"] = str(error)
            task["progress"]["step"] = f"失败：{error}"
            logger.warning("影库转存：任务异常 %s", error)

    def get_task(self, tid: str) -> Optional[Dict[str, Any]]:
        task = self.tasks.get(tid)
        if task is None:
            return None
        return {k: v for k, v in task.items() if not k.startswith("_")}

    def request_cancel(self, tid: str) -> bool:
        task = self.tasks.get(tid)
        if task is None or task.get("status") != "running":
            return False
        task["cancelRequested"] = True
        return True

    def prune(self, keep: int = 30) -> None:
        """清理已结束的旧任务，防止内存无限增长。"""
        finished = [k for k, v in self.tasks.items() if v.get("status") != "running"]
        finished.sort(key=lambda k: self.tasks[k].get("createdAt", 0))
        for k in finished[:-keep]:
            self.tasks.pop(k, None)


transfer_manager = LibraryTransferManager()
