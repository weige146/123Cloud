"""独立秒传池 API。仅由服务器运行，不能作为普通桌面端的数据库代理配置分发。"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
import weakref
from collections import deque
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .sha1_cloud import (
    Sha1CloudClient, _normalize_sha1, _normalize_etag, _normalize_size, _normalize_name,
)


def create_app() -> FastAPI:
    tokens = [v.strip() for v in os.environ.get("SHA1_POOL_API_TOKENS", "").split(",") if v.strip()]
    if not tokens or any(len(t) < 32 or t == "CHANGE_ME" or not t.isascii() for t in tokens):
        raise RuntimeError("Configure separate random API tokens of at least 32 ASCII characters")
    db = Sha1CloudClient()
    if db._ensure_config() is None:
        raise RuntimeError("Pool database configuration is missing or invalid")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    app.state.db = db
    windows = [deque() for _ in tokens]
    # Python 3.9 的 asyncio.Semaphore 在构造时绑定事件循环，而 TestClient/uvicorn factory
    # 可能在无循环或多个短命循环的环境下调用端点。按运行中的循环各建一个，
    # 生产单进程单循环下语义不变（仍是全局 8 并发上限）。
    loop_slots: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = weakref.WeakKeyDictionary()

    def _slots() -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = loop_slots.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(8)
            loop_slots[loop] = semaphore
        return semaphore

    @app.middleware("http")
    async def protect(request: Request, call_next):
        value = request.headers.get("authorization", "")
        token = value[7:] if value.startswith("Bearer ") else ""
        match = None
        for index, expected in enumerate(tokens):
            if hmac.compare_digest(token.encode("utf-8"), expected.encode("ascii")):
                match = index
        if match is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        now = time.monotonic()
        window = windows[match]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= 120:
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        window.append(now)
        try:
            return await call_next(request)
        except Exception:
            # 不记录异常参数或堆栈，也不把 DB/DSN/token 发回客户端。
            return JSONResponse({"error": "unavailable"}, status_code=503)

    @app.get("/lookup")
    async def lookup(request: Request):
        q = request.query_params
        if len(q.multi_items()) != 2 or set(q) not in ({"sha1", "size"}, {"etag", "size"}):
            return JSONResponse({"error": "invalid_input"}, status_code=400)
        size = _normalize_size(q.get("size"))
        kind = "sha1" if "sha1" in q else "etag"
        fingerprint = (_normalize_sha1 if kind == "sha1" else _normalize_etag)(q.get(kind))
        if size is None or fingerprint is None:
            return JSONResponse({"error": "invalid_input"}, status_code=400)
        async with _slots():
            if kind == "sha1":
                etag = await asyncio.to_thread(app.state.db.lookup_etag_by_sha1, fingerprint, size)
                return {"etag": etag}
            result = await asyncio.to_thread(app.state.db.lookup_sha1_by_etag, fingerprint, size)
            return {"sha1": result["sha1"] if result else None}

    @app.post("/submit")
    async def submit(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "json_required"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return JSONResponse({"error": "body_too_large"}, status_code=413)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "invalid_input"}, status_code=400)
        if not isinstance(data, dict) or set(data) != {"sha1", "size", "etag", "name"}:
            return JSONResponse({"error": "invalid_input"}, status_code=400)
        values = (_normalize_sha1(data["sha1"]), _normalize_size(data["size"]),
                  _normalize_etag(data["etag"]), _normalize_name(data["name"]))
        if any(v is None for v in values):
            return JSONResponse({"error": "invalid_input"}, status_code=400)
        async with _slots():
            ok = await asyncio.to_thread(app.state.db.learn_transfer, *values)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    return app
