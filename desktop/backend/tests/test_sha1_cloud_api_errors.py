"""共享SHA1 API 客户端：错误大白话呈现 + 直连失败走系统代理兜底（不打真实接口）。

背景：日志此前只打异常类名（ConnectTimeout / HTTPStatusError），用户分不清
"网络到不了服务器"和"服务端拒绝"，证书修好了也被误认为没生效。

2026-09-19 追加：海外服务器经隧道访问时 TLS 握手就要 1~4 秒，"每个查询新建
连接 + 5 秒一刀切超时 + 搬运 5 并发"会把请求拖到超时并误触熔断（实测单发 2~3
秒能过、5 路并发就超时）。这里钉死四件事：连接按代理地址缓存复用、超时拆分
（连接 5s / 读 10s）、API 并发闸 3 路、API 客户端熔断放宽为 5 次/5 分钟。
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from unittest.mock import patch

import httpx

from app.sha1_cloud import Sha1ApiClient


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://pool.example/lookup")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


def test_http_status_error_brief_shows_code_and_meaning():
    brief = Sha1ApiClient._error_brief_with_detail(_status_error(429))
    assert "HTTP 429" in brief and "限流" in brief
    brief = Sha1ApiClient._error_brief_with_detail(_status_error(401))
    assert "401" in brief and "令牌" in brief
    brief = Sha1ApiClient._error_brief_with_detail(_status_error(403))
    assert "403" in brief and "权限" in brief


def test_connect_timeout_brief_says_network_not_cert():
    brief = Sha1ApiClient._error_brief_with_detail(httpx.ConnectTimeout("timed out"))
    assert "连接不上服务器" in brief
    assert "证书" not in brief


def test_connect_failure_retries_once_via_system_proxy():
    calls = []

    def fake_send(self, method, url, payload, proxy):
        calls.append(proxy)
        if proxy is None:
            raise httpx.ConnectTimeout("timed out")
        return {"etag": None}

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.object(Sha1ApiClient, "_system_https_proxy", lambda self: "http://127.0.0.1:7890"), \
            patch.dict(os.environ, env):
        result = client._request("GET", {"sha1": "a" * 40, "size": 1})

    assert result == {"etag": None}
    assert calls == [None, "http://127.0.0.1:7890"]
    assert client._failure_count == 0


def test_connect_failure_without_proxy_is_recorded_once():
    calls = []

    def fake_send(self, method, url, payload, proxy):
        calls.append(proxy)
        raise httpx.ConnectTimeout("timed out")

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.object(Sha1ApiClient, "_system_https_proxy", lambda self: None), \
            patch.dict(os.environ, env):
        result = client._request("GET", {"sha1": "a" * 40, "size": 1})

    assert result is None
    assert calls == [None]
    assert client._failure_count == 1


def test_http_rejection_does_not_retry_via_proxy():
    """401/403/429 这类服务端拒绝：直连拿到响应就走原路，不浪费时间试代理。"""
    calls = []

    def fake_send(self, method, url, payload, proxy):
        calls.append(proxy)
        raise _status_error(429)

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.object(Sha1ApiClient, "_system_https_proxy", lambda self: "http://127.0.0.1:7890"), \
            patch.dict(os.environ, env):
        result = client._request("GET", {"sha1": "a" * 40, "size": 1})

    assert result is None
    assert calls == [None]
    assert client._failure_count == 1


def test_http_client_cached_per_proxy_with_split_timeouts():
    """长连接按代理地址缓存复用：直连/代理各一个客户端，超时拆分连接 5s、读 10s。"""
    client = Sha1ApiClient()
    direct = client._http_client(None)
    assert client._http_client(None) is direct
    proxied = client._http_client("http://127.0.0.1:7890")
    assert proxied is not direct
    assert client._http_client("http://127.0.0.1:7890") is proxied
    assert direct.timeout.connect == 5.0
    assert direct.timeout.read == 10.0
    assert proxied.timeout.connect == 5.0
    assert proxied.timeout.read == 10.0


def test_api_gate_limits_concurrent_requests():
    """搬运 5 并发打过来，实际同时压到服务器的请求不得超过 3 个。"""
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_send(self, method, url, payload, proxy):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return {"etag": None}

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.dict(os.environ, env):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(
                lambda _: client._request("GET", {"sha1": "a" * 40, "size": 1}), range(5)))

    assert all(result == {"etag": None} for result in results)
    assert state["max"] <= 3


def test_api_client_breaker_relaxed_to_five_failures():
    """慢而不死的服务器：连败 4 次不熔断，第 5 次才熔断且周期为 5 分钟。"""
    calls = []

    def fake_send(self, method, url, payload, proxy):
        calls.append(proxy)
        raise httpx.ConnectTimeout("timed out")

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.object(Sha1ApiClient, "_system_https_proxy", lambda self: None), \
            patch.dict(os.environ, env):
        for _ in range(4):
            assert client._request("GET", {"sha1": "a" * 40, "size": 1}) is None
        assert not client._breaker_open()
        assert client._request("GET", {"sha1": "a" * 40, "size": 1}) is None

    assert len(calls) == 5
    assert client._breaker_open()
    remaining = client._open_until - time.monotonic()
    assert 200 < remaining <= 300


def test_breaker_params_class_scoped():
    """API 客户端熔断放宽（5 次/5 分钟），数据库直连客户端保持原判（3 次/10 分钟）。"""
    from app import sha1_cloud
    assert Sha1ApiClient.breaker_threshold == 5
    assert Sha1ApiClient.breaker_open_seconds == 300
    assert sha1_cloud.Sha1CloudClient.breaker_threshold == sha1_cloud._BREAKER_THRESHOLD == 3
    assert sha1_cloud.Sha1CloudClient.breaker_open_seconds == sha1_cloud._BREAKER_OPEN_SECONDS == 600


def test_breaker_stays_open_despite_late_success():
    """熔断打开期间的迟到成功不重开熔断：429 风暴里偶发放行一次不代表服务恢复。

    回归 2026-09-22 日志：旧逻辑 _record_success 无条件清掉 _open_until，
    一个偶发成功把熔断整个重开，下一波请求马上又撞限流（日志里 429 中间
    夹着"已恢复使用"即此抖动）。
    """
    client = Sha1ApiClient()
    for _ in range(client.breaker_threshold):
        client._record_failure()
    assert client._breaker_open()
    client._record_success()
    assert client._breaker_open()
    client._record_success(force=True)  # 换 Token 等显式操作才允许强制清掉
    assert not client._breaker_open()


def test_half_open_probe_failure_resets_flag_and_late_success_stays_open():
    """半开探针失败重新熔断后必须退出半开状态：迟到的成功同样不得重开熔断。"""
    client = Sha1ApiClient()
    client._half_open = True
    client._record_failure()
    assert client._breaker_open()
    assert not client._half_open
    client._record_success()
    assert client._breaker_open()


def test_request_rechecks_breaker_after_waiting_on_gate():
    """并发闸前排队的请求：等闸期间熔断触发后，拿到闸也不得再发请求。

    回归 2026-09-22 日志：熔断检查在并发闸之前，搬运 5 并发时排在闸外的
    请求已过检查，熔断触发后照样出网继续撞 429（熔断后日志仍在刷 429）。
    """
    calls = []

    def fake_send(self, method, url, payload, proxy):
        calls.append(proxy)
        return {"etag": None}

    checks = iter([False, True])  # 进门第一次检查放行，拿到闸后的复查触发熔断
    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.object(Sha1ApiClient, "_breaker_open", lambda self: next(checks, True)), \
            patch.dict(os.environ, env):
        assert client._request("GET", {"sha1": "a" * 40, "size": 1}) is None

    assert calls == []


def test_breaker_stops_request_storm_under_concurrency():
    """并发风暴端到端：熔断触发后其余请求全部短路，不再往服务器打。"""
    calls = []
    lock = threading.Lock()

    def fake_send(self, method, url, payload, proxy):
        with lock:
            calls.append(1)
        raise _status_error(429)

    client = Sha1ApiClient()
    env = {"SHA1_POOL_API_URL": "https://195.0.0.1:9443", "SHA1_POOL_API_TOKEN": "t"}
    with patch.object(Sha1ApiClient, "_send", fake_send), \
            patch.dict(os.environ, env):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(
                lambda _: client._request("GET", {"sha1": "a" * 40, "size": 1}), range(40)))

    assert client._breaker_open()
    assert len(calls) < 20  # 只有熔断前赶上闸的请求出过网，绝不是 40 个全打
    before = len(calls)
    assert client._request("GET", {"sha1": "a" * 40, "size": 1}) is None
    assert len(calls) == before
