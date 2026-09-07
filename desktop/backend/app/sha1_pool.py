"""秒传池目录搜索（管理员自用）。

按《开发者-AI对接文档-搜索接口》的守则实现，负载保护在后端这一层强制执行：
- /search 仅管理员 Token 可用：首次收到 403 即在进程内停用，之后不再发任何请求
- 搜索结果本地缓存 10 分钟，缓存期内重复搜索不打接口
- 429 / 5xx 不自动重试，把状态原样交给前端提示
- 每批 SHA1 秒传 ≤20 条、条间隔 ≥1 秒

配置复用秒传池的环境变量（SHA1_POOL_API_URL / SHA1_POOL_API_TOKEN / SHA1_POOL_API_CA），
未配置时整个功能停用。日志与流量中不出现 Token。
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl as _ssl
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .sha1_cloud import (
    _build_ssl_context, _normalize_name, _normalize_sha1, _normalize_size, get_pool_token_override,
)

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT = 10.0
_CACHE_TTL = 600.0
_CACHE_MAX_KEYS = 200
_REUSE_BATCH_MAX = 20
_REUSE_INTERVAL = 1.0


def _load_config() -> Optional[Dict[str, str]]:
    url = str(os.environ.get("SHA1_POOL_API_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("SHA1_POOL_API_TOKEN") or "").strip()
    ca = str(os.environ.get("SHA1_POOL_API_CA") or "").strip()
    if not url.startswith("https://") or not token:
        return None
    if ca and not os.path.isfile(ca):
        ca = ""
    return {"url": url, "token": token, "ca": ca}


class PoolSearchClient:
    """搜索接口的最小客户端：显式触发 + 本地缓存 + 403 即停用。"""

    def __init__(self) -> None:
        self._admin_disabled = False  # 收到 403 后置 True：本进程内不再查询
        self._cache: Dict[Tuple[str, int], Tuple[float, List[Dict[str, Any]]]] = {}

    def reset_state(self) -> None:
        """切换 Token 后调用：清掉 403 停用标记与缓存，允许用新 Token 重新探测。"""
        self._admin_disabled = False
        self._cache.clear()

    def reset_for_test(self) -> None:
        self.reset_state()

    def _prune_cache(self, now: float) -> None:
        expired = [key for key, (ts, _) in self._cache.items() if now - ts >= _CACHE_TTL]
        for key in expired:
            self._cache.pop(key, None)
        while len(self._cache) > _CACHE_MAX_KEYS:
            self._cache.pop(min(self._cache, key=lambda key: self._cache[key][0]), None)

    async def search(self, keyword: str, limit: int = 50) -> Dict[str, Any]:
        keyword = str(keyword or "").strip()
        try:
            limit = min(100, max(1, int(limit)))
        except (TypeError, ValueError):
            limit = 50
        if not keyword or len(keyword) > 128:
            return {"available": True, "results": [], "cached": False, "error": "关键词需为 1~128 个字符"}
        if self._admin_disabled:
            # 普通 Token / 已确认无权限：进程内直接停用，不再查询接口
            return {"available": False, "results": [], "cached": False, "error": "当前 Token 无搜索权限（仅管理员可用）"}
        config = _load_config()
        if config is None:
            return {"available": False, "results": [], "cached": False, "error": "秒传池未配置（SHA1_POOL_API_*）"}

        now = time.monotonic()
        self._prune_cache(now)
        cache_key = (keyword.lower(), limit)
        cached = self._cache.get(cache_key)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            return {"available": True, "results": cached[1], "cached": True, "error": ""}

        try:
            verify = _build_ssl_context(config["ca"]) if config["ca"] else True
            token = get_pool_token_override() or config["token"]
            async with httpx.AsyncClient(verify=verify, timeout=_SEARCH_TIMEOUT, follow_redirects=False, trust_env=False) as client:
                response = await client.get(
                    f"{config['url']}/search",
                    params={"keyword": keyword, "limit": str(limit)},
                    headers={"Authorization": "Bearer " + token},
                )
        except Exception as error:
            logger.warning("秒传池搜索失败，按临时不可用处理：%s", type(error).__name__)
            return {"available": True, "results": [], "cached": False, "error": "秒传池暂时连不上，稍后再试"}

        if response.status_code == 403:
            self._admin_disabled = True
            logger.warning("秒传池搜索返回 403：当前 Token 非管理员，本进程内停用搜索")
            return {"available": False, "results": [], "cached": False, "error": "当前 Token 无搜索权限（仅管理员可用）"}
        if response.status_code == 429:
            return {"available": True, "results": [], "cached": False, "error": "请求太频繁，请稍后再试"}
        if response.status_code != 200:
            return {"available": True, "results": [], "cached": False, "error": f"秒传池返回 {response.status_code}，稍后再试"}
        try:
            body = response.json()
            results = [
                {"sha1": str(item.get("sha1") or ""), "size": int(item.get("size") or 0), "name": str(item.get("name") or "")}
                for item in (body.get("results") or [])[:limit]
                if isinstance(item, dict)
            ]
        except Exception:
            return {"available": True, "results": [], "cached": False, "error": "秒传池返回数据异常，稍后再试"}
        self._cache[cache_key] = (now, results)
        return {"available": True, "results": results, "cached": False, "error": ""}


async def reuse_via_sha1(
    pan123: Any, dir_id: str, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """把池子里搜到的条目批量 SHA1 秒传进指定 123 文件夹。

    单批 ≤20 条、条间隔 ≥1 秒（守则），逐条返回 {sha1, name, ok, fileId|error}。
    """
    clean: List[Dict[str, Any]] = []
    for item in list(items or [])[:_REUSE_BATCH_MAX]:
        if not isinstance(item, dict):
            continue
        sha1 = _normalize_sha1(item.get("sha1"))
        size = _normalize_size(item.get("size"))
        name = _normalize_name(item.get("name") or "")
        if sha1 is None or size is None or not name:
            continue
        clean.append({"sha1": sha1, "size": size, "name": name})
    if not clean:
        raise ValueError("没有可秒传的有效条目（需要 sha1/size/name 齐全，单批最多 20 条）")

    results: List[Dict[str, Any]] = []
    for index, item in enumerate(clean):
        if index:
            await asyncio.sleep(_REUSE_INTERVAL)
        entry: Dict[str, Any] = {"sha1": item["sha1"], "name": item["name"], "size": item["size"], "ok": False}
        try:
            file_id = await pan123.sha1_reuse(str(dir_id or "0"), item["name"], item["sha1"], item["size"])
            if file_id:
                entry["ok"] = True
                entry["fileId"] = file_id
            else:
                entry["error"] = "暂不可用"
        except Exception as error:
            entry["error"] = f"失败：{type(error).__name__}"
        results.append(entry)
    return results


pool_search_client = PoolSearchClient()
