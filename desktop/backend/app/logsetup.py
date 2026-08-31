"""统一日志配置（参照 tdr-123help 的三路输出方案）。

- 控制台：供 Electron 壳捕获转发到界面
- 落盘：data/logs/backend.log 轮转文件（5MB × 3），重启后仍可排查
- 内存环形缓冲：最近 10000 条格式化日志，通过 GET /api/logs 读取，
  管理后台"运行日志"页面轮询显示

日志内容约束：应用层只输出大白话的流程叙述（做了什么、进行到哪、
为什么等待/重试），第三方库（httpx、telethon 等）一律降噪到 WARNING，
保证小白看日志也能知道程序在干嘛、没有被无关信息干扰。
"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MEMORY_LOG_CAPACITY = 10_000

# 这些第三方库每个请求都会打一条 INFO，会把流程日志刷掉，统一压到 WARNING
QUIET_LOGGERS = ("httpx", "httpcore", "telethon", "urllib3", "asyncio")

_memory_handler: Optional["MemoryLogHandler"] = None


class MemoryLogHandler(logging.Handler):
    """把格式化后的日志行存进内存环形队列，供 /api/logs 读取。"""

    def __init__(self, capacity: int = MEMORY_LOG_CAPACITY) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        self._lock = threading.Lock()
        self._lines = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self._lines.append(line)

    def snapshot(self, limit: int = 0) -> List[str]:
        with self._lock:
            lines = list(self._lines)
        if limit and limit > 0:
            return lines[-limit:]
        return lines


def setup_logging(data_dir: Optional[Path] = None, log_file: Optional[str] = None) -> None:
    """初始化根 logger；重复调用会先清掉旧 handler（便于测试）。"""
    global _memory_handler

    root = logging.getLogger()
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    resolved_file = log_file or os.environ.get("LOG_FILE")
    if resolved_file is None and data_dir is not None:
        resolved_file = str(Path(data_dir) / "logs" / "backend.log")
    if resolved_file and resolved_file.lower() not in {"off", "none", "disabled"}:
        try:
            Path(resolved_file).resolve().parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                resolved_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception as error:  # 落盘失败不应阻塞启动
            print(f"[logsetup] log file init failed: {error}", flush=True)

    _memory_handler = MemoryLogHandler()
    root.addHandler(_memory_handler)

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def recent_logs(limit: int = 1000) -> List[str]:
    """返回最近 limit 条格式化日志行；日志未初始化时返回空列表。"""
    if _memory_handler is None:
        return []
    return _memory_handler.snapshot(limit)
