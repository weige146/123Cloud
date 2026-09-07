import logging
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from app import sha1_cloud
from app.sha1_pool_api import create_app

@pytest.fixture
def db_env(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.pem'
    ca.write_text('test-only placeholder')
    for k, v in {'HOST': 'db.example.invalid', 'PORT': '3306', 'NAME': 'sha1db',
                 'USER': 'sha1_client', 'PASSWORD': 'test-only-password', 'SSL_CA': str(ca)}.items():
        monkeypatch.setenv('SHA1DB_' + k, v)

def test_admin_config_rejected(db_env, monkeypatch):
    assert sha1_cloud._load_config() is not None
    for user in ('root', 'sha1_admin'):
        monkeypatch.setenv('SHA1DB_USER', user)
        assert sha1_cloud._load_config() is None

def test_other_database_rejected(db_env, monkeypatch):
    monkeypatch.setenv('SHA1DB_NAME', 'mysql')
    assert sha1_cloud._load_config() is None

def test_filename_parameterized_and_basename_only():
    client = sha1_cloud.Sha1CloudClient()
    client._run = MagicMock(return_value=[])
    filename = "测试'; DROP TABLE files;--.mkv"
    assert client.learn_transfer('a'*40, 1, 'b'*32, '/private/path/' + filename)
    sql, params = client._run.call_args.args
    assert filename not in sql
    assert params == ('a'*40, 1, filename, 'b'*32)
    client._run.reset_mock()
    for bad in ('x'*1025, 'a\x00b', '\ud800', 42):
        assert not client.learn_transfer('a'*40, 1, 'b'*32, bad)
    client._run.assert_not_called()

def test_secrets_absent_in_error_logs(db_env, caplog):
    client = sha1_cloud.Sha1CloudClient()
    client._connect = MagicMock(side_effect=RuntimeError('SECRET_DSN_AND_PASSWORD'))
    with caplog.at_level(logging.WARNING):
        assert client.lookup_etag_by_sha1('a'*40, 1) is None
    assert 'SECRET_DSN_AND_PASSWORD' not in caplog.text

@pytest.fixture
def api(db_env, monkeypatch):
    monkeypatch.setenv('SHA1_POOL_API_TOKENS', 'test-only-' + 'a'*32)
    app = create_app()
    app.state.db = MagicMock()
    app.state.db.lookup_etag_by_sha1.return_value = 'b'*32
    app.state.db.lookup_sha1_by_etag.return_value = {'sha1': 'a'*40}
    app.state.db.learn_transfer.return_value = True
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer test-only-' + 'a'*32
    return client, app.state.db

def test_api_lookup_submit_and_bounds(api):
    client, db = api
    assert client.get('/lookup', params={'sha1': 'a'*40, 'size': 1}).json() == {'etag': 'b'*32}
    assert client.get('/lookup', params={'etag': 'b'*32, 'size': 1}).json() == {'sha1': 'a'*40}
    assert client.get('/lookup').status_code == 400
    assert client.get('/lookup', params={'sha1': "' OR 1=1", 'size': 1}).status_code == 400
    data = {'sha1': 'a'*40, 'size': 1, 'etag': 'b'*32, 'name': '中文.mkv'}
    assert client.post('/submit', json=data).json() == {'ok': True}
    db.learn_transfer.assert_called_once_with('a'*40, 1, 'b'*32, '中文.mkv')
    assert client.post('/submit', json=[data]).status_code == 400
    assert client.post('/submit', content='x'*16385, headers={'Content-Type': 'application/json'}).status_code == 413
    assert client.get('/lookup', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    db.lookup_etag_by_sha1.side_effect = RuntimeError('SECRET_DSN')
    response = client.get('/lookup', params={'sha1': 'a'*40, 'size': 1})
    assert response.status_code == 503 and 'SECRET_DSN' not in response.text

def test_api_rate_limit(api):
    client, _ = api
    for _ in range(120):
        assert client.get('/lookup', params={'sha1': 'a'*40, 'size': 1}).status_code == 200
    assert client.get('/lookup', params={'sha1': 'a'*40, 'size': 1}).status_code == 429

def test_api_client_https_and_no_db_fallback(monkeypatch):
    monkeypatch.setenv('SHA1_POOL_API_URL', 'http://pool.example.invalid')
    client = sha1_cloud.Sha1ApiClient()
    client._connect = MagicMock()
    assert client.lookup_etag_by_sha1('a'*40, 1) is None
    client._connect.assert_not_called()

def test_api_client_roundtrip_mock(monkeypatch):
    import httpx
    monkeypatch.setenv('SHA1_POOL_API_URL', 'https://pool.example.invalid')
    monkeypatch.setenv('SHA1_POOL_API_TOKEN', 'test-only-' + 'a'*32)
    real_client = httpx.Client
    calls = []
    def handle(req):
        calls.append(req)
        assert req.headers['Authorization'].startswith('Bearer ')
        return httpx.Response(200, json={'etag': 'b'*32, 'sha1': 'a'*40, 'ok': True})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = sha1_cloud.Sha1ApiClient()
    assert client.lookup_etag_by_sha1('a'*40, 1) == 'b'*32
    assert client.lookup_sha1_by_etag('b'*32, 1)['sha1'] == 'a'*40
    assert client.learn_transfer('a'*40, 1, 'b'*32, '中文.mkv')
    assert len(calls) == 3

def test_nonfinite_decimal_rejected():
    from decimal import Decimal
    for value in ('NaN', 'sNaN', 'Infinity', '-Infinity'):
        assert sha1_cloud._normalize_size(Decimal(value)) is None

def test_real_pymysql_tls_is_required(tmp_path):
    import ssl
    import pymysql
    connection = pymysql.connect(ssl=ssl.create_default_context(), defer_connect=True,
                                 local_infile=False)
    assert connection.ssl is True
    assert connection._ssl_required is True
    assert connection.ctx.check_hostname is True
    assert connection.ctx.verify_mode == ssl.CERT_REQUIRED
    assert not connection._local_infile
