"""影库模块测试：/api/library/*（数据库优先架构）。

- 影库引擎：两种 JSON 格式解析、作品聚合（tmdb 标记 / Season 并入 / commonPath 兜底）
- 数据库层：导入入库、dir 去重、同来源重导更新、拼音搜索、分类/导出/转存取数
- 分享提取：输入解析、类型过滤、fastlink 组装
- 客户端内转存：ensure_path + md5_reuse 编排、进度与取消
- 路由：导入（上传/路径/文件夹）、来源列表与级联删除、配置读写、
  令牌门禁（无令牌放行 / 错令牌 401 / Bearer 放行）、明文令牌只回本机
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app import library_transfer, main, movie_library, movie_library_db
from app.movie_library import (
    b62_to_hex,
    etag_hex,
    hex_to_base62,
    norm,
    parse_dir_name,
    parse_library_content,
)


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _all_works(db_path: Path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return [{"dir": r[0], "title": r[1]}
                for r in conn.execute("SELECT dir, title FROM library_works").fetchall()]
    finally:
        conn.close()


def _fastlink_payload(common_path: str, files) -> dict:
    return {
        "scriptVersion": "3.2.1",
        "exportVersion": "1.0",
        "usesBase62EtagsInExport": True,
        "commonPath": common_path,
        "totalFilesCount": len(files),
        "files": files,
    }


def _etag(i: int) -> str:
    # 造 32 位 hex MD5 形状的假 etag
    return f"{i:032x}"


WORK_A = "电影/华语/海王 (2018) {tmdb-297802}"
WORK_B = "电影/外语/Sea King (2018) {tmdb-2}"


class MovieLibraryEngineTests(unittest.TestCase):
    def test_base62_hex_roundtrip(self):
        for i in range(0, 20):
            hex_md5 = _etag(i)
            self.assertEqual(b62_to_hex(hex_to_base62(hex_md5)), hex_md5)
        self.assertEqual(etag_hex(_etag(7).upper()), _etag(7))
        self.assertEqual(etag_hex(hex_to_base62(_etag(8))), _etag(8))
        self.assertEqual(etag_hex("不是etag"), "")
        self.assertEqual(norm("海 王 (2018) _Part-1_"), "海王2018part1")

    def test_parse_dir_name(self):
        self.assertEqual(parse_dir_name("电影/华语/海王 (2018) {tmdb-297802}"), ("海王", 2018, 297802))
        self.assertEqual(parse_dir_name("剧集/某剧 [tmdb-12345]"), ("某剧", None, 12345))
        title, year, tmdb = parse_dir_name("无标记目录")
        self.assertEqual((title, year, tmdb), ("无标记目录", None, None))

    def test_aggregate_works(self):
        from app.movie_library import aggregate_works
        files = [
            {"path": f"{WORK_A}/Season 1/S01E01.mkv", "fileName": "S01E01.mkv", "etag": _etag(1), "size": 100},
            {"path": f"{WORK_A}/海报.jpg", "fileName": "海报.jpg", "etag": _etag(3), "size": 10},
        ]
        works = aggregate_works("电影/华语", files)
        self.assertIn(WORK_A, works)
        info = works[WORK_A]
        self.assertEqual(info["title"], "海王")
        self.assertEqual(info["year"], 2018)
        self.assertEqual(info["tmdb_id"], 297802)
        self.assertEqual(info["count"], 2)
        self.assertEqual(info["video_count"], 1)
        # 拼音
        self.assertEqual(info["pinyin"], "haiwang")
        self.assertEqual(info["pinyin_first"], "hw")

    def test_parse_all_formats(self):
        from app.movie_library import parse_123share, parse_fastlink_text
        import base64 as b64

        payload = parse_fastlink_text(
            "123FLCPV2$" + WORK_A
            + "%m7dq0IgK1FqBbFJGBEFejQ#5346094965#Season 1/E01.mkv"
            + "$0123456789abcdef0123456789abcdef#120000#海报.jpg"
        )
        self.assertEqual(payload["commonPath"], WORK_A)
        self.assertEqual(payload["files"][0]["etag"], b62_to_hex("m7dq0IgK1FqBbFJGBEFejQ"))

        payload_v1 = parse_fastlink_text("123FSLinkV1$0123456789abcdef0123456789abcdef#7#a.mkv")
        self.assertEqual(payload_v1["files"][0]["etag"], "0123456789abcdef0123456789abcdef")

        nodes = [
            {"FileId": "1", "FileName": "剧集", "Type": 1, "ParentFileId": "0"},
            {"FileId": "2", "FileName": "某剧 (2023) {tmdb-9}", "Type": 1, "ParentFileId": "1"},
            {"FileId": "3", "FileName": "E01.mkv", "Type": 0, "Size": 100,
             "Etag": "m7dq0IgK1FqBbFJGBEFejQ", "ParentFileId": "2"},
        ]
        payload_share = parse_123share(b64.b64encode(json.dumps(nodes).encode()).decode())
        self.assertEqual(payload_share["files"][0]["path"], "剧集/某剧 (2023) {tmdb-9}/E01.mkv")

        payload_arr = parse_library_content(json.dumps([["abc", "5", "x/a.mkv"]]))
        self.assertEqual(payload_arr["files"][0]["path"], "x/a.mkv")

        # 文本里塞 base64：依次回退到 .123share 解析
        content = parse_library_content(b64.b64encode(json.dumps(nodes).encode()).decode())
        self.assertEqual(content["files"][0]["path"], "剧集/某剧 (2023) {tmdb-9}/E01.mkv")

    def test_db_import_search_and_dedup(self):
        """DB 层：入库、dir 去重、同来源重导更新、SQL 搜索（含拼音/首字母）。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")

            # 来源 A：fastlink JSON（含 Season 子目录）
            payload_a = _fastlink_payload(WORK_A + "/", [
                {"path": "Season 1/S01E01.mkv", "fileName": "S01E01.mkv", "etag": hex_to_base62(_etag(1)), "size": 100},
                {"path": "海报.jpg", "fileName": "海报.jpg", "etag": hex_to_base62(_etag(3)), "size": 10},
            ])
            r = db.import_payload("库A.json", payload_a)
            self.assertEqual(r["added"], 1)
            self.assertEqual(r["skipped"], 0)

            # 来源 B：另一作品 + 与 A 完全相同的 dir（skip 模式下应整作品跳过）
            payload_b = _fastlink_payload("", [
                {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 200},
                {"path": f"{WORK_A}/dup.mkv", "fileName": "dup.mkv", "etag": _etag(9), "size": 999},
            ])
            r = db.import_payload("库B.json", payload_b, mode="skip")
            self.assertEqual(r["added"], 1)
            self.assertEqual(r["skipped"], 1)

            # 关键词/拼音/首字母搜索
            total, rows = db.search("海王", 1, 20)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["dir"], WORK_A)
            total, _ = db.search("haiwang", 1, 20)
            self.assertEqual(total, 1)
            total, _ = db.search("hw", 1, 20)
            self.assertEqual(total, 1)

            # 分类过滤 + 分页
            total, rows = db.search("", 1, 20, cat="电影", sub="外语")
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["dir"], WORK_B)
            total, _ = db.search("", 1, 1)
            self.assertEqual(total, 2)
            _, page2 = db.search("", 2, 1)
            self.assertEqual(len(page2), 1)

            # 来源筛选
            total, _ = db.search("", 1, 20, libs=["库A.json"])
            self.assertEqual(total, 1)

            # 来源列表
            sources = db.list_sources()
            self.assertEqual({s["name"] for s in sources}, {"库A.json", "库B.json"})

            # categories：不足 2 个作品的分类不出
            cats = db.categories()
            self.assertEqual(cats[0]["name"], "电影")
            self.assertEqual(cats[0]["count"], 2)

            # list_files / export_work
            listing = db.list_files(WORK_A)
            self.assertEqual(len(listing["files"]), 2)
            exported = db.export_work(WORK_A)
            self.assertEqual(exported["totalFilesCount"], 2)
            self.assertTrue(exported["files"][0]["etag"])

            # 同来源重导 → 更新（skip 模式：先删后插，整包替换）
            payload_a2 = _fastlink_payload(WORK_A + "/", [
                {"path": "new.mkv", "fileName": "new.mkv", "etag": _etag(8), "size": 5},
            ])
            db.import_payload("库A.json", payload_a2, mode="skip")
            listing = db.list_files(WORK_A)
            self.assertEqual(listing["files"][0]["fileName"], "new.mkv")

            # 删除来源级联（作品与文件一并移除）
            self.assertTrue(db.delete_source("库A.json"))
            self.assertFalse(db.works_exist([WORK_A]))
            self.assertEqual(db.totals()["workCount"], 1)
            self.assertEqual(len(db.list_files(WORK_A) or {}), 0)

    def test_db_export_category_and_multi(self):
        """分类导出/多分类合并导出（etag 去重）与转存取数 includeFiles。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
                {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 200},
                {"path": "剧集/某剧 (2023) {tmdb-9}/e01.mkv", "fileName": "e01.mkv", "etag": _etag(3), "size": 300},
            ]))

            cat = db.export_category("电影")
            self.assertEqual(cat["totalFilesCount"], 2)
            self.assertEqual(cat["commonPath"], "电影/")
            # 相对路径：前缀已剥掉
            self.assertTrue(all(not f["path"].startswith("电影/") for f in cat["files"]))

            merged = db.export_multi([("电影", ""), ("剧集", "")])
            self.assertEqual(merged["totalFilesCount"], 3)
            # 合并导出按 etag 去重
            dup = db.export_multi([("电影", ""), ("电影", "华语")])
            self.assertEqual(dup["totalFilesCount"], 2)

            works = db.transfer_files([WORK_A], ["a.mkv"])
            self.assertEqual(len(works), 1)
            self.assertEqual([f["fileName"] for f in works[0]["files"]], ["a.mkv"])
            self.assertEqual(db.transfer_files([WORK_A], ["不存在.mkv"]), [])
            self.assertEqual(db.transfer_files(["不存在目录"]), [])


class MovieLibraryClassificationTests(unittest.TestCase):
    """影库分类重写步骤1：地区归一、tmdb_status 入库、旧库迁移补列。"""

    def test_normalize_region(self):
        self.assertEqual(movie_library.normalize_region(["CN"]), "华语")
        self.assertEqual(movie_library.normalize_region(["HK"]), "港台")
        self.assertEqual(movie_library.normalize_region(["TW", "CN"]), "港台")  # 首个命中
        self.assertEqual(movie_library.normalize_region(["JP"]), "日韩")
        self.assertEqual(movie_library.normalize_region(["US", "GB"]), "欧美")
        self.assertEqual(movie_library.normalize_region("cn"), "华语")  # 字符串、大小写不敏感
        self.assertEqual(movie_library.normalize_region(["NG"]), "其他")  # 有值未映射
        self.assertEqual(movie_library.normalize_region([]), "")
        self.assertEqual(movie_library.normalize_region(None), "")

    def test_import_sets_tmdb_status(self):
        """有 tmdb_id → pending（交后台回填），无 tmdb 标记 → none。"""
        from app.movie_library_db import LibraryDb
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
                {"path": "电影/无名作品 2020/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 200},
            ]))
            conn = sqlite3.connect(Path(d) / "cloud123.db")
            try:
                status = {r[0]: r[1] for r in conn.execute(
                    "SELECT dir, tmdb_status FROM library_works").fetchall()}
            finally:
                conn.close()
            self.assertEqual(status[WORK_A], "pending")
            self.assertEqual(status["电影/无名作品 2020"], "none")

    def test_migration_adds_columns_backfills_and_is_idempotent(self):
        """旧库缺新列：LibraryDb 初始化补列、把有 tmdb_id 的旧行排进 pending、可重复迁移。"""
        import sqlite3
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db_file = Path(d) / "cloud123.db"
            old_schema = (
                "CREATE TABLE library_works (dir TEXT PRIMARY KEY, title TEXT NOT NULL,"
                " norm_title TEXT NOT NULL, year INTEGER, tmdb_id INTEGER,"
                " cat TEXT NOT NULL DEFAULT '', sub TEXT NOT NULL DEFAULT '',"
                " pinyin TEXT NOT NULL DEFAULT '', pinyin_first TEXT NOT NULL DEFAULT '',"
                " file_count INTEGER NOT NULL DEFAULT 0, video_count INTEGER NOT NULL DEFAULT 0,"
                " total_size INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL);"
            )
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(old_schema)
                conn.execute(
                    "INSERT INTO library_works (dir, title, norm_title, tmdb_id, source)"
                    " VALUES (?,?,?,?,?)",
                    (WORK_A, "海王", "海王", 297802, "旧库.json"))
                conn.execute(
                    "INSERT INTO library_works (dir, title, norm_title, tmdb_id, source)"
                    " VALUES (?,?,?,?,?)",
                    ("电影/无名 2020", "无名", "无名", None, "旧库.json"))
                conn.commit()
            finally:
                conn.close()

            LibraryDb(db_file)  # 触发迁移
            LibraryDb(db_file)  # 再迁移一次，验证幂等（不报错、不重复补列）

            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(library_works)").fetchall()}
                self.assertTrue({
                    "media_type", "genres", "region", "poster_path",
                    "vote_average", "overview", "tmdb_status", "enrich_attempts",
                } <= cols)
                rows = {r["dir"]: dict(r) for r in conn.execute("SELECT * FROM library_works").fetchall()}
            finally:
                conn.close()
            self.assertEqual(rows[WORK_A]["tmdb_status"], "pending")
            self.assertEqual(rows["电影/无名 2020"]["tmdb_status"], "none")
            self.assertEqual(rows[WORK_A]["genres"], "[]")  # 新列默认值


class _FakePan123:
    """记录 ensure_path/md5_reuse 调用的假 OpenAPI 客户端。"""

    def __init__(self):
        self.calls = []

    async def ensure_path(self, root_dir_id, path):
        parts = [str(root_dir_id)] + [p for p in path if p]
        return "/".join(parts)

    async def md5_reuse(self, parent_file_id, filename, etag, size):
        self.calls.append((parent_file_id, filename, etag, size))
        if filename.startswith("miss"):
            return None
        if filename.startswith("boom"):
            raise RuntimeError("随机失败")
        return 4321


class LibraryTransferTests(unittest.TestCase):
    def test_transfer_orchestrates_reuse(self):
        fake = _FakePan123()
        works = [{
            "dir": WORK_A,
            "files": [
                {"fileName": "ok1.mkv", "etag": _etag(1), "size": 1},
                {"fileName": "miss1.mkv", "etag": _etag(2), "size": 2},
                {"fileName": "ok2.mkv", "etag": hex_to_base62(_etag(3)), "size": 3},
                {"fileName": "bad.mkv", "etag": "!!!", "size": 4},
            ],
        }]
        tid = library_transfer.transfer_manager.start_task(
            fake, works, target_path="影库转存", target_dir_id="0", interval_ms=100, label="单作品",
            concurrency=1,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            task = library_transfer.transfer_manager.get_task(tid)
            if task["status"] != "running":
                break
            time.sleep(0.05)
        task = library_transfer.transfer_manager.get_task(tid)
        self.assertEqual(task["status"], "done", task)
        # 目标目录 + 作品子目录都被创建
        self.assertEqual(fake.calls[0][0], f"0/影库转存/{WORK_A}")
        # base62 etag 被统一转回 hex
        self.assertEqual(fake.calls[0][2], _etag(1))
        self.assertEqual(task["result"]["success"], 2)
        self.assertEqual(task["result"]["missed"], 1)
        self.assertEqual(task["result"]["failed"], 1)

    def test_transfer_concurrency_processes_all(self):
        fake = _FakePan123()
        works = [{
            "dir": "电影/A",
            "files": [{"fileName": f"f{i}.mkv", "etag": _etag(i), "size": 1} for i in range(10)],
        }]
        tid = library_transfer.transfer_manager.start_task(
            fake, works, target_path="目标", target_dir_id="0", interval_ms=10, concurrency=5,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            task = library_transfer.transfer_manager.get_task(tid)
            if task["status"] != "running":
                break
            time.sleep(0.05)
        task = library_transfer.transfer_manager.get_task(tid)
        self.assertEqual(task["status"], "done", task)
        self.assertEqual(task["result"]["success"], 10)
        self.assertEqual(len(fake.calls), 10)

    def test_cancel_requested_flags(self):
        fake = _FakePan123()
        works = [{"dir": "A", "files": [{"fileName": "a.mkv", "etag": _etag(1), "size": 1}]}]
        tid = library_transfer.transfer_manager.start_task(fake, works, target_path="", target_dir_id="0", interval_ms=100, concurrency=1)
        self.assertTrue(library_transfer.transfer_manager.request_cancel(tid))
        self.assertFalse(library_transfer.transfer_manager.request_cancel("不存在"))


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("Client", (), {"host": host})()


class _StubBodyRequest(_StubRequest):
    """带请求体的桩：导入路由会 await request.body()。"""

    def __init__(self, body: bytes, host: str = "127.0.0.1", authorization: str = ""):
        super().__init__(host=host, authorization=authorization)
        self._body = body

    async def body(self) -> bytes:
        return self._body


class LibraryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        # 换上测试专用 store，结束后必须恢复，否则污染同进程内的其他测试模块
        self._original_store = main.store
        self.addCleanup(setattr, main, "store", self._original_store)
        self.store = main.store = self._patch_store()
        # 影库数据库单例同样指向测试库，避免写进真实 cloud123.db
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(self.store.db_file)

    def _patch_store(self):
        from app.session_store import SessionStore
        return SessionStore(Path(self._directory.name))

    def _seed_library(self, name: str = "库.json") -> str:
        """直接入库一个作品，返回作品目录。"""
        result = movie_library_db.import_payload(name, _fastlink_payload("电影/", [
            {"path": f"{WORK_A}/v.mkv", "fileName": "v.mkv", "etag": _etag(1), "size": 123},
        ]))
        self.assertTrue(result["ok"])
        return WORK_A

    def test_config_roundtrip_and_token_masking(self):
        with unittest.mock.patch.object(main, "store", self.store):
            saved = asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                transferIntervalMs=60, transferConcurrency=3, exportDir="/tmp/导出", token="a" * 24,
            ), _StubRequest(host="127.0.0.1")))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["config"]["exportDir"], "/tmp/导出")
            self.assertEqual(saved["config"]["transferIntervalMs"], 60)
            self.assertEqual(saved["config"]["transferConcurrency"], 3)
            # 本机：明文返回令牌
            self.assertEqual(saved["config"]["token"], "a" * 24)
            remote = asyncio.run(main.read_library_config(_StubRequest(host="192.168.1.8")))
            self.assertEqual(remote["config"]["token"], "")
            self.assertTrue(remote["config"]["tokenSet"])
            self.assertIsNotNone(remote["config"]["tokenPreview"])
            local = asyncio.run(main.read_library_config(_StubRequest(host="127.0.0.1")))
            self.assertEqual(local["config"]["token"], "a" * 24)

    def test_token_guard(self):
        with unittest.mock.patch.object(main, "store", self.store):
            asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                token="t" * 24,
            ), _StubRequest()))
            # 未带令牌 → 401
            with self.assertRaises(HTTPException) as ctx:
                main._guard_library_token(_StubRequest(host="10.0.0.2"))
            self.assertEqual(ctx.exception.status_code, 401)
            # 错令牌 → 401；query 正确 → 放行；Bearer 正确 → 放行
            with self.assertRaises(HTTPException):
                main._guard_library_token(_StubRequest(host="10.0.0.2"), "wrong")
            main._guard_library_token(_StubRequest(host="10.0.0.2"), "t" * 24)
            main._guard_library_token(
                _StubRequest(host="10.0.0.2", authorization="Bearer " + "t" * 24))
            # 清掉令牌后放行
            asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                clearToken=True,
            ), _StubRequest()))
            main._guard_library_token(_StubRequest(host="10.0.0.2"))

    def test_config_blank_token_keeps_existing(self):
        with unittest.mock.patch.object(main, "store", self.store):
            asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                token="k" * 24,
            ), _StubRequest()))
            saved = asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                exportDir="/tmp/x", token="",
            ), _StubRequest()))
            self.assertTrue(saved["config"]["tokenSet"])

    def test_import_route_ingests_to_db(self):
        """上传导入（浏览器）→ 直接入库，源 JSON 可删。"""
        with unittest.mock.patch.object(main, "store", self.store):
            text = json.dumps(_fastlink_payload("电影/", [
                {"path": f"{WORK_A}/v.mkv", "fileName": "v.mkv", "etag": _etag(1), "size": 123},
            ]), ensure_ascii=False)
            result = asyncio.run(main.import_library_file(
                _StubBodyRequest(text.encode("utf-8")), name="上传库.json", token="",
            ))
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["skipped"], 0)
            status = asyncio.run(main.read_library_status())
            self.assertEqual(status["status"]["workCount"], 1)
            self.assertEqual(status["status"]["libCount"], 1)
            # 空内容 / 非影库内容应报错
            with self.assertRaises(HTTPException):
                asyncio.run(main.import_library_file(_StubBodyRequest(b""), name="x.json"))
            with self.assertRaises(HTTPException):
                asyncio.run(main.import_library_file(_StubBodyRequest(b"not a library"), name="x.json"))

    def test_import_paths_route(self):
        """按本地路径批量导入（桌面端文件选择器）。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_dir, tmp)
        _write_json(tmp / "A.json", _fastlink_payload("", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 1},
        ]))
        _write_json(tmp / "B.json", _fastlink_payload("", [
            {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 2},
        ]))
        with unittest.mock.patch.object(main, "store", self.store):
            result = asyncio.run(main.import_library_paths(
                main.LibraryImportPathsRequest(paths=[str(tmp / "A.json"), str(tmp / "B.json"), str(tmp / "缺失.json")]),
                _StubRequest(),
            ))
            self.assertTrue(result["ok"])
            self.assertEqual(result["added"], 2)
            self.assertEqual(result["failed"], 1)
            names = {s["name"] for s in movie_library_db.list_sources()}
            self.assertEqual(names, {"A.json", "B.json"})
            # 同一来源再次导入（默认合并模式）：文件完全相同 → 无新增、作品数不叠加
            again = asyncio.run(main.import_library_paths(
                main.LibraryImportPathsRequest(paths=[str(tmp / "A.json")]), _StubRequest(),
            ))
            self.assertEqual(again["added"], 0)
            self.assertEqual(again["mergedWorks"], 0)
            self.assertEqual(again["skipped"], 1)
            self.assertEqual(asyncio.run(main.read_library_status())["status"]["workCount"], 2)

    def test_import_dir_route_skips_checkpoints(self):
        """文件夹批量导入：递归子目录，跳过 _checkpoints 与隐藏目录。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_dir, tmp)
        _write_json(tmp / "根库.json", _fastlink_payload("", [
            {"path": "电影/根作品 (2001) {tmdb-31}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 1},
        ]))
        sub = tmp / "子目录"
        sub.mkdir()
        _write_json(sub / "子库.json", _fastlink_payload("", [
            {"path": "剧集/子作品 (2002) {tmdb-32}/e.mkv", "fileName": "e.mkv", "etag": _etag(2), "size": 2},
        ]))
        (tmp / "_checkpoints").mkdir()
        _write_json(tmp / "_checkpoints" / "半成品.json", _fastlink_payload("", [
            {"path": "电影/不该出现 (2003) {tmdb-33}/x.mkv", "fileName": "x.mkv", "etag": _etag(3), "size": 3},
        ]))
        (tmp / ".hidden").mkdir()
        _write_json(tmp / ".hidden" / "h.json", _fastlink_payload("", [
            {"path": "电影/隐藏作品 (2004) {tmdb-34}/y.mkv", "fileName": "y.mkv", "etag": _etag(4), "size": 4},
        ]))
        with unittest.mock.patch.object(main, "store", self.store):
            result = asyncio.run(main.import_library_dir(
                main.LibraryImportDirRequest(path=str(tmp)), _StubRequest(),
            ))
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["added"], 2)
            self.assertEqual(result["failed"], 0)
            works = {w["title"] for w in _all_works(self.store.db_file)}
            self.assertEqual(works, {"根作品", "子作品"})
            # 目录不存在 → 400
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.import_library_dir(
                    main.LibraryImportDirRequest(path=str(tmp / "不存在")), _StubRequest()))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_sources_route_and_cascade_delete(self):
        with unittest.mock.patch.object(main, "store", self.store):
            self._seed_library("库A.json")
            movie_library_db.import_payload("库B.json", _fastlink_payload("", [
                {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 2},
            ]))
            listed = asyncio.run(main.read_library_sources())
            self.assertEqual({s["name"] for s in listed["sources"]}, {"库A.json", "库B.json"})

            asyncio.run(main.delete_library_source(
                main.LibrarySourceRequest(name="库A.json"), _StubRequest()))
            remaining = {s["name"] for s in movie_library_db.list_sources()}
            self.assertEqual(remaining, {"库B.json"})
            # 级联：作品与文件都不剩
            self.assertFalse(movie_library_db.works_exist([WORK_A]))
            self.assertIsNone(movie_library_db.list_files(WORK_A))
            # 删不存在的来源 → 404
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.delete_library_source(
                    main.LibrarySourceRequest(name="不存在.json"), _StubRequest()))
            self.assertEqual(ctx.exception.status_code, 404)

    def test_search_files_export_routes(self):
        with unittest.mock.patch.object(main, "store", self.store):
            work_dir = self._seed_library()
            result = asyncio.run(main.search_library(_StubRequest(), q="海王", page=1, size=20))
            self.assertTrue(result["ok"])
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["dirs"][0]["dir"], work_dir)
            files = asyncio.run(main.read_library_files(_StubRequest(), dir=work_dir))
            self.assertEqual(len(files["files"]), 1)
            exported = asyncio.run(main.export_library_json(_StubRequest(), dir=work_dir))
            self.assertEqual(exported["library"]["totalFilesCount"], 1)
            # 分类导出（带 lib 来源筛选）
            by_cat = asyncio.run(main.export_library_json(_StubRequest(), cat="电影", sub="华语", lib="库.json"))
            self.assertEqual(by_cat["library"]["totalFilesCount"], 1)
            status = asyncio.run(main.read_library_status())
            self.assertEqual(status["status"]["workCount"], 1)
            # 作品不存在 → 404
            with self.assertRaises(HTTPException):
                asyncio.run(main.read_library_files(_StubRequest(), dir="不存在"))

    def test_export_save_writes_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_dir, tmp)
        with unittest.mock.patch.object(main, "store", self.store):
            work_dir = self._seed_library()
            # 指定导出目录
            asyncio.run(main.write_library_config(main.LibraryConfigRequest(
                exportDir=str(tmp / "导出"),
            ), _StubRequest()))
            result = asyncio.run(main.save_library_export(
                main.LibraryExportSaveRequest(dir=work_dir, label="Demo"),
                _StubRequest(),
            ))
            self.assertTrue(result["ok"])
            self.assertEqual(result["totalFilesCount"], 1)
            self.assertTrue(result["file"].startswith("Demo-"))
            saved = Path(tmp) / "导出" / result["file"]
            self.assertTrue(saved.exists(), saved)
            payload = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(payload["totalFilesCount"], 1)
            self.assertTrue(payload["files"][0]["etag"])

            # 未配置导出目录时落在 DATA_DIR/秒传文件导出
            fake_data = Path(tempfile.mkdtemp())
            self.addCleanup(self._cleanup_dir, fake_data)
            blank_cfg = {"token": "", "transferIntervalMs": 200,
                         "transferConcurrency": 5, "exportDir": ""}
            with unittest.mock.patch.object(main, "DATA_DIR", fake_data), \
                    unittest.mock.patch.object(main, "_library_config", lambda: blank_cfg):
                fallback = asyncio.run(main.save_library_export(
                    main.LibraryExportSaveRequest(dir=work_dir, label="默认"), _StubRequest(),
                ))
            self.assertTrue((fake_data / "秒传文件导出" / fallback["file"]).exists())

            # includeFiles 过滤后为空 → 404
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.save_library_export(
                    main.LibraryExportSaveRequest(dir=work_dir, includeFiles=["不存在.mkv"]), _StubRequest(),
                ))
            self.assertEqual(ctx.exception.status_code, 404)

    def test_poster_cache_route(self):
        with unittest.mock.patch.object(main, "store", self.store):
            asyncio.run(main.write_library_config(main.LibraryConfigRequest(), _StubRequest()))
            self.store.write_value("moviePoster:555", {"url": "http://img/555.jpg"})
            poster = asyncio.run(main.library_poster(_StubRequest(), tmdbId=555, title="x", year=0, token=""))
            self.assertEqual(poster["url"], "http://img/555.jpg")

    def test_transfer_route_rejects_without_auth_client(self):
        with unittest.mock.patch.object(main, "store", self.store):
            work_dir = self._seed_library()
            with unittest.mock.patch.object(main, "_authorized_pan123_client", AsyncMock(side_effect=RuntimeError("未授权"))):
                with self.assertRaises(RuntimeError):
                    asyncio.run(main.start_library_transfer(
                        main.LibraryTransferRequest(dirs=[work_dir]),
                        _StubRequest(),
                    ))

    @staticmethod
    def _cleanup_dir(path: Path) -> None:
        import shutil
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class MovieLibraryStreamImportTests(unittest.TestCase):
    """巨型秒传 JSON 流式导入：分块解析与整读结果严格一致、跨批次作品聚合、去重跳过、错误回退。"""

    def _make_json_file(self, path: Path, common_path: str = "电影", count: int = 300) -> Path:
        """构造标准 123FastLink JSON：多作品、含 tmdb 标记/Season/海报，乱序穿插保证跨批次。"""
        files = []
        for i in range(count):
            work = f"作品{i % 7} (2018) {{tmdb-{1000 + i % 7}}}"
            files.append({"path": f"{work}/Season 1/E{i:03d}.mkv", "etag": hex_to_base62(_etag(i)), "size": 100 + i})
            if i % 3 == 0:
                files.append({"path": f"{work}/海报{i}.jpg", "etag": hex_to_base62(_etag(5000 + i)), "size": 9})
        payload = _fastlink_payload(common_path, files)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_stream_matches_full_parse(self):
        """分块极小（强制部分读取路径）时，流式条目与整读解析严格一致。"""
        with tempfile.TemporaryDirectory() as d:
            f = self._make_json_file(Path(d) / "库.json", common_path="电影")
            meta, entries = movie_library.open_library_stream(f, chunk_size=97)
            streamed = list(entries)
            self.assertEqual(meta["commonPath"], "电影")
            self.assertTrue(meta["usesBase62"])
            full = parse_library_content(f.read_text(encoding="utf-8"))
            self.assertEqual(meta["commonPath"], full["commonPath"])
            self.assertEqual(len(streamed), len(full["files"]))
            self.assertEqual(streamed, full["files"])

    def test_stream_array_root_and_commonpath_prefix(self):
        """[etag,size,path] 数组形态与相对路径拼回 commonPath。"""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "arr.json"
            f.write_text(json.dumps([[_etag(1), "100", "a.mkv"], [_etag(2), "200", "sub/b.mkv"]]), encoding="utf-8")
            meta, entries = movie_library.open_library_stream(f, chunk_size=11)
            files = list(entries)
            self.assertEqual(meta, {"commonPath": "", "usesBase62": False})
            self.assertEqual(files[0]["path"], "a.mkv")
            self.assertEqual(files[1]["path"], "sub/b.mkv")

    def test_stream_db_import_matches_payload_import(self):
        """流式入库与整读入库的库内容/统计完全一致（含跨批次、跨分块的作品聚合）。"""
        from app.movie_library_db import LibraryDb

        def rows_of(db_path: Path):
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                works = conn.execute(
                    "SELECT dir, title, file_count, video_count, total_size, source FROM library_works ORDER BY dir"
                ).fetchall()
                files = conn.execute(
                    "SELECT dir, path, file_name, etag, size, is_video FROM library_work_files ORDER BY dir, path"
                ).fetchall()
                sources = conn.execute(
                    "SELECT name, common_path, work_count, file_count, total_size FROM library_sources"
                ).fetchall()
                return works, files, sources
            finally:
                conn.close()

        with tempfile.TemporaryDirectory() as d:
            f = self._make_json_file(Path(d) / "库.json", common_path="电影", count=120)
            db_a = LibraryDb(Path(d) / "a.db")
            payload = movie_library.parse_library_content(f.read_text(encoding="utf-8"))
            r_payload = db_a.import_payload("库.json", payload)
            db_b = LibraryDb(Path(d) / "b.db")
            meta, entries = movie_library.open_library_stream(f, chunk_size=211)
            r_stream = db_b.import_stream("库.json", meta["commonPath"], entries, batch_size=13)
            self.assertEqual(r_payload["added"], r_stream["added"])
            self.assertEqual(r_payload["fileCount"], r_stream["fileCount"])
            self.assertEqual(r_payload["totalSize"], r_stream["totalSize"])
            self.assertEqual(rows_of(Path(d) / "a.db"), rows_of(Path(d) / "b.db"))

    def test_stream_db_skip_existing_and_reimport_updates(self):
        """skip 模式：dir 已存在整作品跳过（保留先入库的）；同名来源重导先清后插（可更新）。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            payload = _fastlink_payload("电影", [
                {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            ])
            db.import_payload("旧来源.json", payload)

            f = Path(d) / "库.json"
            f.write_text(json.dumps(_fastlink_payload("电影", [
                {"path": f"{WORK_A}/new.mkv", "fileName": "new.mkv", "etag": _etag(9), "size": 5},
                {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 200},
            ]), ensure_ascii=False), encoding="utf-8")
            meta, entries = movie_library.open_library_stream(f)
            r = db.import_stream("库.json", meta["commonPath"], entries, mode="skip")
            self.assertEqual(r["added"], 1)
            self.assertEqual(r["skipped"], 1)
            listing = db.list_files(WORK_A)
            self.assertEqual(listing["files"][0]["fileName"], "a.mkv", "已存在作品应保留先入库的文件")

            # 同名来源重导 → 先清后插（可更新）
            work_c = "剧集/美剧/Show (2020) {tmdb-3}"
            f2 = Path(d) / "库2.json"
            f2.write_text(json.dumps(_fastlink_payload("电影", [
                {"path": f"{work_c}/b2.mkv", "fileName": "b2.mkv", "etag": _etag(3), "size": 1},
            ]), ensure_ascii=False), encoding="utf-8")
            meta2, entries2 = movie_library.open_library_stream(f2)
            r2 = db.import_stream("库2.json", meta2["commonPath"], entries2, mode="skip")
            self.assertEqual(r2["added"], 1)
            total, _ = db.search("", 1, 20, libs=["库2.json"])
            self.assertEqual(total, 1)

            f3 = Path(d) / "库2.json"
            f3.write_text(json.dumps(_fastlink_payload("电影", [
                {"path": f"{work_c}/b2.mkv", "fileName": "b2.mkv", "etag": _etag(4), "size": 2},
            ]), ensure_ascii=False), encoding="utf-8")
            meta3, entries3 = movie_library.open_library_stream(f3)
            r3 = db.import_stream("库2.json", meta3["commonPath"], entries3, mode="skip")
            self.assertEqual(r3["added"], 1, "同名来源重导应先清后插、再次新增")
            listing = db.list_files(f"电影/{work_c}")
            self.assertEqual(listing["files"][0]["etag"], _etag(4), "重导后文件应为新版本")

    def test_stream_errors(self):
        """空 files / 缺 files / 截断 JSON / libraries 嵌套 → 各自明确报错或触发整读回退。"""
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "empty.json"
            empty.write_text(json.dumps({"commonPath": "x", "files": []}), encoding="utf-8")
            meta, entries = movie_library.open_library_stream(empty)
            with self.assertRaises(ValueError):
                list(entries)

            no_files = Path(d) / "nofiles.json"
            no_files.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                movie_library.open_library_stream(no_files)

            truncated = Path(d) / "trunc.json"
            truncated.write_text('{"commonPath":"x","files":[{"path":"a.mkv"', encoding="utf-8")
            meta, entries = movie_library.open_library_stream(truncated)
            with self.assertRaises(ValueError):
                list(entries)

            libs = Path(d) / "libs.json"
            libs.write_text(json.dumps({"libraries": [{"commonPath": "x", "files": []}]}), encoding="utf-8")
            with self.assertRaises(movie_library.LibraryFullParseFallback):
                movie_library.open_library_stream(libs)

    def test_video_extensions_config_and_import(self):
        """视频扩展名：默认放宽、自定义合并、导入 is_video/聚合计数生效、非影库文件跳过。"""
        from app.movie_library import normalize_video_extensions, video_ext_set
        from app.movie_library_db import LibraryDb

        # 清洗：逗号/分号/空格/中文顿号，带点不带点均可，去重去杂
        self.assertEqual(normalize_video_extensions("tp, MXF; m4v、 .dv  bad!"), (".tp", ".mxf", ".m4v", ".dv"))
        merged = video_ext_set("td5")
        self.assertIn(".mkv", merged)
        self.assertIn(".td5", merged)
        self.assertIn(".m4v", merged, "m4v 应已进默认集合")

        # 导入：默认不算视频的扩展名，配置自定义后计入 video_count / is_video
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            payload = _fastlink_payload("电影", [
                {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
                {"path": f"{WORK_A}/b.td5", "fileName": "b.td5", "etag": _etag(2), "size": 50},
            ])
            r_default = db.import_payload("默认.json", payload)
            self.assertEqual(r_default["added"], 1)
            listing_default = db.list_files(WORK_A)
            by_name = {row["fileName"]: row for row in listing_default["files"]}
            self.assertEqual(by_name["a.mkv"]["isVideo"], True)
            self.assertEqual(by_name["b.td5"]["isVideo"], False)

            ext = video_ext_set("td5")
            def entries():
                for f in payload["files"]:
                    yield f
            db2 = LibraryDb(Path(d) / "cloud123-2.db")
            r_custom = db2.import_stream("自定义.json", "电影", entries(), video_ext=ext)
            self.assertEqual(r_custom["added"], 1)
            listing_custom = db2.list_files(WORK_A)
            by_name2 = {row["fileName"]: row for row in listing_custom["files"]}
            self.assertEqual(by_name2["b.td5"]["isVideo"], True)
            total_rows, work_rows = db2.search("", 1, 20)
            self.assertEqual(len(work_rows), 1)
            self.assertEqual(work_rows[0]["count"], 2)
            self.assertEqual(work_rows[0]["videoCount"], 2, "自定义扩展名应计入视频数")

    def test_import_from_path_helper(self):
        """路由层 _import_library_from_path：JSON 流式、libraries 回退整读、V2 文本整读。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            with unittest.mock.patch.object(movie_library_db, "_default_db", db):
                big = self._make_json_file(Path(d) / "big.json", common_path="电影", count=90)
                r = main._import_library_from_path(str(big), "big.json")
                self.assertEqual(r["added"], 7)
                self.assertEqual(r["fileCount"], 120)

                libs = Path(d) / "libs.json"
                libs.write_text(json.dumps({"libraries": [
                    {"commonPath": "电影", "files": [
                        {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
                    ]},
                ]}, ensure_ascii=False), encoding="utf-8")
                r2 = main._import_library_from_path(str(libs), "libs.json")
                self.assertEqual(r2["added"], 1)

                text = Path(d) / "links.txt"
                text.write_text("123FSLinkV1$0123456789abcdef0123456789abcdef#7#dir/a.mkv", encoding="utf-8")
                r3 = main._import_library_from_path(str(text), "links.txt")
                self.assertEqual(r3["added"], 1)


class LibraryEnrichTests(unittest.TestCase):
    """影库分类充实（步骤2）：DB 队列方法 + 字段归一 + 路由（打桩免联网）。"""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        self.db = movie_library_db.LibraryDb(Path(self._directory.name) / "cloud123.db")
        movie_library_db._default_db = self.db

    def _seed(self):
        self.db.import_payload("库.json", _fastlink_payload("", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]))

    def test_pending_works_only_pending_with_id(self):
        self.db.import_payload("库.json", _fastlink_payload("", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            {"path": "电影/无名 2020/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 10},
        ]))
        rows = self.db.pending_works(50)
        self.assertEqual([r["dir"] for r in rows], [WORK_A])  # 无 tmdb 的不进队列
        self.assertEqual(rows[0]["tmdb_id"], 297802)
        stats = self.db.enrich_stats()
        self.assertEqual((stats["pending"], stats["none"]), (1, 1))
        self.assertEqual(stats["total"], 2)

    def test_apply_enrichment_writes_and_sets_ok(self):
        self._seed()
        self.db.apply_enrichment(WORK_A, {
            "media_type": "movie", "genres": ["动作", "科幻"], "region": "欧美",
            "poster_path": "https://image.tmdb.org/x.jpg", "vote_average": 7.5,
            "overview": "海底王国", "year": 2018,
        })
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            r = dict(conn.execute("SELECT * FROM library_works WHERE dir = ?", (WORK_A,)).fetchone())
        finally:
            conn.close()
        self.assertEqual(r["tmdb_status"], "ok")
        self.assertEqual(r["media_type"], "movie")
        self.assertEqual(json.loads(r["genres"]), ["动作", "科幻"])
        self.assertEqual(r["region"], "欧美")
        self.assertAlmostEqual(r["vote_average"], 7.5)
        self.assertEqual(self.db.enrich_stats()["ok"], 1)
        # 已 ok 不再进 pending
        self.assertEqual(self.db.pending_works(50), [])

    def test_mark_failure_threshold_then_reset(self):
        self._seed()
        self.db.mark_enrich_failure(WORK_A, max_attempts=3)
        self.assertEqual(self.db.enrich_stats()["pending"], 1)  # 第 1 次仍 pending
        self.db.mark_enrich_failure(WORK_A, max_attempts=3)
        self.db.mark_enrich_failure(WORK_A, max_attempts=3)   # 第 3 次 → failed
        stats = self.db.enrich_stats()
        self.assertEqual((stats["failed"], stats["pending"]), (1, 0))
        # 只重排 failed → 回到 pending，attempts 归零
        self.assertEqual(self.db.reset_enrichment(only_failed=True), 1)
        self.assertEqual(self.db.enrich_stats()["pending"], 1)
        self.assertEqual(self.db.pending_works(50)[0]["tmdb_id"], 297802)

    def test_reset_refresh_all_requeues_ok(self):
        self._seed()
        self.db.apply_enrichment(WORK_A, {"media_type": "movie", "genres": [], "region": "欧美",
                                          "poster_path": "", "vote_average": 0, "overview": "", "year": 2018})
        self.assertEqual(self.db.reset_enrichment(only_failed=True), 0)  # 只重排 failed，ok 不动
        self.assertEqual(self.db.reset_enrichment(only_failed=False), 1)  # 全量重排
        self.assertEqual(self.db.enrich_stats()["pending"], 1)

    def test_tmdb_enrich_fields_normalize(self):
        info = {
            "title": "Aquaman", "release_date": "2018-12-07", "overview": "o",
            "vote_average": 6.8, "poster_path": "/abc.jpg", "origin_country": ["US", "CN"],
            "genres": [{"id": 1, "name": "动作"}, {"id": 2, "name": "科幻"}, {"id": 3}],
        }
        fields = main._tmdb_enrich_fields(info, "movie")
        self.assertEqual(fields["media_type"], "电影")  # media_type 存中文频道
        self.assertEqual(fields["genres"], ["动作", "科幻"])
        self.assertEqual(fields["region"], "欧美")  # 首个命中 US
        self.assertEqual(fields["year"], 2018)
        self.assertEqual(fields["poster_path"], "https://image.tmdb.org/t/p/w185/abc.jpg")
        self.assertAlmostEqual(fields["vote_average"], 6.8)

    def test_tmdb_enrich_channel_buckets(self):
        """频道六分：动画→动漫、纪录→纪录片（电影剧集都适用），剧集按 儿童/综艺 细分。"""
        cases = [
            ({"genres": [{"id": 16, "name": "动画"}]}, "tv", "动漫"),
            ({"genres": [{"id": 16, "name": "动画"}]}, "movie", "动漫"),
            ({"genres": [{"id": 99, "name": "纪录"}]}, "tv", "纪录片"),
            ({"genres": [{"id": 99, "name": "Documentary"}]}, "movie", "纪录片"),
            ({"genres": [{"id": 10762, "name": "儿童"}, {"id": 10759, "name": "动作冒险"}]}, "tv", "儿童"),
            ({"genres": [{"id": 10764, "name": "真人秀"}]}, "tv", "综艺"),
            ({"genres": [{"id": 10767, "name": "脱口秀"}]}, "tv", "综艺"),
            ({"genres": [{"id": 18, "name": "剧情"}]}, "tv", "电视剧"),
            ({"genres": [{"id": 28, "name": "动作"}]}, "movie", "电影"),
            ({"genres": []}, "tv", "电视剧"),
            ({"genres": []}, "movie", "电影"),
        ]
        for info, matched, expected in cases:
            fields = main._tmdb_enrich_fields(info, matched)
            self.assertEqual(fields["media_type"], expected, (matched, info))

    def test_infer_technical_detailed(self):
        """细粒度属性识别（对齐油猴整理字段）：资源类型/DV/HDR/编码/帧率/地区版。"""
        tech = movie_library.infer_technical_detailed([
            "Show.S01E01.UHD.BluRay.REMUX.2160p.HEVC.DTS-HD.MA.7.1.DoVi.HDR10.mkv",
            "Show.S01E02.2160p.WEB-DL.H265.DDP.5.1.HDR10+.mkv",
        ])
        self.assertEqual(tech["resourceType"], "UHD BluRay Remux")  # 优先级高于 WEB-DL
        self.assertEqual(tech["dolbyVision"], "DV")
        self.assertEqual(tech["dynamicRange"], "HDR10+")  # HDR10+ 优先于 HDR10
        self.assertEqual(tech["videoCodec"], "HEVC")
        self.assertEqual(tech["audioCodec"], "DTS.HD.MA")  # 优先级高于 DDP
        self.assertEqual(tech["frameRate"], "")

        tech2 = movie_library.infer_technical_detailed([
            "Movie.2160p.UHD.BluRay.H.265.23.976fps.TrueHD.IMAX.mkv",
            "Movie.REMUX.1080p.AVC.25fps.REPACK.mkv",
        ])
        self.assertEqual(tech2["resourceType"], "Remux")  # REMUX 档位高于 UHD BluRay
        self.assertEqual(tech2["videoCodec"], "H265")  # H.265 → H265；作品级取优先级最高（H265 > AVC）
        self.assertEqual(tech2["audioCodec"], "TrueHD")
        self.assertEqual(tech2["frameRate"], "23.976fps")
        self.assertIn("IMAX", tech2["originalEdition"])
        self.assertIn("REPACK", tech2["originalEdition"])

    def test_infer_technical_detailed_frame_rate_fix(self):
        """帧率修复：H.265.25fps 的点分版本号不并进数字（265.25fps → 25fps）。"""
        tech = movie_library.infer_technical_detailed(["Show.S01E01.H.265.25fps.1080p.mkv"])
        self.assertEqual(tech["frameRate"], "25fps")
        tech2 = movie_library.infer_technical_detailed(["Show.23.976fps.mkv"])
        self.assertEqual(tech2["frameRate"], "23.976fps")
        tech3 = movie_library.infer_technical_detailed(["Show.1080p.mkv"])
        self.assertEqual(tech3["frameRate"], "")

    def test_infer_technical_remux_resolution_gap(self):
        """REMUX 记号隔分辨率段：BluRay.1080p.Remux 补升 BluRay Remux；纯 Remux 不误升。"""
        self.assertEqual(
            movie_library.infer_technical_detailed(["Movie.2019.BluRay.1080p.Remux.AVC.FLAC.2.0-GRP.mkv"])["resourceType"],
            "BluRay Remux",
        )
        self.assertEqual(
            movie_library.infer_technical_detailed(["Movie.2019.1080p.BluRay.Remux.AVC.FLAC.2.0-GRP.mkv"])["resourceType"],
            "BluRay Remux",
        )
        self.assertEqual(
            movie_library.infer_technical_detailed(["Movie.2019.UHD.BluRay.2160p.Remux.HEVC-GRP.mkv"])["resourceType"],
            "UHD BluRay Remux",
        )
        self.assertEqual(
            movie_library.infer_technical_detailed(["Movie.2019.BD.1080p.Remux.AVC-GRP.mkv"])["resourceType"],
            "BluRay Remux",
        )
        self.assertEqual(
            movie_library.infer_technical_detailed(["Movie.2019.1080p.Remux.H.264-GRP.mkv"])["resourceType"],
            "Remux",
        )

    def test_channel_from_stored_migration(self):
        """旧数据（media_type=movie/tv + genres 中文名）回填中文频道。"""
        from app.movie_library import channel_from_stored
        cases = [
            ("movie", ["动画", "科幻"], "动漫"),
            ("tv", ["动画"], "动漫"),
            ("movie", ["纪录"], "纪录片"),
            ("tv", ["儿童"], "儿童"),
            ("tv", ["真人秀"], "综艺"),
            ("tv", ["剧情"], "电视剧"),
            ("movie", ["剧情"], "电影"),
            ("tv", [], "电视剧"),
            ("movie", [], "电影"),
        ]
        for matched, genres, expected in cases:
            self.assertEqual(channel_from_stored(matched, genres), expected, (matched, genres))

    def test_media_type_channel_migration_on_open(self):
        """旧库打开（触发 _migrate）时 media_type 的 movie/tv 自动回填成中文频道，幂等。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "old.db"
            db = LibraryDb(db_path)
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{WORK_A}/v.mkv", "fileName": "v.mkv", "etag": _etag(1), "size": 1},
            ]))
            db.apply_enrichment(WORK_A, {"media_type": "movie", "genres": ["动画", "科幻"]})
            LibraryDb(db_path)  # 重新打开触发迁移
            conn = __import__("sqlite3").connect(db_path)
            try:
                value = conn.execute("SELECT media_type FROM library_works WHERE dir = ?", (WORK_A,)).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(value, "动漫")

    def test_enrich_status_route(self):
        original_store = main.store
        self.addCleanup(setattr, main, "store", original_store)
        from app.session_store import SessionStore
        main.store = SessionStore(Path(self._directory.name))
        self._seed()
        result = asyncio.run(main.read_library_enrich_status(_StubRequest(), token=""))
        self.assertTrue(result["ok"])
        self.assertEqual(result["stats"]["pending"], 1)

    def test_enrich_start_route_applies_stubbed_lookup(self):
        original_store = main.store
        self.addCleanup(setattr, main, "store", original_store)
        from app.session_store import SessionStore
        main.store = SessionStore(Path(self._directory.name))
        self._seed()
        fields = {"media_type": "movie", "genres": ["动作"], "region": "欧美",
                  "poster_path": "", "vote_average": 7.0, "overview": "", "year": 2018}
        with unittest.mock.patch.object(main, "_tmdb_enrich_lookup", AsyncMock(return_value=fields)):
            result = asyncio.run(main.start_library_enrich(main.LibraryTokenRequest(), _StubRequest()))
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["stats"]["ok"], 1)

    def _switch_store(self):
        original_store = main.store
        self.addCleanup(setattr, main, "store", original_store)
        from app.session_store import SessionStore
        main.store = SessionStore(Path(self._directory.name))
        return main.store

    def test_lookup_prefers_type_and_early_stops(self):
        """先查偏好类型；首查命中且标题匹配（评分≥2）就不再查另一个类型；结果进 KV 缓存。"""
        store = self._switch_store()
        calls = []

        async def fake_fetch(type_, tmdb_id):
            calls.append(type_)
            if type_ == "tv":
                return {"name": "海王", "first_air_date": "2018-01-01",
                        "genres": [{"id": 18, "name": "剧情"}], "popularity": 10}
            return None

        with unittest.mock.patch.object(main, "_tmdb_fetch_info", fake_fetch):
            fields = asyncio.run(main._tmdb_enrich_lookup(297802, "海王", 2018, prefer_type="tv"))
        self.assertEqual(calls, ["tv"], "首查命中且标题匹配就不再查第二个类型")
        self.assertEqual(fields["media_type"], "电视剧")
        # 第二次：同样参数走 KV 缓存，不再发请求
        fields2 = asyncio.run(main._tmdb_enrich_lookup(297802, "海王", 2018, prefer_type="movie"))
        self.assertEqual(calls, ["tv"])
        self.assertIsNotNone(fields2)
        self.assertTrue(store.read_value(main._enrich_cache_key("zh-CN", 297802)))

    def test_lookup_falls_back_when_first_type_mismatches(self):
        """首查标题对不上（评分<2）继续查另一类型择优，不会错配。"""
        self._switch_store()  # 隔离 KV 缓存，不能读真实库
        calls = []

        async def fake_fetch(type_, tmdb_id):
            calls.append(type_)
            if type_ == "tv":
                return {"name": "完全不同的剧", "first_air_date": "2020-01-01", "genres": []}
            return {"title": "海王", "release_date": "2018-12-07", "genres": [{"id": 28, "name": "动作"}]}

        with unittest.mock.patch.object(main, "_tmdb_fetch_info", fake_fetch):
            fields = asyncio.run(main._tmdb_enrich_lookup(297802, "海王", 2018, prefer_type="tv"))
        self.assertEqual(calls, ["tv", "movie"], "首查不匹配应继续查 movie")
        self.assertEqual(fields["media_type"], "电影")

    def test_search_lookup_requires_title_match(self):
        """无标记选配：搜索结果必须标题匹配（评分≥2）才采纳；完全不相干不返回 id。"""

        class _FakeResponse:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, payload):
                self._payload = payload

            async def get(self, url, headers=None):
                return _FakeResponse(self._payload)

        with unittest.mock.patch.object(main, "_tmdb_http", lambda: _FakeClient(
            {"results": [{"id": 42, "name": "海王", "first_air_date": "2018-01-01"}]}
        )):
            matched = asyncio.run(main._tmdb_search_lookup("海王", 2018))
        self.assertEqual(matched, 42)

        with unittest.mock.patch.object(main, "_tmdb_http", lambda: _FakeClient(
            {"results": [{"id": 43, "name": "完全无关的剧", "first_air_date": "2018-01-01"}]}
        )):
            matched = asyncio.run(main._tmdb_search_lookup("海王", 2018))
        self.assertIsNone(matched, "标题对不上的搜索结果不采纳")

    def test_prefer_type_heuristic(self):
        self.assertEqual(main._prefer_tmdb_type({"video_count": 5, "dir": "电影/海王"}), "tv")
        self.assertEqual(main._prefer_tmdb_type({"video_count": 1, "dir": "剧集/Show/Season 1"}), "tv")
        self.assertEqual(main._prefer_tmdb_type({"video_count": 1, "dir": "电影/海王 (2018)"}), "movie")


W_MOVIE_1 = "合集/甲 (2018) {tmdb-11}"
W_MOVIE_2 = "合集/乙 (2019) {tmdb-12}"
W_TV_1 = "合集/丙 (2021) {tmdb-13}"
W_NOID = "合集/丁 2020"


class LibraryFacetsTests(unittest.TestCase):
    """影库分类维度筛选与交叉计数（步骤3）。"""

    def setUp(self):
        from app.movie_library_db import LibraryDb
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db = LibraryDb(Path(self._directory.name) / "cloud123.db")
        self.db.import_payload("库.json", _fastlink_payload("", [
            {"path": f"{W_MOVIE_1}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 10},
            {"path": f"{W_MOVIE_2}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 10},
            {"path": f"{W_TV_1}/c.mkv", "fileName": "c.mkv", "etag": _etag(3), "size": 10},
            {"path": f"{W_NOID}/d.mkv", "fileName": "d.mkv", "etag": _etag(4), "size": 10},
        ]))
        self.db.apply_enrichment(W_MOVIE_1, {"media_type": "movie", "genres": ["动作", "科幻"],
                                             "region": "欧美", "vote_average": 7.0, "year": 2018})
        self.db.apply_enrichment(W_MOVIE_2, {"media_type": "movie", "genres": ["科幻"],
                                             "region": "华语", "vote_average": 9.0, "year": 2019})
        self.db.apply_enrichment(W_TV_1, {"media_type": "tv", "genres": ["动作"],
                                          "region": "日韩", "vote_average": 5.0, "year": 2021})

    def _names(self, items):
        return {i["name"]: i["count"] for i in items}

    def test_facets_counts(self):
        f = self.db.facets()
        self.assertEqual(self._names(f["channels"]), {"movie": 2, "tv": 1})
        self.assertEqual(self._names(f["genres"]), {"动作": 2, "科幻": 2})
        self.assertEqual(self._names(f["regions"]), {"欧美": 1, "华语": 1, "日韩": 1})
        self.assertEqual(self._names(f["decades"]), {2010: 2, 2020: 1})

    def test_facets_cross_filter_narrows_others(self):
        # 选电影频道：类型/地区/年代按电影作品收窄，频道自身不变
        f = self.db.facets(media_type="movie")
        self.assertEqual(self._names(f["channels"]), {"movie": 2, "tv": 1})  # 自身不收窄
        self.assertEqual(self._names(f["genres"]), {"动作": 1, "科幻": 2})   # 丙(tv 的动作)被排除
        self.assertEqual(self._names(f["regions"]), {"欧美": 1, "华语": 1})
        self.assertEqual(self._names(f["decades"]), {2010: 2})

    def test_search_by_dimension(self):
        total, rows = self.db.search("", 1, 20, media_type="movie")
        self.assertEqual(total, 2)
        total, rows = self.db.search("", 1, 20, genre="动作")
        self.assertEqual({r["dir"] for r in rows}, {W_MOVIE_1, W_TV_1})
        total, _ = self.db.search("", 1, 20, region="日韩")
        self.assertEqual(total, 1)
        total, _ = self.db.search("", 1, 20, decade=2020)  # 2020-2029
        self.assertEqual(total, 1)

    def test_search_sort_and_enriched_fields(self):
        total, rows = self.db.search("", 1, 20, sort="rating")
        self.assertEqual([r["dir"] for r in rows][:2], [W_MOVIE_2, W_MOVIE_1])  # 9.0 → 7.0
        movie = {r["dir"]: r for r in rows}
        self.assertEqual(movie[W_MOVIE_1]["mediaType"], "movie")
        self.assertEqual(movie[W_MOVIE_1]["genres"], ["动作", "科幻"])
        self.assertEqual(movie[W_MOVIE_1]["region"], "欧美")
        self.assertAlmostEqual(movie[W_MOVIE_2]["voteAverage"], 9.0)
        # 无 tmdb 的作品分类字段留空、状态 none
        _, norows = self.db.search("丁", 1, 20)
        self.assertEqual(norows[0]["tmdbStatus"], "none")
        self.assertEqual(norows[0]["genres"], [])

    def test_facets_and_search_routes(self):
        original_store = main.store
        self.addCleanup(setattr, main, "store", original_store)
        from app.session_store import SessionStore
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(Path(self._directory.name) / "cloud123.db")
        main.store = SessionStore(Path(self._directory.name))
        facets = asyncio.run(main.read_library_facets(_StubRequest()))
        self.assertEqual(self._names(facets["facets"]["channels"]), {"电影": 2, "电视剧": 1})  # media_type 存中文频道
        result = asyncio.run(main.search_library(_StubRequest(), mediaType="电视剧", page=1, size=20))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["dirs"][0]["dir"], W_TV_1)
        # 频道搜索按中文名（新值），旧的 movie/tv 值迁移后不再命中


class LibraryDimTests(unittest.TestCase):
    """分类维度增量：语言/完结/画质版本/评分区间/热度。"""

    def test_normalize_language(self):
        self.assertEqual(movie_library.normalize_language("zh"), "中文")
        self.assertEqual(movie_library.normalize_language("en"), "英语")
        self.assertEqual(movie_library.normalize_language("JA"), "日语")
        self.assertEqual(movie_library.normalize_language("xx"), "其他")
        self.assertEqual(movie_library.normalize_language(""), "")
        self.assertEqual(movie_library.normalize_language(None), "")

    def test_normalize_air_status(self):
        self.assertEqual(movie_library.normalize_air_status("movie", {"status": "Released"}), "")
        self.assertEqual(movie_library.normalize_air_status("tv", {"in_production": True}), "更新中")
        self.assertEqual(movie_library.normalize_air_status("tv", {"status": "Ended"}), "已完结")
        self.assertEqual(movie_library.normalize_air_status("tv", {"status": "Returning Series"}), "更新中")
        self.assertEqual(movie_library.normalize_air_status("tv", {"status": "Canceled"}), "已停更")
        self.assertEqual(movie_library.normalize_air_status("tv", {"status": "Planned"}), "未开播")
        self.assertEqual(movie_library.normalize_air_status("tv", {}), "")

    def test_infer_technical(self):
        self.assertEqual(movie_library.infer_technical(["Movie.2160p.x265.mkv", "Movie.1080p.mkv"]), ("4K", ""))
        self.assertEqual(movie_library.infer_technical(["S01E01.1080p.WEB-DL.mkv", "x.720p.HDTV.mkv"]), ("1080p", "WEB-DL"))
        self.assertEqual(movie_library.infer_technical(["a.2160p.REMUX.mkv", "b.1080p.BluRay.mkv"]), ("4K", "REMUX"))
        self.assertEqual(movie_library.infer_technical(["纯中文无标记.mkv"]), ("", ""))
        self.assertEqual(movie_library.infer_technical([]), ("", ""))

    def test_import_writes_resolution_edition(self):
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{WORK_A}/Movie.2160p.x265.mkv", "fileName": "Movie.2160p.x265.mkv", "etag": _etag(1), "size": 100},
                {"path": f"{WORK_A}/nfo.txt", "fileName": "nfo.txt", "etag": _etag(2), "size": 5},
            ]))
            _, rows = db.search("", 1, 20)
            self.assertEqual(rows[0]["resolution"], "4K")
            self.assertEqual(rows[0]["edition"], "")

    def test_import_stream_writes_tech(self):
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            entries = [
                {"path": f"{WORK_A}/Ep01.1080p.WEB-DL.mkv", "fileName": "Ep01.1080p.WEB-DL.mkv", "etag": _etag(1), "size": 100},
                {"path": f"{WORK_A}/Ep02.720p.HDTV.mkv", "fileName": "Ep02.720p.HDTV.mkv", "etag": _etag(2), "size": 50},
            ]
            r = db.import_stream("库.json", "", entries)
            self.assertEqual(r["added"], 1)
            _, rows = db.search("", 1, 20)
            self.assertEqual(rows[0]["resolution"], "1080p")  # 取最高
            self.assertEqual(rows[0]["edition"], "WEB-DL")    # 最高优先级

    def test_apply_enrichment_persists_new_fields(self):
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            ]))
            db.apply_enrichment(WORK_A, {
                "media_type": "tv", "genres": ["科幻"], "region": "日韩", "poster_path": "",
                "vote_average": 8.5, "overview": "", "year": 2021,
                "language": "日语", "air_status": "已完结", "popularity": 123.4,
            })
            _, rows = db.search("", 1, 20)
            w = rows[0]
            self.assertEqual(w["language"], "日语")
            self.assertEqual(w["airStatus"], "已完结")
            self.assertAlmostEqual(w["popularity"], 123.4)

    def test_search_rating_and_popularity(self):
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            dirs = ["合集/高 (2020) {tmdb-201}", "合集/中 (2020) {tmdb-202}", "合集/低 (2020) {tmdb-203}"]
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{dirs[0]}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 1},
                {"path": f"{dirs[1]}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 1},
                {"path": f"{dirs[2]}/c.mkv", "fileName": "c.mkv", "etag": _etag(3), "size": 1},
            ]))
            db.apply_enrichment(dirs[0], {"media_type": "movie", "genres": [], "region": "欧美",
                                          "poster_path": "", "vote_average": 9.2, "overview": "", "popularity": 5})
            db.apply_enrichment(dirs[1], {"media_type": "movie", "genres": [], "region": "欧美",
                                          "poster_path": "", "vote_average": 7.5, "overview": "", "popularity": 99})
            db.apply_enrichment(dirs[2], {"media_type": "movie", "genres": [], "region": "欧美",
                                          "poster_path": "", "vote_average": 6.0, "overview": "", "popularity": 50})
            total, _ = db.search("", 1, 20, rating=8)  # vote_average >= 8
            self.assertEqual(total, 1)
            total, rows = db.search("", 1, 20, rating=7)
            self.assertEqual(total, 2)
            _, rows = db.search("", 1, 20, sort="popularity")
            self.assertEqual(rows[0]["dir"], dirs[1])  # popularity 99 最高
            f = db.facets()
            self.assertEqual({i["name"]: i["count"] for i in f["ratings"]}, {9: 1, 8: 1, 7: 2})

    def test_tech_filter_and_facets(self):
        """tech 列：导入写入、旧库迁移回填、tech 筛选与特效/编码/音轨 facets 计数。"""
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            db = LibraryDb(db_path)
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": "电影/A/A (2020) {tmdb-1}/A.UHD.BluRay.REMUX.2160p.DoVi.HDR10.HEVC.TrueHD.23.976fps.mkv",
                 "fileName": "A.UHD.BluRay.REMUX.2160p.DoVi.HDR10.HEVC.TrueHD.23.976fps.mkv", "etag": _etag(1), "size": 1},
                {"path": "电影/B/B (2021) {tmdb-2}/B.2160p.WEB-DL.H.265.DDP.HDR10+.mkv",
                 "fileName": "B.2160p.WEB-DL.H.265.DDP.HDR10+.mkv", "etag": _etag(2), "size": 2},
            ]))
            # tech 列已随导入写入
            row = db.search("", 1, 50, tech="dolbyVision:DV")[1]
            self.assertEqual(len(row), 1)
            self.assertEqual(row[0]["dir"], "电影/A/A (2020) {tmdb-1}")
            # HDR 是 HDR10/HDR10+ 的前缀：键值精确匹配不会误伤
            self.assertEqual(len(db.search("", 1, 50, tech="dynamicRange:HDR")[1]), 0)
            self.assertEqual(len(db.search("", 1, 50, tech="dynamicRange:HDR10+")[1]), 1)
            self.assertEqual(len(db.search("", 1, 50, tech="videoCodec:H265")[1]), 1)
            self.assertEqual(len(db.search("", 1, 50, tech="audioCodec:TrueHD")[1]), 1)
            self.assertEqual(len(db.search("", 1, 50, tech="originalEdition:IMAX")[1]), 0)
            f = db.facets()
            self.assertEqual({i["name"]: i["count"] for i in f["effects"]},
                             {"DV": 1, "HDR10": 1, "HDR10+": 1})
            self.assertEqual({i["name"]: i["count"] for i in f["videoCodecs"]}, {"HEVC": 1, "H265": 1})
            self.assertEqual({i["name"]: i["count"] for i in f["audioCodecs"]}, {"TrueHD": 1, "DDP": 1})
            # 交叉筛选：选中 HDR10+ 后特效行隐藏同字段（dynamicRange）全部选项，只剩 DV
            f2 = db.facets(tech="dynamicRange:HDR10+")
            self.assertEqual({i["name"]: i["count"] for i in f2["effects"]}, {"DV": 1})
            self.assertEqual(f2["videoCodecs"][0]["count"], 1)

            # 旧库（tech 为空）重开触发迁移回填
            import sqlite3 as _sq
            conn = _sq.connect(db_path)
            conn.execute("UPDATE library_works SET tech = ''")
            conn.commit()
            conn.close()
            LibraryDb(db_path)
            self.assertEqual(len(db.search("", 1, 50, tech="dolbyVision:DV")[1]), 1)

    def test_facets_new_dimensions(self):
        from app.movie_library_db import LibraryDb
        with tempfile.TemporaryDirectory() as d:
            db = LibraryDb(Path(d) / "cloud123.db")
            w1 = "合集/甲 (2018) {tmdb-11}"
            w2 = "合集/乙 (2019) {tmdb-12}"
            db.import_payload("库.json", _fastlink_payload("", [
                {"path": f"{w1}/Movie.2160p.BluRay.mkv", "fileName": "Movie.2160p.BluRay.mkv", "etag": _etag(1), "size": 1},
                {"path": f"{w2}/Show.1080p.WEB-DL.mkv", "fileName": "Show.1080p.WEB-DL.mkv", "etag": _etag(2), "size": 1},
            ]))
            db.apply_enrichment(w1, {"media_type": "movie", "genres": ["动作"], "region": "欧美",
                                     "poster_path": "", "vote_average": 8.2, "overview": "", "year": 2018,
                                     "language": "英语", "air_status": "", "popularity": 10})
            db.apply_enrichment(w2, {"media_type": "tv", "genres": ["剧情"], "region": "华语",
                                     "poster_path": "", "vote_average": 9.0, "overview": "", "year": 2019,
                                     "language": "中文", "air_status": "已完结", "popularity": 20})
            f = db.facets()
            self.assertEqual({i["name"]: i["count"] for i in f["languages"]}, {"英语": 1, "中文": 1})
            self.assertEqual(f["statuses"], [])  # 「更新中/已完结」维度已下线
            self.assertEqual({i["name"]: i["count"] for i in f["resolutions"]}, {"4K": 1, "1080p": 1})
            self.assertEqual({i["name"]: i["count"] for i in f["editions"]}, {"BluRay": 1, "WEB-DL": 1})
            # 选电影频道：语言收窄到英语、地区欧美；状态（仅 tv）变空
            f2 = db.facets(media_type="movie")
            self.assertEqual({i["name"]: i["count"] for i in f2["languages"]}, {"英语": 1})
            self.assertEqual(f2["statuses"], [])
            # 选分辨率 4K：只有甲，其它维度随之收窄，分辨率自身仍是全集
            f3 = db.facets(resolution="4K")
            self.assertEqual({i["name"]: i["count"] for i in f3["resolutions"]}, {"4K": 1, "1080p": 1})
            self.assertEqual({i["name"]: i["count"] for i in f3["editions"]}, {"BluRay": 1})
