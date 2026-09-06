"""115 点对点离线直链服务：根目录文件 → AList 式直链 → 提交 115 离线。

面向「软件部署在公网环境」的场景：把放在指定根目录内的文件暴露成可被
115 服务器访问的 HTTP 直链，再调用 115 离线下载让其直接拉取（点对点离线）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.responses import FileResponse

from .pan115 import (
    Pan115Error,
    create_helper_client,
    ensure_helper_enabled,
    offline_submit_chunks,
    summarize_helper_results,
)

APP_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
HTTP_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)


def get_dl_root(helper_config: Dict[str, Any]) -> Path:
    configured = str(helper_config.get("directLinkRoot") or "").strip()
    root = Path(configured) if configured else APP_DATA_DIR / "directlink"
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize_rel(rel: str) -> str:
    value = str(rel or "").strip().replace("\\", "/")
    while value.startswith("/"):
        value = value[1:]
    return value


def resolve_dl_path(helper_config: Dict[str, Any], rel: str) -> Path:
    root = get_dl_root(helper_config).resolve()
    path = root.joinpath(*normalize_rel(rel).split("/")).resolve()
    if not path.is_relative_to(root):
        raise Pan115Error(f"非法路径：{rel}")
    return path


def list_directlink_files(helper_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = get_dl_root(helper_config)
    files: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files.append(
            {
                "name": path.name,
                "rel": rel,
                "size": path.stat().st_size,
                "mtime": int(path.stat().st_mtime),
            }
        )
    return files


def build_direct_link(public_base_url: str, rel: str) -> str:
    base = str(public_base_url or "").rstrip("/")
    return f"{base}/dlink/{normalize_rel(rel)}"


def resolve_public_base_url(helper_config: Dict[str, Any], request: Any) -> str:
    configured = str(helper_config.get("publicBaseUrl") or "").strip().rstrip("/")
    if configured:
        return configured
    netloc = getattr(getattr(request, "url", None), "netloc", "") or ""
    scheme = getattr(getattr(request, "url", None), "scheme", "http") or "http"
    if netloc:
        return f"{scheme}://{netloc}"
    raise Pan115Error("无法确定公网基址；请配置 publicBaseUrl")


def read_directlink_status(helper_config: Dict[str, Any], request: Any) -> Dict[str, Any]:
    base_url = resolve_public_base_url(helper_config, request)
    files = [
        {**item, "url": build_direct_link(base_url, item["rel"])}
        for item in list_directlink_files(helper_config)
    ]
    return {
        "ok": True,
        "root": str(get_dl_root(helper_config)),
        "baseUrl": base_url,
        "files": files,
    }


async def submit_directlink_offline(helper_config: Dict[str, Any], keys: List[str], request: Any) -> Dict[str, Any]:
    ensure_helper_enabled(helper_config)
    base_url = resolve_public_base_url(helper_config, request)
    results: List[Dict[str, Any]] = []
    url_to_rel: Dict[str, str] = {}
    pending: List[Dict[str, str]] = []
    seen: set[str] = set()
    for key in keys:
        rel = normalize_rel(key)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        try:
            path = resolve_dl_path(helper_config, rel)
            if not path.is_file():
                raise Pan115Error(f"文件不存在：{rel}")
            url = build_direct_link(base_url, rel)
            url_to_rel[url] = rel
            pending.append({"rel": rel, "url": url})
        except Pan115Error as error:
            results.append({"ok": False, "type": "dlink", "label": rel, "link": "", "message": str(error)})
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
                results.append({"ok": True, "type": "dlink", "label": url_to_rel.get(url, url), "link": url, "message": message})
        except Exception as error:
            for url in batch:
                results.append({"ok": False, "type": "dlink", "label": url_to_rel.get(url, url), "link": url, "message": str(error)})
    return summarize_helper_results(results)


async def submit_arbitrary_urls_offline(helper_config: Dict[str, Any], urls: List[str]) -> Dict[str, Any]:
    ensure_helper_enabled(helper_config)
    cleaned = [str(item or "").strip() for item in urls if str(item or "").strip()]
    results: List[Dict[str, Any]] = []
    valid: List[str] = []
    seen: set[str] = set()
    for url in cleaned:
        if not HTTP_URL_RE.match(url):
            results.append({"ok": False, "type": "url", "label": url, "link": url, "message": "不是 http(s) 直链"})
            continue
        if url not in seen:
            seen.add(url)
            valid.append(url)
    if not valid:
        if results:
            return summarize_helper_results(results)
        raise Pan115Error("没有可提交离线的 http(s) 直链")
    client = create_helper_client(helper_config)
    target_dir_id = str(helper_config.get("offlineTargetDirId") or "0")
    for batch in offline_submit_chunks(valid):
        try:
            message = await client.add_offline_urls(batch, target_dir_id)
            results.extend({"ok": True, "type": "url", "label": url, "link": url, "message": message} for url in batch)
        except Exception as error:
            results.extend({"ok": False, "type": "url", "label": url, "link": url, "message": str(error)} for url in batch)
    return summarize_helper_results(results)


async def serve_directlink_file(helper_config: Dict[str, Any], rel: str) -> FileResponse:
    path = resolve_dl_path(helper_config, rel)
    if not path.is_file():
        raise Pan115Error(f"文件不存在：{rel}")
    return FileResponse(str(path))