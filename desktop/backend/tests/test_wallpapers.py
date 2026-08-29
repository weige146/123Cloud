from __future__ import annotations

import asyncio

import httpx
import pytest

from app.wallpapers import BingWallpaperService, WallpaperUpstreamError, normalize_bing_image, normalize_bing_payload


def test_normalize_bing_image_allows_expected_https_urls() -> None:
    result = normalize_bing_image(
        {
            "hsh": "image-hash",
            "url": "/th?id=OHR.Sample_ZH-CN1234567890_1920x1080.jpg&rf=LaDigue_1920x1080.jpg",
            "title": "示例壁纸",
            "copyright": "示例版权",
            "copyrightlink": "/search?q=sample",
            "startdate": "20260815",
        }
    )

    assert result == {
        "id": "image-hash",
        "url": "https://www.bing.com/th?id=OHR.Sample_ZH-CN1234567890_1920x1080.jpg&rf=LaDigue_1920x1080.jpg",
        "title": "示例壁纸",
        "copyright": "示例版权",
        "copyrightLink": "https://www.bing.com/search?q=sample",
        "startDate": "20260815",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bing.com/th?id=unsafe",
        "https://evil.example/th?id=unsafe",
        "https://www.bing.com/account/settings",
        "javascript:alert(1)",
    ],
)
def test_normalize_bing_image_rejects_untrusted_image_urls(url: str) -> None:
    assert normalize_bing_image({"url": url}) is None


def test_normalize_payload_filters_duplicates_and_caps_results() -> None:
    images = [{"hsh": f"id-{index}", "url": f"/th?id=image-{index}"} for index in range(10)]
    images.insert(2, {"hsh": "id-1", "url": "/th?id=duplicate"})
    images.insert(3, {"hsh": "bad", "url": "https://example.com/image.jpg"})

    result = normalize_bing_payload({"images": images})

    assert len(result) == 8
    assert len({item["id"] for item in result}) == 8


def test_service_uses_fresh_cache_and_refreshes_after_expiry() -> None:
    now = [100.0]
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        return [{"id": f"image-{calls}", "url": f"https://www.bing.com/th?id={calls}"}]

    service = BingWallpaperService(ttl_seconds=10, clock=lambda: now[0], fetcher=fetcher)

    first = asyncio.run(service.get())
    second = asyncio.run(service.get())
    now[0] = 111.0
    third = asyncio.run(service.get())

    assert first["items"] == second["items"]
    assert first["items"] != third["items"]
    assert calls == 2


def test_service_does_not_return_stale_cache_when_refresh_fails() -> None:
    now = [100.0]
    should_fail = False

    async def fetcher():
        if should_fail:
            raise httpx.ReadTimeout("timeout")
        return [{"id": "image-1", "url": "https://www.bing.com/th?id=1"}]

    service = BingWallpaperService(ttl_seconds=10, clock=lambda: now[0], fetcher=fetcher)
    asyncio.run(service.get())
    now[0] = 111.0
    should_fail = True

    with pytest.raises(WallpaperUpstreamError):
        asyncio.run(service.get())


def test_service_coalesces_concurrent_requests() -> None:
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return [{"id": "image-1", "url": "https://www.bing.com/th?id=1"}]

    async def run_requests():
        service = BingWallpaperService(fetcher=fetcher)
        return await asyncio.gather(service.get(), service.get(), service.get())

    results = asyncio.run(run_requests())
    assert calls == 1
    assert all(result["items"][0]["id"] == "image-1" for result in results)
