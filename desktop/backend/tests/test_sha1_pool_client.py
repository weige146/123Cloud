"""sha1_pool 搜索客户端守则测试（不打真实接口）。"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import sha1_pool


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class FakeHttpClient:
    """替身 httpx.Client：记录调用次数与参数，按脚本返回响应/异常。"""

    instances: List["FakeHttpClient"] = []

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls = 0
        self.last_kwargs: Any = None
        FakeHttpClient.instances.append(self)

    async def __aenter__(self) -> "FakeHttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, params: Any = None, headers: Any = None):
        self.calls += 1
        self.last_kwargs = {"url": url, "params": params, "headers": headers}
        action = self.script.pop(0) if self.script else FakeResponse(200, {"results": []})
        if isinstance(action, Exception):
            raise action
        return action


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch):
    def install(script: List[Any]) -> FakeHttpClient:
        http = FakeHttpClient(script)
        monkeypatch.setattr(sha1_pool.httpx, "AsyncClient", lambda **kwargs: http)
        return http

    return install


@pytest.fixture(autouse=True)
def pool_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHA1_POOL_API_URL", "https://pool.example")
    monkeypatch.setenv("SHA1_POOL_API_TOKEN", "t" * 32)
    monkeypatch.delenv("SHA1_POOL_API_CA", raising=False)
    sha1_pool.pool_search_client.reset_for_test()
    yield
    sha1_pool.pool_search_client.reset_for_test()


def test_missing_config_disables(monkeypatch: pytest.MonkeyPatch, fake_http) -> None:
    monkeypatch.delenv("SHA1_POOL_API_TOKEN", raising=False)
    http = fake_http([])
    data = asyncio.run(sha1_pool.pool_search_client.search("matrix"))
    assert data["available"] is False
    assert http.calls == 0


def test_keyword_validation_short_circuits(fake_http) -> None:
    http = fake_http([])
    for keyword in ("", "   ", "x" * 129):
        data = asyncio.run(sha1_pool.pool_search_client.search(keyword))
        assert data["available"] is True
        assert data["results"] == []
        assert data["error"]
    assert http.calls == 0


def test_search_success_and_cache(fake_http) -> None:
    http = fake_http([
        FakeResponse(200, {"results": [{"sha1": "a" * 40, "size": 123, "name": "movie.mkv"}]}),
    ])
    client = sha1_pool.pool_search_client
    first = asyncio.run(client.search("Matrix", 10))
    assert first["available"] is True and first["results"][0]["name"] == "movie.mkv"
    # 命中缓存：同一关键词不再发请求
    second = asyncio.run(client.search("matrix", 10))
    assert second["cached"] is True and second["results"][0]["name"] == "movie.mkv"
    assert http.calls == 1
    assert http.last_kwargs["headers"]["Authorization"].startswith("Bearer ")
    assert http.last_kwargs["params"] == {"keyword": "Matrix", "limit": "10"}


def test_search_403_disables_without_more_requests(fake_http) -> None:
    http = fake_http([FakeResponse(403, {"error": "forbidden"})])
    client = sha1_pool.pool_search_client
    first = asyncio.run(client.search("kw"))
    assert first["available"] is False
    second = asyncio.run(client.search("another"))
    assert second["available"] is False
    assert http.calls == 1  # 403 之后进程内停用，不再发请求


def test_search_429_not_cached_and_retryable(fake_http) -> None:
    http = fake_http([
        FakeResponse(429, {"error": "rate_limited"}),
        FakeResponse(200, {"results": []}),
    ])
    client = sha1_pool.pool_search_client
    first = asyncio.run(client.search("kw"))
    assert first["available"] is True and "频繁" in first["error"]
    second = asyncio.run(client.search("kw"))
    assert second["cached"] is False and second["error"] == ""
    assert http.calls == 2  # 429 不缓存，允许稍后重试


def test_search_timeout_reported_as_unavailable(fake_http) -> None:
    http = fake_http([httpx_connect_error()])
    data = asyncio.run(sha1_pool.pool_search_client.search("kw"))
    assert data["available"] is True
    assert "连不上" in data["error"]


def test_search_uses_real_async_client_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：search 必须走 AsyncClient。曾误用同步 httpx.Client 导致 `__aenter__` AttributeError。"""
    import httpx

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"results": [{"sha1": "a" * 40, "size": 1, "name": "x.mkv"}], "count": 1})

    real_factory = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("verify", None)
        return real_factory(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sha1_pool.httpx, "AsyncClient", factory)
    data = asyncio.run(sha1_pool.pool_search_client.search("Matrix", 10))
    assert data["available"] is True and data["results"][0]["name"] == "x.mkv"
    assert captured["url"].startswith("https://pool.example/search?keyword=Matrix")
    assert captured["auth"].startswith("Bearer ")


def httpx_connect_error() -> Exception:
    import httpx

    return httpx.ConnectError("boom")


def test_reuse_validates_items_and_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sha1_pool, "_REUSE_INTERVAL", 0)
    pan123 = MagicMock()
    pan123.sha1_reuse = AsyncMock(side_effect=[777, None])
    items = [
        {"sha1": "a" * 40, "size": 10, "name": "ok.mkv"},
        {"sha1": "bad", "size": 10, "name": "跳过.mkv"},  # 非法 sha1
        {"sha1": "b" * 40, "size": -1, "name": "跳过2.mkv"},  # 非法 size
        {"sha1": "c" * 40, "size": 30, "name": "miss.mkv"},
    ]
    results = asyncio.run(sha1_pool.reuse_via_sha1(pan123, "9", items))
    assert [r["ok"] for r in results] == [True, False]
    assert results[0]["fileId"] == 777
    assert results[1]["error"] == "暂不可用"
    assert pan123.sha1_reuse.await_count == 2
    assert pan123.sha1_reuse.await_args_list[0].args == ("9", "ok.mkv", "a" * 40, 10)
    assert pan123.sha1_reuse.await_args_list[1].args == ("9", "miss.mkv", "c" * 40, 30)


def test_reuse_caps_batch_at_20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sha1_pool, "_REUSE_INTERVAL", 0)
    pan123 = MagicMock()
    pan123.sha1_reuse = AsyncMock(return_value=1)
    items = [{"sha1": f"{index:040x}", "size": 1, "name": f"f{index}.mkv"} for index in range(30)]
    results = asyncio.run(sha1_pool.reuse_via_sha1(pan123, "0", items))
    assert len(results) == 20
    assert pan123.sha1_reuse.await_count == 20


def test_reuse_without_valid_items_raises() -> None:
    pan123 = MagicMock()
    with pytest.raises(ValueError):
        asyncio.run(sha1_pool.reuse_via_sha1(pan123, "0", [{"sha1": "nope", "size": 1, "name": "x.mkv"}]))


def test_search_uses_token_override(fake_http, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页切换的管理员 Token 要覆盖默认分发 Token，搜索请求带上它。"""
    http = fake_http([])
    from app import sha1_cloud

    sha1_cloud.set_pool_token_override("admin" * 12)
    try:
        data = asyncio.run(sha1_pool.pool_search_client.search("kw"))
    finally:
        sha1_cloud.set_pool_token_override(None)
    assert data["available"] is True
    assert http.last_kwargs["headers"]["Authorization"] == "Bearer " + "admin" * 12


def test_reset_state_allows_reprobe_after_403(fake_http) -> None:
    """设置页切换 Token 后要清掉 403 停用标记，让新 Token 重新探测。"""
    http = fake_http([FakeResponse(403, {"error": "forbidden"}), FakeResponse(200, {"results": []})])
    client = sha1_pool.pool_search_client
    first = asyncio.run(client.search("kw"))
    assert first["available"] is False
    client.reset_state()
    second = asyncio.run(client.search("kw"))
    assert second["available"] is True
    assert http.calls == 2
