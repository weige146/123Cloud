from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx


BING_ARCHIVE_URL = "https://www.bing.com/HPImageArchive.aspx"
BING_BASE_URL = "https://www.bing.com"
ALLOWED_BING_HOSTS = {"www.bing.com", "cn.bing.com"}
START_DATE_PATTERN = re.compile(r"^\d{8}$")


class WallpaperUpstreamError(RuntimeError):
    pass


def _allowlisted_url(value: object, *, image: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    absolute = urljoin(BING_BASE_URL, raw)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_BING_HOSTS:
        return ""
    if image and not parsed.path.startswith("/th"):
        return ""
    return absolute


def normalize_bing_image(image: object) -> Optional[Dict[str, str]]:
    if not isinstance(image, dict):
        return None

    url = _allowlisted_url(image.get("url"), image=True)
    if not url:
        return None

    copyright_link = _allowlisted_url(image.get("copyrightlink"))
    start_date = str(image.get("startdate") or "").strip()
    if not START_DATE_PATTERN.fullmatch(start_date):
        start_date = ""

    image_id = str(image.get("hsh") or "").strip()
    if not image_id:
        image_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]

    return {
        "id": image_id,
        "url": url,
        "title": str(image.get("title") or "").strip(),
        "copyright": str(image.get("copyright") or "").strip(),
        "copyrightLink": copyright_link,
        "startDate": start_date,
    }


def normalize_bing_payload(payload: object, *, limit: int = 8) -> List[Dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        return []

    result: List[Dict[str, str]] = []
    seen: set[str] = set()
    for raw_image in payload["images"]:
        image = normalize_bing_image(raw_image)
        if not image or image["id"] in seen:
            continue
        seen.add(image["id"])
        result.append(image)
        if len(result) >= max(1, min(limit, 8)):
            break
    return result


async def fetch_bing_wallpapers(timeout_seconds: float = 6.0) -> List[Dict[str, str]]:
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(
            BING_ARCHIVE_URL,
            params={"format": "js", "idx": 0, "n": 8, "mkt": "zh-CN"},
            headers={"Accept": "application/json", "User-Agent": "123Cloud-Wallpaper/1.0"},
        )
        response.raise_for_status()
        images = normalize_bing_payload(response.json())
        if not images:
            raise WallpaperUpstreamError("Bing returned no allowlisted wallpaper images")
        return images


class BingWallpaperService:
    def __init__(
        self,
        *,
        ttl_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
        fetcher: Optional[Callable[[], Awaitable[List[Dict[str, str]]]]] = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.fetcher = fetcher or fetch_bing_wallpapers
        self._cache: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _is_fresh(self) -> bool:
        return bool(self._cache) and self.clock() - self._cached_at < self.ttl_seconds

    async def get(self) -> Dict[str, Any]:
        if self._is_fresh():
            return dict(self._cache or {})

        async with self._get_lock():
            if self._is_fresh():
                return dict(self._cache or {})

            try:
                items = await self.fetcher()
            except WallpaperUpstreamError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise WallpaperUpstreamError("Bing wallpaper request failed") from exc

            if not items:
                raise WallpaperUpstreamError("Bing returned no wallpaper images")

            fetched_at = datetime.now(timezone.utc)
            response = {
                "items": items[:8],
                "fetchedAt": fetched_at.isoformat(),
                "expiresAt": (fetched_at + timedelta(seconds=self.ttl_seconds)).isoformat(),
            }
            self._cache = response
            self._cached_at = self.clock()
            return dict(response)
