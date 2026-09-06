"""123 网盘 AList 式代理直链 + 推 115 离线。

参考 AList 的 /d/{path} 代理思路：后端持 123 登录态，把用户在 123 网盘里
指定的「一个文件夹」当成虚拟目录列出，为每个文件生成稳定公网直链
`/dpan/123/{fileId}`。115 离线去拉我们这个端点时，后端即时用 123 的
download_info 取底层直链并流式转发（支持 Range / 断点），从而规避
「123 直链有时效、易被第三方拒收」的问题。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

import httpx
from fastapi.responses import Response, StreamingResponse

from .pan115 import (
    Pan115Error,
    create_helper_client,
    ensure_helper_enabled,
    offline_submit_chunks,
    summarize_helper_results,
)

# fileId -> (url, fetched_at)，用于代理取流时短时间内复用 123 直链，
# 避免 115 每个分片请求都去触发一次 123 OpenAPI 取链（有限流）。
_PAN123_LINK_CACHE_TTL = 60.0
_pan123_link_cache: Dict[int, Dict[str, Any]] = {}


async def pan123_source_files(pan_client: Any, source_dir_id: int) -> List[Dict[str, Any]]:
    """递归列出用户指定的 123 源目录（AList 式虚拟目录），返回 {fileId,name,type,size,rel}。"""
    seen: set[int] = set()
    records: List[Dict[str, Any]] = []

    async def walk(parent_file_id: int, ancestors: List[str]) -> None:
        if parent_file_id in seen:
            return
        seen.add(parent_file_id)
        try:
            items = await pan_client.list_files(str(parent_file_id))
        except Exception as error:
            raise Pan115Error(f"123 列目录失败：{error}") from error
        for item in items:
            file_id = int(item.get("fileId") or 0)
            if not file_id:
                continue
            name = str(item.get("name") or "").strip()
            is_dir = int(item.get("type") or 0) == 1
            if is_dir:
                await walk(file_id, [*ancestors, name])
                continue
            rel = "/".join([*ancestors, name]) if ancestors else name
            records.append(
                {
                    "fileId": file_id,
                    "name": name,
                    "type": 0,
                    "size": int(item.get("size") or 0),
                    "rel": rel,
                }
            )

    await walk(int(source_dir_id or 0), [])
    return records


def build_pan123_link(public_base_url: str, file_id: int) -> str:
    return f"{str(public_base_url or '').rstrip('/')}/dpan/123/{int(file_id)}"


def resolve_public_base_url(helper_config: Dict[str, Any], request: Any) -> str:
    configured = str(helper_config.get("publicBaseUrl") or "").strip().rstrip("/")
    if configured:
        return configured
    netloc = getattr(getattr(request, "url", None), "netloc", "") or ""
    scheme = getattr(getattr(request, "url", None), "scheme", "http") or "http"
    if netloc:
        return f"{scheme}://{netloc}"
    raise Pan115Error("无法确定公网基址；请配置 publicBaseUrl")


def _source_dir_id(helper_config: Dict[str, Any]) -> int:
    return int(helper_config.get("pan123SourceDirId") or 0)


def build_index(files: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {int(item["fileId"]): item for item in files}


async def browse_pan123_dir(pan_client: Any, parent_id: int) -> Dict[str, Any]:
    """列出 123 网盘某目录的直接子项（目录在前），供客户端目录选择器逐级浏览。"""
    try:
        items = await pan_client.list_files(str(int(parent_id or 0)))
    except Exception as error:
        raise Pan115Error(f"123 列目录失败：{error}") from error
    directories: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []
    for item in items:
        file_id = int(item.get("fileId") or 0)
        if not file_id:
            continue
        name = str(item.get("name") or "").strip()
        record = {"fileId": file_id, "name": name, "size": int(item.get("size") or 0)}
        if int(item.get("type") or 0) == 1:
            directories.append(record)
        else:
            files.append(record)
    directories.sort(key=lambda item: str(item["name"]).lower())
    files.sort(key=lambda item: str(item["name"]).lower())
    return {"ok": True, "parentId": int(parent_id or 0), "directories": directories, "files": files}


async def list_pan123_links(pan_client: Any, helper_config: Dict[str, Any], request: Any) -> Dict[str, Any]:
    source_dir_id = _source_dir_id(helper_config)
    base_url = resolve_public_base_url(helper_config, request)
    files = [
        {**item, "url": build_pan123_link(base_url, item["fileId"])}
        for item in await pan123_source_files(pan_client, source_dir_id)
    ]
    return {"ok": True, "sourceDirId": source_dir_id, "baseUrl": base_url, "files": files}


async def submit_pan123_offline(pan_client: Any, helper_config: Dict[str, Any], keys: List[str], request: Any) -> Dict[str, Any]:
    ensure_helper_enabled(helper_config)
    base_url = resolve_public_base_url(helper_config, request)
    index = build_index(await pan123_source_files(pan_client, _source_dir_id(helper_config)))
    results: List[Dict[str, Any]] = []
    url_to_file: Dict[str, Dict[str, Any]] = {}
    pending: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for key in keys:
        try:
            file_id = int(str(key or "").strip())
        except (TypeError, ValueError):
            results.append({"ok": False, "type": "pan123", "label": str(key), "link": "", "message": "非法文件 ID"})
            continue
        if file_id in seen:
            continue
        seen.add(file_id)
        record = index.get(file_id)
        if record is None:
            results.append({"ok": False, "type": "pan123", "label": f"#{file_id}", "link": "", "message": "文件不在源目录"})
            continue
        url = build_pan123_link(base_url, file_id)
        url_to_file[url] = record
        pending.append({"fileId": file_id, "url": url, "rel": record["rel"]})
    if not pending:
        if results:
            return summarize_helper_results(results)
        raise Pan115Error("没有可提交离线的文件")
    client = create_helper_client(helper_config)
    target_dir_id = str(helper_config.get("offlineTargetDirId") or "0")
    for batch in offline_submit_chunks([item["url"] for item in pending]):
        try:
            message = await client.add_offline_urls(batch, target_dir_id)
            for url in batch:
                record = url_to_file.get(url, {})
                results.append({"ok": True, "type": "pan123", "label": record.get("rel") or url, "link": url, "message": message})
        except Exception as error:
            for url in batch:
                record = url_to_file.get(url, {})
                results.append({"ok": False, "type": "pan123", "label": record.get("rel") or url, "link": url, "message": str(error)})
    return summarize_helper_results(results)


async def _cached_download_url(pan_client: Any, file_id: int) -> str:
    cached = _pan123_link_cache.get(file_id)
    if cached and time.time() - cached.get("fetchedAt", 0) < _PAN123_LINK_CACHE_TTL:
        return str(cached.get("url") or "")
    url = await pan_client.download_info(file_id)
    _pan123_link_cache[file_id] = {"url": url, "fetchedAt": time.time()}
    return url


async def proxy_pan123_download(pan_client: Any, file_id: int, request: Any) -> Response:
    """AList /d/ 式代理：即时取 123 直链，把文件流透传给请求方（支持 Range）。"""
    try:
        url = await _cached_download_url(pan_client, file_id)
    except Exception as error:
        raise Pan115Error(f"123 取直链失败：{error}") from error
    headers = {"user-agent": "Mozilla/5.0", "accept": "*/*"}
    client_range = request.headers.get("range")
    if client_range:
        headers["range"] = client_range
    timeout = httpx.Timeout(300.0, connect=30.0)
    upstream = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
    response = await upstream.get(url, headers=headers)
    pass_headers: Dict[str, str] = {}
    for name in ("content-length", "content-range", "accept-ranges", "content-type", "etag"):
        if response.headers.get(name):
            pass_headers[name] = str(response.headers.get(name))

    async def stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream(),
        status_code=response.status_code,
        headers=pass_headers,
        media_type=response.headers.get("content-type", "application/octet-stream"),
    )