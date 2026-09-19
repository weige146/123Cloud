"""共享SHA1 API 客户端：错误大白话呈现 + 直连失败走系统代理兜底（不打真实接口）。

背景：日志此前只打异常类名（ConnectTimeout / HTTPStatusError），用户分不清
"网络到不了服务器"和"服务端拒绝"，证书修好了也被误认为没生效。
"""

from __future__ import annotations

import os
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
