"""sha1_cloud 共享SHA1库客户端的单元测试（不连真实数据库）。"""

from __future__ import annotations

import ssl as _ssl_module
import sys
import time
import types
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from app import sha1_cloud


class FakeCursor:
    def __init__(self, rows: Optional[List[Tuple]] = None) -> None:
        self.rows = rows or []
        self.executed: List[Tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> List[Tuple]:
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class FakeConnection:
    def __init__(self, rows: Optional[List[Tuple]] = None) -> None:
        self.cursor_obj = FakeCursor(rows)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


def _config(ssl_ca: str = "ca.pem") -> Dict[str, Any]:
    return {"host": "x", "port": 1, "user": "u", "password": "p",
            "database": "d", "ssl_ca": ssl_ca}


def _ready_client(rows: Optional[List[Tuple]] = None) -> Tuple[sha1_cloud.Sha1CloudClient, FakeConnection]:
    client = sha1_cloud.Sha1CloudClient()
    client._config = _config()
    client._config_loaded = True
    connection = FakeConnection(rows)
    client._connect = MagicMock(return_value=connection)  # type: ignore[method-assign]
    return client, connection


def _write_self_signed_ca(path: Any) -> None:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_unconfigured_client_never_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in sha1_cloud._REQUIRED_FIELDS:
        monkeypatch.delenv(field, raising=False)
    client = sha1_cloud.Sha1CloudClient()
    client._connect = MagicMock()  # type: ignore[method-assign]
    assert sha1_cloud._load_config() is None
    assert client.lookup_etag_by_sha1("a" * 40, 1) is None
    assert client.lookup_sha1_by_etag("a" * 32, 1) is None
    assert client.learn_transfer("a" * 40, 1, "a" * 32, "x.bin") is False
    client._connect.assert_not_called()


def test_any_missing_required_field_disables(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    ca_file = tmp_path / "ca.pem"
    _write_self_signed_ca(ca_file)
    values = {
        "SHA1DB_HOST": "db.example.invalid",
        "SHA1DB_PORT": "3306",
        "SHA1DB_USER": "u",
        "SHA1DB_PASSWORD": "p",
        "SHA1DB_NAME": "sha1db",
        "SHA1DB_SSL_CA": str(ca_file),
    }
    # 基准：六项齐全 → 配置有效
    for field, value in values.items():
        monkeypatch.setenv(field, value)
    assert sha1_cloud._load_config() is not None
    # 逐项移除，每一项缺失都必须停用
    for field in sha1_cloud._REQUIRED_FIELDS:
        monkeypatch.delenv(field, raising=False)
        assert sha1_cloud._load_config() is None, f"缺少 {field} 时应停用"
        monkeypatch.setenv(field, values[field])


def test_invalid_port_disables_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    ca_file = tmp_path / "ca.pem"
    _write_self_signed_ca(ca_file)
    monkeypatch.setenv("SHA1DB_HOST", "db.example.invalid")
    monkeypatch.setenv("SHA1DB_PORT", "not-a-number")
    monkeypatch.setenv("SHA1DB_USER", "u")
    monkeypatch.setenv("SHA1DB_PASSWORD", "p")
    monkeypatch.setenv("SHA1DB_NAME", "d")
    monkeypatch.setenv("SHA1DB_SSL_CA", str(ca_file))
    client = sha1_cloud.Sha1CloudClient()
    client._connect = MagicMock()  # type: ignore[method-assign]
    assert sha1_cloud._load_config() is None
    assert client.lookup_etag_by_sha1("a" * 40, 1) is None
    client._connect.assert_not_called()


def test_ca_file_must_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("SHA1DB_HOST", "db.example.invalid")
    monkeypatch.setenv("SHA1DB_PORT", "3306")
    monkeypatch.setenv("SHA1DB_USER", "u")
    monkeypatch.setenv("SHA1DB_PASSWORD", "p")
    monkeypatch.setenv("SHA1DB_NAME", "d")
    monkeypatch.setenv("SHA1DB_SSL_CA", str(tmp_path / "no-such-ca.pem"))
    assert sha1_cloud._load_config() is None


def test_invalid_inputs_short_circuit() -> None:
    client, connection = _ready_client()
    assert client.lookup_etag_by_sha1("not-a-hash", 1) is None
    assert client.lookup_etag_by_sha1("A" * 39, 1) is None
    assert client.lookup_etag_by_sha1("a" * 40, -1) is None
    assert client.lookup_etag_by_sha1("a" * 40, "abc") is None
    assert client.lookup_sha1_by_etag("zz", 1) is None
    assert client.learn_transfer("a" * 40, None, "a" * 32) is False
    assert connection.cursor_obj.executed == []


def test_lookup_etag_by_sha1_normalizes_and_hits() -> None:
    client, connection = _ready_client([("A" * 32,)])
    etag = client.lookup_etag_by_sha1("A" * 39 + "a", "123")
    assert etag == "a" * 32
    sql, params = connection.cursor_obj.executed[0]
    assert "FROM files" in sql and "LIMIT 1" in sql
    assert params == ("a" * 40, 123)


def test_lookup_sha1_by_etag_returns_shape_without_name() -> None:
    client, connection = _ready_client([("B" * 40, 456)])
    learned = client.lookup_sha1_by_etag("C" * 32, "456")
    assert learned == {
        "sha1": "b" * 40,
        "size": 456,
        "etag": "c" * 32,
        "name": "",
        "source": "shared_db",
    }
    sql, _ = connection.cursor_obj.executed[0]
    # 隐私：共享库查询不读取文件名
    assert "name" not in sql.replace("sha1", "")


def test_learn_transfer_uses_insert_ignore_with_name() -> None:
    client, connection = _ready_client()
    assert client.learn_transfer("A" * 40, "789", "D" * 32, "x.bin") is True
    sql, params = connection.cursor_obj.executed[0]
    # 账号只有 SELECT/INSERT 权限，回写绝不能包含需要 UPDATE 权限的子句
    assert "INSERT IGNORE" in sql
    assert "ON DUPLICATE" not in sql
    # 新增时写入文件名，仍不需要 UPDATE
    assert params == ("a" * 40, 789, "x.bin", "d" * 32)


def test_learn_transfer_rejects_invalid_etag_without_sql() -> None:
    client, connection = _ready_client()
    assert client.learn_transfer("A" * 40, 789, "invalid-etag", "x.bin") is False
    assert client.learn_transfer("A" * 40, 789, "", "x.bin") is False
    assert client.learn_transfer("A" * 40, 789, None, "x.bin") is False
    assert connection.cursor_obj.executed == []


def test_normalize_size_strictness() -> None:
    from decimal import Decimal

    assert sha1_cloud._normalize_size(True) is None
    assert sha1_cloud._normalize_size(1.9) is None
    assert sha1_cloud._normalize_size(float("inf")) is None
    assert sha1_cloud._normalize_size(Decimal("1.9")) is None
    assert sha1_cloud._normalize_size(Decimal("2")) == 2
    assert sha1_cloud._normalize_size(2**63) is None          # 超出 signed BIGINT
    assert sha1_cloud._normalize_size(2**63 - 1) == 2**63 - 1
    assert sha1_cloud._normalize_size(2.0) == 2
    assert sha1_cloud._normalize_size("10") == 10
    assert sha1_cloud._normalize_size(0) == 0


def test_build_ssl_context_requires_ca_and_verifies(tmp_path: Any) -> None:
    ca_file = tmp_path / "ca.pem"
    _write_self_signed_ca(ca_file)
    ctx = sha1_cloud._build_ssl_context(str(ca_file))
    assert ctx.verify_mode == _ssl_module.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_connect_always_passes_explicit_tls_context(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    class FakePymysqlModule(types.ModuleType):
        def connect(self, **kwargs: Any) -> FakeConnection:
            captured.update(kwargs)
            return FakeConnection()

    monkeypatch.setitem(sys.modules, "pymysql", FakePymysqlModule("pymysql"))

    ca_file = tmp_path / "ca.pem"
    _write_self_signed_ca(ca_file)
    client = sha1_cloud.Sha1CloudClient()
    client._config = _config(ssl_ca=str(ca_file))
    client._config_loaded = True
    connection = client._connect(client._config)
    assert isinstance(connection, FakeConnection)
    assert isinstance(captured.get("ssl"), _ssl_module.SSLContext)


def test_circuit_breaker_opens_after_consecutive_failures() -> None:
    client = sha1_cloud.Sha1CloudClient()
    client._config = _config()
    client._config_loaded = True
    client._connect = MagicMock(side_effect=OSError("network down"))  # type: ignore[method-assign]
    for _ in range(sha1_cloud._BREAKER_THRESHOLD):
        assert client.lookup_etag_by_sha1("a" * 40, 1) is None
    assert client._connect.call_count == sha1_cloud._BREAKER_THRESHOLD
    assert client._breaker_open()
    # 熔断打开后直接短路，不再尝试连接
    assert client.lookup_etag_by_sha1("a" * 40, 1) is None
    assert client._connect.call_count == sha1_cloud._BREAKER_THRESHOLD


def test_circuit_breaker_half_open_probe_failure_reopens(monkeypatch: pytest.MonkeyPatch) -> None:
    client = sha1_cloud.Sha1CloudClient()
    client._config = _config()
    client._config_loaded = True
    results = iter([OSError("down"), OSError("down"), OSError("down"), OSError("probe fail")])
    client._connect = MagicMock(side_effect=lambda config: (_ for _ in ()).throw(next(results)))  # type: ignore[method-assign]

    clock = {"now": 1000.0}
    monkeypatch.setattr(sha1_cloud.time, "monotonic", lambda: clock["now"])
    for _ in range(sha1_cloud._BREAKER_THRESHOLD):
        client.lookup_etag_by_sha1("a" * 40, 1)
    assert client._breaker_open()
    # 时间推进越过熔断窗口 → 进入半开，放行一个探针
    clock["now"] = client._open_until + 1
    assert not client._breaker_open()
    assert client.lookup_etag_by_sha1("a" * 40, 1) is None  # 探针失败
    # 半开探针失败 → 立即重新熔断，无需再累计三次
    assert client._breaker_open()
    assert not client._half_open or client._open_until > clock["now"]


def test_circuit_breaker_half_open_probe_success_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    client, connection = _ready_client([("A" * 32,)])
    clock = {"now": 1000.0}
    monkeypatch.setattr(sha1_cloud.time, "monotonic", lambda: clock["now"])
    # 直接置为"已熔断且到期"状态
    client._open_until = 1000.0
    client._half_open = True
    clock["now"] = 1001.0
    assert not client._breaker_open()
    assert client.lookup_etag_by_sha1("a" * 40, 1) == "a" * 32  # 半开探针成功
    assert client._open_until == 0.0
    assert not client._half_open
    assert client._failure_count == 0


def test_connection_failure_swallowed_as_miss() -> None:
    client = sha1_cloud.Sha1CloudClient()
    client._config = _config()
    client._config_loaded = True
    client._connect = MagicMock(side_effect=OSError("network down"))  # type: ignore[method-assign]
    assert client.lookup_etag_by_sha1("a" * 40, 1) is None
    assert client.lookup_sha1_by_etag("a" * 32, 1) is None
    assert client.learn_transfer("a" * 40, 1, "a" * 32, "x.bin") is False
