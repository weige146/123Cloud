"""共享 SHA1 秒传库客户端（可选功能）。

把本地学习表（transfer_hashes）的命中范围扩展到所有客户端用户共享的中心库：
- 115→123 搬运：本地没学过的文件，按 (sha1, size) 反查别人学到的 123 etag；
- 123→115 搬运：本地没学过的文件，按 (etag, size) 反查当初的 115 sha1；
- 经内容验证的搬运成功后（离线落盘、SHA1 秒传），把 (sha1, size, etag) 回写中心库供别人使用。

设计约束（与安全审查结论对齐）：
- 配置只来自环境变量（SHA1DB_*，全部必填，代码/日志不出现任何值），
  未配置、配置不完整或非法时整个模块静默停用；
- 连接强制 TLS 且校验服务器身份（VERIFY_CA+IDENTITY：随包分发的 CA + 证书 IP SAN），
  没有 CA 直接拒绝连接，绝不降级明文；
- 所有网络操作"尽力而为"：连接失败/超时一律记 WARNING 并按"没有命中"处理，
  绝不抛异常打断搬运；连续失败 3 次自动熔断 10 分钟，避免不可达时拖慢任务；
- 只按内容指纹反查 (sha1, size) / (etag, size)；绝不以 (name, size) 反查共享库，
  新增记录携带文件名，但文件名不用于判断内容相同；
- 回写只发生在内容经过验证的搬运成功之后；INSERT IGNORE 首写优先，只需要 INSERT 权限；
- 调用方从共享库拿到的映射视为"未经验证"，不应触发自动删除源文件等不可逆动作。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl as _ssl
import threading
import time
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SHA1_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_ETAG_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_CONNECT_TIMEOUT = 3
_QUERY_TIMEOUT = 5
_BREAKER_THRESHOLD = 3
_BREAKER_OPEN_SECONDS = 600
_MAX_SIZE = 2**63 - 1  # 按 signed BIGINT 上界拒绝（服务端列为 BIGINT UNSIGNED，客户端取更严的一侧）
_REQUIRED_FIELDS = ("SHA1DB_HOST", "SHA1DB_PORT", "SHA1DB_USER",
                    "SHA1DB_PASSWORD", "SHA1DB_NAME", "SHA1DB_SSL_CA")


def _error_brief(error: BaseException) -> str:
    """只暴露异常类型和 MySQL 数字错误码（如 OperationalError(1045)），不带任何参数值。"""
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None
    name = type(error).__name__
    return f"{name}({code})" if code is not None else name


def _load_config() -> Optional[Dict[str, Any]]:
    """读取环境变量配置；全部字段必须存在且合法，否则返回 None（功能停用）。"""
    try:
        missing = [k for k in _REQUIRED_FIELDS if not str(os.environ.get(k) or "").strip()]
        if missing:
            return None
        host = os.environ["SHA1DB_HOST"].strip()
        user = os.environ["SHA1DB_USER"].strip()
        if user.lower() in {"root", "sha1_admin"}:
            raise ValueError("administrator account is not allowed in pool runtime")
        if os.environ["SHA1DB_NAME"].strip() != "sha1db":
            raise ValueError("only sha1db is allowed")
        port = int(os.environ["SHA1DB_PORT"].strip())
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range: {port}")
        ssl_ca = os.environ["SHA1DB_SSL_CA"].strip()
        if not os.path.isfile(ssl_ca):
            raise FileNotFoundError(f"CA file not found: {ssl_ca}")
        return {
            "host": host,
            "port": port,
            "user": os.environ["SHA1DB_USER"].strip(),
            "password": os.environ["SHA1DB_PASSWORD"],
            "database": os.environ["SHA1DB_NAME"].strip(),
            "ssl_ca": ssl_ca,
        }
    except Exception as error:
        logger.warning("共享SHA1库配置非法（SHA1DB_*），功能停用：%s", _error_brief(error))
        return None


def _normalize_sha1(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip().lower()
    return text if _SHA1_PATTERN.match(text) else None


def _normalize_etag(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip().lower()
    return text if _ETAG_PATTERN.match(text) else None


def _normalize_size(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, Decimal):
        # 拒绝隐式截断：非整数的 Decimal 直接视为非法
        if not value.is_finite() or value != value.to_integral_value():
            return None
        value = int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return None
    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # 超出 BIGINT 范围的值会让数据库改写或报错，客户端必须先行拒绝
    return size if 0 <= size <= _MAX_SIZE else None


def _normalize_name(value: Any) -> Optional[str]:
    """只保存基本名称，拒绝过长名称、控制字符和非法 Unicode。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    if len(name) > 1024 or name in {".", ".."}:
        return None
    if any(ord(c) < 32 or 127 <= ord(c) < 160 for c in name):
        return None
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return name


def _build_ssl_context(ssl_ca: str) -> "_ssl.SSLContext":
    """VERIFY_CA + 主机名/IP SAN 校验：CA 缺失或证书不符都会让连接直接失败。"""
    context = _ssl.create_default_context(cafile=ssl_ca)
    context.check_hostname = True
    context.verify_mode = _ssl.CERT_REQUIRED
    return context


class Sha1CloudClient:
    """中心库的最小客户端：短连接 + 严格入参校验 + 全部异常吞掉 + 失败熔断。"""

    def __init__(self) -> None:
        self._config: Optional[Dict[str, Any]] = None
        self._config_loaded = False
        self._disabled_logged = False
        self._failure_count = 0
        self._open_until = 0.0
        self._half_open = False
        self._state_lock = threading.Lock()

    def _ensure_config(self) -> Optional[Dict[str, Any]]:
        if not self._config_loaded:
            self._config = _load_config()
            self._config_loaded = True
            if self._config is None and not self._disabled_logged:
                self._disabled_logged = True
                logger.info("共享SHA1库未配置或配置非法（SHA1DB_*），仅使用本地学习表")
        return self._config

    def _connect(self, config: Dict[str, Any]):
        import pymysql

        return pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8mb4",
            local_infile=False,
            connect_timeout=_CONNECT_TIMEOUT,
            read_timeout=_QUERY_TIMEOUT,
            write_timeout=_QUERY_TIMEOUT,
            autocommit=True,
            # 显式传入 TLS 上下文（VERIFY_CA+IDENTITY）：证书校验失败即连接失败，不降级明文
            ssl=_build_ssl_context(str(config["ssl_ca"])),
        )

    def _breaker_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _enter_half_open_if_expired(self) -> None:
        """熔断到期后进入半开：放行一个探针请求；成败决定关闭或再次熔断。"""
        with self._state_lock:
            if self._open_until and time.monotonic() >= self._open_until:
                self._open_until = 0.0
                self._half_open = True

    def _record_success(self) -> None:
        with self._state_lock:
            self._failure_count = 0
            self._open_until = 0.0
            self._half_open = False

    def _record_failure(self) -> None:
        with self._state_lock:
            if self._half_open:
                # 半开探针失败：立即重新熔断一个完整周期
                self._open_until = time.monotonic() + _BREAKER_OPEN_SECONDS
                logger.warning(
                    "共享SHA1库半开探针失败，继续熔断 %d 分钟", _BREAKER_OPEN_SECONDS // 60,
                )
                return
            self._failure_count += 1
            if self._failure_count >= _BREAKER_THRESHOLD:
                self._open_until = time.monotonic() + _BREAKER_OPEN_SECONDS
                self._failure_count = 0
                self._half_open = False
                logger.warning(
                    "共享SHA1库连续失败 %d 次，熔断 %d 分钟",
                    _BREAKER_THRESHOLD, _BREAKER_OPEN_SECONDS // 60,
                )

    def _run(self, sql: str, params: tuple) -> Optional[list]:
        config = self._ensure_config()
        if config is None:
            return None
        if self._breaker_open():
            return None
        if self._open_until:
            self._enter_half_open_if_expired()
            if self._breaker_open():
                return None
        try:
            connection = self._connect(config)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
                self._record_success()
                return rows
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
        except Exception as error:
            self._record_failure()
            logger.warning("共享SHA1库操作失败，本次按未命中处理：%s", _error_brief(error))
            return None

    def lookup_etag_by_sha1(self, sha1: Any, size: Any) -> Optional[str]:
        """按内容 (sha1, size) 反查共享库中别人学到的 123 etag。"""
        normalized_sha1 = _normalize_sha1(sha1)
        normalized_size = _normalize_size(size)
        if normalized_sha1 is None or normalized_size is None:
            return None
        rows = self._run(
            "SELECT etag FROM files WHERE sha1 = %s AND size = %s AND etag <> '' LIMIT 1",
            (normalized_sha1, normalized_size),
        )
        if not rows:
            return None
        etag = _normalize_etag(rows[0][0])
        return etag

    def lookup_sha1_by_etag(self, etag: Any, size: Any) -> Optional[Dict[str, Any]]:
        """按 (etag, size) 反查当初的 115 sha1；返回结构与本地学习表一致（不含文件名）。"""
        normalized_etag = _normalize_etag(etag)
        normalized_size = _normalize_size(size)
        if normalized_etag is None or normalized_size is None:
            return None
        rows = self._run(
            "SELECT sha1, size FROM files WHERE etag = %s AND size = %s LIMIT 1",
            (normalized_etag, normalized_size),
        )
        if not rows:
            return None
        sha1 = _normalize_sha1(rows[0][0])
        if sha1 is None:
            return None
        return {
            "sha1": sha1,
            "size": int(rows[0][1] or normalized_size),
            "etag": normalized_etag,
            "name": "",
            "source": "shared_db",
        }

    def learn_transfer(self, sha1: Any, size: Any, etag: Any, name: Any = "") -> bool:
        """搬运成功后的回写。

        用 INSERT IGNORE（首写优先）：同一 (sha1, size) 已存在时本次静默跳过，
        不回填、不覆盖。只需要 INSERT 权限。name 保存文件基本名称，
        不保存目录；已有空文件名由管理员单独维护，不给客户端 UPDATE 权限。
        etag 必须是有效非空的 32 位十六进制：无效时直接返回 False 且不发 SQL，
        避免空 etag 占住唯一键后正确值永远无法写入。
        """
        normalized_sha1 = _normalize_sha1(sha1)
        normalized_size = _normalize_size(size)
        normalized_etag = _normalize_etag(etag)
        if normalized_sha1 is None or normalized_size is None or normalized_etag is None:
            return False
        normalized_name = _normalize_name(name)
        if normalized_name is None:
            return False
        rows = self._run(
            """
            INSERT IGNORE INTO files (sha1, size, name, etag, pan)
            VALUES (%s, %s, %s, %s, '115')
            """,
            (normalized_sha1, normalized_size, normalized_name, normalized_etag),
        )
        return rows is not None


class Sha1ApiClient(Sha1CloudClient):
    """普通客户端仅连接 HTTPS API，不读取数据库凭据。"""

    def _request(self, method: str, payload: dict) -> Optional[dict]:
        if self._breaker_open():
            return None
        if self._open_until:
            self._enter_half_open_if_expired()
        try:
            import httpx
            from urllib.parse import urlsplit
            url = os.environ.get("SHA1_POOL_API_URL", "").rstrip("/")
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("invalid API URL")
            token = _token_override or os.environ.get("SHA1_POOL_API_TOKEN", "")
            if not token or token == "CHANGE_ME":
                raise ValueError("API token missing")
            verify = _ssl.create_default_context(cafile=os.environ.get("SHA1_POOL_API_CA") or None)
            with httpx.Client(verify=verify, timeout=5, follow_redirects=False, trust_env=False) as client:
                kwargs = {"params": payload} if method == "GET" else {"json": payload}
                endpoint = "/lookup" if method == "GET" else "/submit"
                with client.stream(method, url + endpoint,
                                   headers={"Authorization": "Bearer " + token}, **kwargs) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 16384:
                            raise ValueError("API response too large")
                    import json
                    result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("invalid API response")
            self._record_success()
            return result
        except Exception as error:
            self._record_failure()
            logger.warning("共享SHA1 API失败，本次按未命中处理：%s", _error_brief(error))
            return None

    def lookup_etag_by_sha1(self, sha1: Any, size: Any) -> Optional[str]:
        sha1, size = _normalize_sha1(sha1), _normalize_size(size)
        if sha1 is None or size is None:
            return None
        result = self._request("GET", {"sha1": sha1, "size": size})
        return _normalize_etag(result.get("etag")) if result else None

    def lookup_sha1_by_etag(self, etag: Any, size: Any) -> Optional[Dict[str, Any]]:
        etag, size = _normalize_etag(etag), _normalize_size(size)
        if etag is None or size is None:
            return None
        result = self._request("GET", {"etag": etag, "size": size})
        sha1 = _normalize_sha1(result.get("sha1")) if result else None
        if sha1 is None:
            return None
        return {"sha1": sha1, "size": size, "etag": etag, "name": "", "source": "shared_db"}

    def learn_transfer(self, sha1: Any, size: Any, etag: Any, name: Any = "") -> bool:
        sha1, size, etag, name = (_normalize_sha1(sha1), _normalize_size(size),
                                 _normalize_etag(etag), _normalize_name(name))
        if any(value is None for value in (sha1, size, etag, name)):
            return False
        result = self._request("POST", {"sha1": sha1, "size": size, "etag": etag, "name": name})
        return bool(result and result.get("ok") is True)


# 配置 API 时绝不回退到直连数据库。未配置 API 则兼容原来的 SHA1DB_* 环境变量。
_client = Sha1ApiClient() if os.environ.get("SHA1_POOL_API_URL") else Sha1CloudClient()

# 运行时可覆盖的秒传池 Token：默认用环境变量里的分发 Token（只够搬运加速），
# 设置页可切换为管理员 Token 解锁目录搜索；重置即清掉覆盖。
_token_override: Optional[str] = None


def set_pool_token_override(token: Optional[str]) -> None:
    """设置/清除秒传池 Token 覆盖；传 None 或空串恢复环境变量里的默认 Token。"""
    global _token_override
    _token_override = str(token or "").strip() or None
    reset = getattr(_client, "_record_success", None)
    if callable(reset):
        reset()  # 换 Token 后清掉旧 Token 留下的失败计数/熔断状态


def get_pool_token_override() -> Optional[str]:
    return _token_override


async def lookup_etag_by_sha1(sha1: Any, size: Any) -> Optional[str]:
    return await asyncio.to_thread(_client.lookup_etag_by_sha1, sha1, size)


async def lookup_sha1_by_etag(etag: Any, size: Any) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_client.lookup_sha1_by_etag, etag, size)


async def learn_transfer(sha1: Any, size: Any, etag: Any, name: Any = "") -> bool:
    return await asyncio.to_thread(_client.learn_transfer, sha1, size, etag, name)
