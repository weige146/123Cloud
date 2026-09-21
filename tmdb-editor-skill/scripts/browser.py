#!/usr/bin/env python3
"""TMDB 写操作浏览器通道（Playwright 驱动真实 Chrome，绕 AWS WAF 的同源 fetch）。

TMDB 的资料编辑接口（/remote/、/image）只认登录态 + 同源请求，外部 curl 会被站点的
AWS WAF 拦截。本脚本用 Playwright 打开/附加真实 Chrome，在 themoviedb.org 页面上下文里
执行 fetch，等价于浏览器内手工操作。依赖：playwright（skill venv 自带）+ 系统 Chrome。

子命令：
  check                         检查 playwright / Chrome / 登录态
  login [--profile DIR]         打开可见浏览器人工登录 TMDB（持久 profile，之后免登录）
  run PLAN [--profile DIR] [--cdp URL] [--interval-ms N] [--keep-going] [--log FILE] [--headless]
                                执行写操作计划 JSON，逐条输出结果

计划 JSON 形态（ops 顺序执行）：
{
  "intervalMs": 500,
  "ops": [
    {"op": "upload_config", "pageUrl": "/tv/330017/images/posters"},
    {"op": "fetch", "method": "GET", "url": "/tv/330017/season/1/remote/episodes?translate=false"},
    {"op": "fetch", "method": "POST", "url": "/tv/330017/season/1/remote/episodes",
     "query": {"language": "zh-CN", "translate": "false"},
     "data": {"episode_number": 2, "name": "第2集", "overview": "", "air_date": "", "runtime": 13, "locked_fields": []}},
    {"op": "upload", "url": "/image", "file": "poster.jpg",
     "fields": {"media_id": "<BSON>", "media_type": "TvSeries", "type": "poster", "translate": "false"}}
  ]
}
说明：
- 相对 URL 相对 https://www.themoviedb.org；fetch 的 data 字段按官方约定编码成 `data=<JSON>` 表单。
- upload 的文件字段名固定 upload_files，multipart 由页面内 FormData 自动带 boundary。
- 写操作之间按 intervalMs 限速（官方礼仪 300-800ms）。
- 响应非 JSON（WAF/登录页）时结果打 auth/waf 标记，方便上层停下来处理。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from typing import Any, Dict, List, Optional

BASE = "https://www.themoviedb.org"
DEFAULT_PROFILE = os.path.join(os.path.expanduser("~"), ".zcode", "skills", "tmdb-editor", "chrome-profile")
PROXY_CONFIG = os.path.join(os.path.expanduser("~"), ".zcode", "skills", "tmdb-editor", "proxy.txt")
COOKIES_FILE = os.path.join(os.path.expanduser("~"), ".zcode", "skills", "tmdb-editor", "tmdb-cookies.json")
MUTATING = {"POST", "PUT", "DELETE", "PATCH"}


def resolve_proxy(cli_value: Optional[str]) -> Optional[str]:
    """代理解析顺序：--proxy > 环境变量 TMDB_PROXY > ~/.zcode/skills/tmdb-editor/proxy.txt。

    大陆网络直连 themoviedb.org 会超时，把代理地址（如 http://127.0.0.1:7890）写进
    proxy.txt 即对 login/run/check 全部生效。
    """
    if cli_value:
        return cli_value
    env = os.environ.get("TMDB_PROXY", "").strip()
    if env:
        return env
    try:
        with open(PROXY_CONFIG, encoding="utf-8") as fh:
            text = fh.read().strip()
        return text or None
    except OSError:
        return None


HELPER_STATE = os.path.join(os.path.dirname(PROXY_CONFIG), "helper-browser.json")
HELPER_PORT = 9223


def _cdp_alive(port: int) -> bool:
    import http.client

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        conn.request("GET", "/json/version")
        conn.getresponse().read()
        conn.close()
        return True
    except Exception:
        return False


def helper_port() -> Optional[int]:
    """常驻浏览器（browser.py open 启动）的 CDP 端口；不可达返回 None。"""
    try:
        with open(HELPER_STATE, encoding="utf-8") as fh:
            port = int(json.load(fh).get("port") or 0)
        return port if port and _cdp_alive(port) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _chrome_binary() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return "chrome"


def cmd_open(args) -> int:
    """启动常驻浏览器：登录态与会话像日常浏览器一样持续（对齐参考 skill 的常开做法）。"""
    port = int(args.port or HELPER_PORT)
    os.makedirs(args.profile, exist_ok=True)
    if _cdp_alive(port):
        print(json.dumps({"opened": True, "alreadyRunning": True, "cdp": f"http://127.0.0.1:{port}"}, ensure_ascii=False))
        return 0
    import subprocess

    proc = subprocess.Popen(
        [
            _chrome_binary(),
            f"--user-data-dir={args.profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=zh-CN",
            BASE,
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        if _cdp_alive(port):
            with open(HELPER_STATE, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "pid": proc.pid}, fh)
            print(json.dumps({"opened": True, "cdp": f"http://127.0.0.1:{port}", "pid": proc.pid}, ensure_ascii=False))
            return 0
        time.sleep(0.5)
    print(json.dumps({"opened": False, "hint": "Chrome 启动后 CDP 端口未就绪"}, ensure_ascii=False))
    return 1


def cmd_close(args) -> int:
    port = helper_port()
    if not port:
        print(json.dumps({"closed": True, "note": "常驻浏览器未在运行"}))
        return 0
    import signal

    try:
        with open(HELPER_STATE, encoding="utf-8") as fh:
            pid = int(json.load(fh).get("pid") or 0)
        if pid:
            os.kill(pid, signal.SIGTERM)
        print(json.dumps({"closed": True, "pid": pid}))
    except Exception as err:
        print(json.dumps({"closed": False, "error": str(err)[:120]}, ensure_ascii=False))
        return 1
    return 0

FETCH_JS = """
async (request) => {
  const r = await fetch(request.url, {
    method: request.method,
    credentials: 'same-origin',
    headers: request.headers,
    body: request.body === null ? undefined : request.body
  });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch (err) {}
  return { status: r.status, finalUrl: r.url, isJson: data !== null, data, textSnippet: text.slice(0, 600) };
}
"""

UPLOAD_JS = """
async (request) => {
  const binary = atob(request.base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: request.mime });
  const fd = new FormData();
  fd.append('upload_files', blob, request.filename);
  for (const [key, value] of Object.entries(request.fields)) fd.append(key, value);
  const r = await fetch(request.url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
    body: fd
  });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch (err) {}
  return { status: r.status, finalUrl: r.url, isJson: data !== null, data, textSnippet: text.slice(0, 600) };
}
"""

UPLOAD_CONFIG_JS = """
() => {
  const html = document.documentElement.outerHTML;
  const mediaId = html.match(/media_id:\\s*['"]([a-f0-9]{12,40})['"]/);
  if (!mediaId) return null;
  const scope = html.slice(mediaId.index, mediaId.index + 400);
  const mediaType = scope.match(/media_type:\\s*['"]([^'"]+)['"]/);
  const type = scope.match(/(?:^|[^\\w])type:\\s*['"]([^'"]+)['"]/);
  return {
    media_id: mediaId[1],
    media_type: mediaType ? mediaType[1] : '',
    type: type && /^(poster|backdrop|logo|still|profile)$/.test(type[1]) ? type[1] : 'poster'
  };
}
"""

LOGIN_CHECK_JS = """
async () => {
  // 实测：未登录访问 /settings/api 是同 URL 返回 401 的登录页（不重定向），登录后才是 200
  const r = await fetch('/settings/api', { credentials: 'same-origin' });
  return { loggedIn: r.status === 200, status: r.status, finalUrl: r.url };
}
"""


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误：缺少 playwright。请用 skill 的 .venv/bin/python 运行，或 pip install playwright", file=sys.stderr)
        raise SystemExit(2)
    return sync_playwright


def _pick_page(context, base: str):
    for page in context.pages:
        if base.split("://", 1)[1] in (page.url or ""):
            return page
    page = context.new_page()
    page.goto(base, wait_until="domcontentloaded", timeout=60000)
    return page


def _ensure_same_origin(page, base: str) -> None:
    if not (page.url or "").startswith(base):
        page.goto(base, wait_until="domcontentloaded", timeout=60000)


def _open_context(playwright, args):
    """优先级：--cdp 附加 > 常驻浏览器（open 启动，自动发现）> 每次启闭的持久 profile。"""
    proxy = resolve_proxy(getattr(args, "proxy", None))
    cdp = getattr(args, "cdp", None) or (f"http://127.0.0.1:{helper_port()}" if helper_port() else None)
    if cdp:
        browser = playwright.chromium.connect_over_cdp(cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, context, True
    os.makedirs(args.profile, exist_ok=True)
    launch_kwargs: Dict[str, Any] = {
        "user_data_dir": args.profile,
        "channel": "chrome",
        "headless": bool(getattr(args, "headless", False)),
        "viewport": {"width": 1440, "height": 900},
        "args": ["--lang=zh-CN"],
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    return None, context, False


def classify(result: Dict[str, Any]) -> Optional[str]:
    """把可疑响应归类：auth=掉登录/无权限，waf=WAF 拦截页，failure=TMDB 校验失败。"""
    if result.get("status") in (401, 403):
        return "auth"
    if not result.get("isJson"):
        text = str(result.get("textSnippet") or "")
        if "onetrust" in text or "challenge" in text or "<html" in text.lower():
            return "waf_or_login"
    data = result.get("data")
    if isinstance(data, dict) and data.get("failure"):
        return "tmdb_validation"
    return None


def save_cookies(context) -> int:
    """把 TMDB cookie 存盘：TMDB 的会话 cookie 是会话级（浏览器一关就丢），必须显式持久化。"""
    try:
        cookies = context.cookies(["https://www.themoviedb.org", "https://www.themoviedb.org/"])
        if not cookies:
            cookies = context.cookies()
        with open(COOKIES_FILE, "w", encoding="utf-8") as fh:
            json.dump(cookies, fh, ensure_ascii=False)
        return len(cookies)
    except Exception:
        return 0


def restore_cookies(context) -> int:
    """只补当前上下文里**缺失**的 cookie，绝不覆盖已有同名 cookie——
    覆盖会把 profile 里更新的会话轮换回退成存档里的旧死会话（实测踩坑）。"""
    if not os.path.isfile(COOKIES_FILE):
        return 0
    try:
        with open(COOKIES_FILE, encoding="utf-8") as fh:
            cookies = json.load(fh)
        if not isinstance(cookies, list) or not cookies:
            return 0
        existing = {(c.get("name"), c.get("domain")) for c in context.cookies()}
        missing = [c for c in cookies if (c.get("name"), c.get("domain")) not in existing]
        if missing:
            context.add_cookies(missing)
        return len(missing)
    except Exception:
        pass
    return 0


def run_plan(args) -> int:
    sync_playwright = _import_playwright()
    if args.plan == "-":
        plan = json.load(sys.stdin)
    else:
        with open(args.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
    ops: List[Dict[str, Any]] = plan.get("ops") or []
    if not ops:
        print("错误：计划里没有 ops", file=sys.stderr)
        return 2
    interval_ms = int(args.interval_ms if args.interval_ms is not None else plan.get("intervalMs", 500))
    keep_going = bool(args.keep_going or plan.get("keepGoing"))

    log_fh = open(args.log, "a", encoding="utf-8") if args.log else None
    results: List[Dict[str, Any]] = []
    last_mutating_at = 0.0

    with sync_playwright() as pw:
        browser, context, attached = _open_context(pw, args)
        try:
            restored = restore_cookies(context)
            if restored:
                print(f"[cookies] 已注入 {restored} 条存档 cookie", file=sys.stderr)
            try:
                page = _pick_page(context, BASE)
            except Exception as err:
                message = str(err)
                if "ERR_TIMED_OUT" in message or "ERR_CONNECTION" in message or "ERR_NAME_NOT_RESOLVED" in message:
                    proxy = resolve_proxy(getattr(args, "proxy", None))
                    hint = "确认代理软件已开机并放行 Chrome" if proxy else \
                        "大陆直连 TMDB 会超时：加 --proxy http://127.0.0.1:端口，或把代理地址写进 ~/.zcode/skills/tmdb-editor/proxy.txt（一行）"
                    raise SystemExit(f"错误：连不上 themoviedb.org（当前代理：{proxy or '无'}）。{hint}")
                raise
            for index, op in enumerate(ops):
                result: Dict[str, Any] = {"index": index, "op": op.get("op", "?")}
                try:
                    kind = op.get("op")
                    if kind == "upload_config":
                        url = op.get("pageUrl") or ""
                        target = url if url.startswith("http") else BASE + url
                        page.goto(target, wait_until="domcontentloaded", timeout=60000)
                        config = page.evaluate(UPLOAD_CONFIG_JS)
                        if not config:
                            raise RuntimeError("页面没有解析到上传配置（确认已登录，且是图片上传页）")
                        result.update(ok=True, config=config)
                    elif kind == "navigate":
                        url = op.get("url") or ""
                        target = url if url.startswith("http") else BASE + url
                        page.goto(target, wait_until="domcontentloaded", timeout=60000)
                        result.update(ok=True, url=page.url, title=page.title())
                    elif kind == "hover":
                        page.hover(op.get("selector") or "", timeout=10000)
                        time.sleep(0.6)
                        result.update(ok=True)
                    elif kind == "screenshot":
                        path = op.get("path") or "/tmp/tmdb-editor-screenshot.png"
                        page.screenshot(path=path, full_page=bool(op.get("fullPage")))
                        result.update(ok=True, path=path)
                    elif kind == "click":
                        page.click(op.get("selector") or "", timeout=10000)
                        time.sleep(float(op.get("wait") or 1.0))
                        result.update(ok=True, url=page.url)
                    elif kind == "eval":
                        if not op.get("stay"):
                            _ensure_same_origin(page, BASE)
                        value = page.evaluate(op.get("js") or "() => null")
                        result.update(ok=True, value=value)
                    elif kind == "fetch":
                        _ensure_same_origin(page, BASE)
                        method = str(op.get("method") or "GET").upper()
                        body = None
                        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}
                        if method in MUTATING:
                            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
                            if op.get("data") is not None:
                                body = "data=" + _quote(json.dumps(op["data"], ensure_ascii=False))
                            elif op.get("form"):
                                body = "&".join(f"{k}={_quote(str(v))}" for k, v in op["form"].items())
                        if interval_ms and method in MUTATING and last_mutating_at:
                            gap = time.monotonic() - last_mutating_at
                            if gap * 1000 < interval_ms:
                                time.sleep(interval_ms / 1000 - gap)
                        url = _build_url(op, BASE)
                        payload = {"url": url, "method": method, "headers": headers, "body": body}
                        raw = page.evaluate(FETCH_JS, payload)
                        if method in MUTATING:
                            last_mutating_at = time.monotonic()
                        result.update(ok=bool(raw.get("isJson") and raw.get("status", 0) < 400), status=raw.get("status"), response=raw)
                        flag = classify(raw)
                        if flag:
                            result["flag"] = flag
                            result["ok"] = False
                    elif kind == "upload":
                        _ensure_same_origin(page, BASE)
                        file_path = op.get("file") or ""
                        if not os.path.isfile(file_path):
                            raise RuntimeError(f"文件不存在：{file_path}")
                        with open(file_path, "rb") as fh:
                            blob = fh.read()
                        mime = mimetypes.guess_type(file_path)[0] or ("image/png" if file_path.lower().endswith(".png") else "image/jpeg")
                        url = op.get("url") or "/image"
                        if interval_ms and last_mutating_at:
                            gap = time.monotonic() - last_mutating_at
                            if gap * 1000 < interval_ms:
                                time.sleep(interval_ms / 1000 - gap)
                        raw = page.evaluate(UPLOAD_JS, {
                            "url": url if url.startswith("http") else BASE + url,
                            "base64": base64.b64encode(blob).decode(),
                            "mime": mime,
                            "filename": os.path.basename(file_path),
                            "fields": {str(k): str(v) for k, v in (op.get("fields") or {}).items()},
                        })
                        last_mutating_at = time.monotonic()
                        result.update(ok=bool(raw.get("isJson") and isinstance(raw.get("data"), dict) and raw["data"].get("success") is True),
                                      status=raw.get("status"), response=raw)
                        flag = classify(raw)
                        if flag:
                            result["flag"] = flag
                            result["ok"] = False
                    else:
                        raise RuntimeError(f"未知 op 类型：{kind}（支持 upload_config/navigate/hover/click/screenshot/fetch/upload/eval）")
                except Exception as err:  # 单条失败记录后继续/中止由 keepGoing 决定
                    result.update(ok=False, error=str(err))
                results.append(result)
                if log_fh:
                    log_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                    log_fh.flush()
                status = "ok" if result.get("ok") else "FAIL" + (f"({result.get('flag')})" if result.get("flag") else "")
                print(f"[{index + 1}/{len(ops)}] {status} {result.get('op')} {result.get('error') or ''}", file=sys.stderr)
                if not result.get("ok") and not keep_going:
                    break
        finally:
            if not attached:
                saved = save_cookies(context)
                if saved:
                    print(f"[cookies] 已存档 {saved} 条 cookie", file=sys.stderr)
                context.close()
    failed = [r for r in results if not r.get("ok")]
    print(json.dumps({"total": len(ops), "executed": len(results), "failed": len(failed), "results": results}, ensure_ascii=False, indent=2))
    if log_fh:
        log_fh.close()
    return 1 if failed else 0


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _build_url(op: Dict[str, Any], base: str) -> str:
    from urllib.parse import urlencode

    url = str(op.get("url") or "")
    full = url if url.startswith("http") else base + url
    query = op.get("query") or {}
    if query:
        full += ("&" if "?" in full else "?") + urlencode(query)
    return full


def cmd_login(args) -> int:
    sync_playwright = _import_playwright()
    wait_seconds = int(getattr(args, "wait_seconds", None) or 600)
    with sync_playwright() as pw:
        _browser, context, attached = _open_context(pw, args)
        restore_cookies(context)  # 若有存档会话，可能直接就是登录态
        page = _pick_page(context, BASE)
        page.goto(BASE + "/login", wait_until="domcontentloaded")
        print("浏览器已打开 TMDB 登录页：请在窗口里完成登录（含 Cookie 弹窗确认）。", file=sys.stderr)
        try:
            input("登录完成后回到终端按回车立即校验……")
        except EOFError:
            print(f"无终端输入，自动每 3 秒轮询登录态（最长 {wait_seconds} 秒）……", file=sys.stderr)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                state = page.evaluate(LOGIN_CHECK_JS)
            except Exception:
                # 登录表单提交/页面导航会暂时销毁执行上下文，等一拍再查
                time.sleep(3)
                continue
            if state.get("loggedIn"):
                saved = save_cookies(context)
                print(json.dumps({"loggedIn": True, "profile": args.profile, "cookiesSaved": saved}, ensure_ascii=False))
                if not attached:
                    context.close()
                return 0
            time.sleep(3)
        print(json.dumps({"loggedIn": False, "hint": "登录未生效：检查账号密码/人机验证后重试"}, ensure_ascii=False))
        if not attached:
            context.close()
        return 1


def cmd_check(args) -> int:
    info: Dict[str, Any] = {"playwright": True, "chrome": False, "cdpReachable": False, "proxy": resolve_proxy(args.proxy) or "(无)", "loggedIn": None}
    sync_playwright = _import_playwright()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            browser.close()
            info["chrome"] = True
        except Exception as err:
            info["chrome"] = False
            info["chromeError"] = str(err)[:200]
    if args.cdp:
        try:
            import http.client
            from urllib.parse import urlparse

            parsed = urlparse(args.cdp)
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
            conn.request("GET", "/json/version")
            conn.getresponse().read()
            conn.close()
            info["cdpReachable"] = True
        except Exception as err:
            info["cdpReachable"] = False
            info["cdpError"] = str(err)[:200]
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if not info["chrome"]:
        print("提示：未找到可用的 Chrome。Playwright 的 channel='chrome' 需要系统装有 Google Chrome。", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TMDB 写操作浏览器通道")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="检查环境")
    p.add_argument("--cdp", help="CDP 端点，如 http://127.0.0.1:9222")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--proxy", default=None, help="代理服务器，如 http://127.0.0.1:7890（大陆直连 TMDB 必配）")

    p = sub.add_parser("open", help="启动常驻浏览器（登录态持续，推荐开一次后一直用）")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--port", type=int, default=HELPER_PORT, help="CDP 端口（默认 9223）")
    p.add_argument("--proxy", default=None)

    p = sub.add_parser("close", help="关闭常驻浏览器")

    p = sub.add_parser("login", help="人工登录 TMDB 并持久化 profile")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--cdp")
    p.add_argument("--proxy", default=None)
    p.add_argument("--wait-seconds", type=int, default=600, help="无终端输入时自动轮询登录态的最长秒数")

    p = sub.add_parser("run", help="执行写操作计划")
    p.add_argument("plan", help="计划 JSON 文件路径，或 - 读 stdin")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--cdp", help="附加到已运行的 Chrome CDP 端点（优先于 profile）")
    p.add_argument("--proxy", default=None, help="代理服务器（也可写 proxy.txt 或环境变量 TMDB_PROXY）")
    p.add_argument("--interval-ms", type=int, default=None, help="写请求间隔（默认取计划 intervalMs 或 500）")
    p.add_argument("--keep-going", action="store_true", help="单条失败不中止")
    p.add_argument("--headless", action="store_true", help="无头运行（登录态已持久化时可用；WAF 风险更高）")
    p.add_argument("--log", help="结果 JSONL 追加日志")

    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "open":
        return cmd_open(args)
    if args.command == "close":
        return cmd_close(args)
    if args.command == "login":
        return cmd_login(args)
    return run_plan(args)


if __name__ == "__main__":
    sys.exit(main())
