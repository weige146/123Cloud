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

            # 来源 B：另一作品 + 与 A 完全相同的 dir（应跳过）
            payload_b = _fastlink_payload("", [
                {"path": f"{WORK_B}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 200},
                {"path": f"{WORK_A}/dup.mkv", "fileName": "dup.mkv", "etag": _etag(9), "size": 999},
            ])
            r = db.import_payload("库B.json", payload_b)
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

            # 同来源重导 → 更新（先删后插）
            payload_a2 = _fastlink_payload(WORK_A + "/", [
                {"path": "new.mkv", "fileName": "new.mkv", "etag": _etag(8), "size": 5},
            ])
            db.import_payload("库A.json", payload_a2)
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
            # 同一来源再次导入 → 先清后插（可更新），作品数不叠加
            again = asyncio.run(main.import_library_paths(
                main.LibraryImportPathsRequest(paths=[str(tmp / "A.json")]), _StubRequest(),
            ))
            self.assertEqual(again["added"], 1)
            self.assertEqual(again["skipped"], 0)
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
