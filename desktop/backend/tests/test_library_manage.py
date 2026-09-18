"""影库数据管理测试：海报墙多选删除 / 整分类删除 / 数据库备份导出与恢复导入。

删除语义：只动本地数据库（works/files/playback 级联），不碰网盘与源 JSON。
备份语义：四张表原样导出成独立 sqlite；恢复时作品已存在跳过、播放记录保留较新的一条。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app import main, movie_library_db

WORK_TV = "剧集/欧美/权力的游戏 (2011) {tmdb-1399}"
WORK_TV2 = "剧集/欧美/绝命毒师 (2008) {tmdb-1396}"
WORK_MOVIE = "电影/外语/海王 (2018) {tmdb-297802}"


def _etag(i: int) -> str:
    return f"{i:032x}"


def _payload(files) -> dict:
    return {"commonPath": "", "usesBase62EtagsInExport": False,
            "files": [{"path": p, "etag": e, "size": s} for p, e, s in files]}


def _seed(db=movie_library_db, source: str = "库.json"):
    db.import_payload(source, _payload([
        (f"{WORK_TV}/Season 1/GoT.S01E01.mkv", _etag(1), 100),
        (f"{WORK_TV}/Season 1/GoT.S01E02.mkv", _etag(2), 200),
        (f"{WORK_TV2}/S01E01.mkv", _etag(3), 300),
        (f"{WORK_MOVIE}/Aquaman.2018.2160p.mkv", _etag(5), 500),
        (f"{WORK_MOVIE}/poster.jpg", _etag(6), 5),
    ]))


def _all_rows(db_path: Path, table: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = None
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    finally:
        conn.close()


class LibraryDeleteTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(Path(self._directory.name) / "cloud123.db")
        _seed()

    def test_delete_works_cascades(self):
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, cloud_file_id=11)
        movie_library_db.upsert_playback(WORK_MOVIE, f"{WORK_MOVIE}/Aquaman.2018.2160p.mkv", 0, 0, cloud_file_id=12)
        result = movie_library_db.delete_works([WORK_TV, WORK_MOVIE])
        self.assertEqual(result["works"], 2)
        self.assertEqual(result["files"], 4)  # GoT 两个 + 海王视频与海报
        self.assertEqual(result["playback"], 2)
        remaining = {w["dir"] for w in movie_library_db.search("", 1, 50)[1]}
        self.assertEqual(remaining, {WORK_TV2})
        self.assertEqual(movie_library_db.list_playback(WORK_TV), [])
        self.assertEqual(movie_library_db.delete_works([]), {"works": 0, "files": 0, "playback": 0})

    def test_delete_category_with_and_without_sub(self):
        result = movie_library_db.delete_category("剧集", "欧美")
        self.assertEqual(result["works"], 2)
        remaining = {w["dir"] for w in movie_library_db.search("", 1, 50)[1]}
        self.assertEqual(remaining, {WORK_MOVIE})
        # 空 / 「全部文件」这种合成分类不允许删
        with self.assertRaises(ValueError):
            movie_library_db.delete_category("")
        with self.assertRaises(ValueError):
            movie_library_db.delete_category("全部文件")


class LibraryBackupTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(Path(self._directory.name) / "a.db")
        _seed()
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1,
                                         cloud_file_id=11, position_sec=120, duration_sec=600)
        movie_library_db.apply_enrichment(WORK_TV, {"media_type": "tv", "genres": ["奇幻"], "poster_path": "/x.jpg"})

    def _export(self) -> Path:
        backup_path = Path(self._directory.name) / "备份.db"
        counts = movie_library_db.export_backup(str(backup_path))
        self.assertEqual(counts["library_works"], 3)
        self.assertEqual(counts["library_work_files"], 5)
        self.assertEqual(counts["library_playback"], 1)
        self.assertEqual(counts["library_sources"], 1)
        return backup_path

    def _switch_to_fresh_db(self) -> Path:
        fresh = Path(self._directory.name) / "fresh.db"
        movie_library_db._default_db = None
        movie_library_db.init(fresh)
        return fresh

    def test_export_import_roundtrip_fresh_db(self):
        backup_path = self._export()
        source_db = Path(self._directory.name) / "a.db"
        fresh = self._switch_to_fresh_db()
        result = movie_library_db.import_backup(str(backup_path))
        self.assertEqual(result["library_works"], 3)
        self.assertEqual(result["library_works_skipped"], 0)
        self.assertEqual(result["library_work_files"], 5)
        self.assertEqual(result["library_playback"], 1)
        # 逐表逐行与原库一致（TMDB 整理结果、播放记录都在）
        for table in ("library_sources", "library_works", "library_work_files", "library_playback"):
            self.assertEqual(_all_rows(source_db, table), _all_rows(fresh, table), table)
        record = movie_library_db.get_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv")
        self.assertEqual(record["cloudFileId"], 11)
        self.assertEqual(record["positionSec"], 120)

    def test_import_skips_existing_and_keeps_newer_playback(self):
        backup_path = self._export()
        fresh = self._switch_to_fresh_db()
        # 本地已有一个同名作品（保留先入库的）与两条播放记录：
        # E01 本地更新（不应被备份覆盖）、E02 备份更新（本地记录应被覆盖）
        movie_library_db.import_payload("本地.json", _payload([
            (f"{WORK_TV}/Season 1/GoT.S01E01.mkv", _etag(1), 100),
        ]))
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, cloud_file_id=999)
        self.assertEqual(len(movie_library_db.list_playback(WORK_TV)), 1)
        result = movie_library_db.import_backup(str(backup_path))
        self.assertEqual(result["library_works"], 2)          # 只进了另外两个作品
        self.assertEqual(result["library_works_skipped"], 1)  # 已存在的跳过
        works = {w["dir"] for w in movie_library_db.search("", 1, 50)[1]}
        self.assertEqual(works, {WORK_TV, WORK_TV2, WORK_MOVIE})
        # 备份里的 E01 记录 updated_at 早于本地 → 本地保留（cloudFileId 999 不动）
        record = movie_library_db.get_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv")
        self.assertEqual(record["cloudFileId"], 999)

    def test_import_rejects_bad_file(self):
        junk = Path(self._directory.name) / "junk.db"
        junk.write_text("not a sqlite file", encoding="utf-8")
        with self.assertRaises(ValueError):
            movie_library_db.import_backup(str(junk))
        with self.assertRaises(ValueError):
            movie_library_db.import_backup(str(Path(self._directory.name) / "不存在.db"))
        empty_sqlite = Path(self._directory.name) / "empty.db"
        sqlite3.connect(empty_sqlite).close()
        with self.assertRaises(ValueError):
            movie_library_db.import_backup(str(empty_sqlite))

    def test_export_overwrites_stale_file(self):
        backup_path = self._export()
        movie_library_db.delete_works([WORK_TV2, WORK_MOVIE])
        counts = movie_library_db.export_backup(str(backup_path))
        self.assertEqual(counts["library_works"], 1)  # 不残留旧数据


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("Client", (), {"host": host})()


class LibraryManageRouteTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_store = main.store
        self.addCleanup(setattr, main, "store", self._original_store)
        from app.session_store import SessionStore
        main.store = SessionStore(Path(self._directory.name))
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(main.store.db_file)
        _seed()

    def test_works_delete_route(self):
        result = asyncio.run(main.delete_library_works(
            main.LibraryWorksDeleteRequest(dirs=[WORK_MOVIE]), _StubRequest()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["works"], 1)
        remaining = {w["dir"] for w in movie_library_db.search("", 1, 50)[1]}
        self.assertNotIn(WORK_MOVIE, remaining)
        with self.assertRaises(HTTPException):
            asyncio.run(main.delete_library_works(main.LibraryWorksDeleteRequest(dirs=[]), _StubRequest()))

    def test_category_delete_route(self):
        result = asyncio.run(main.delete_library_category(
            main.LibraryCategoryDeleteRequest(cat="电影"), _StubRequest()))
        self.assertEqual(result["works"], 1)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.delete_library_category(
                main.LibraryCategoryDeleteRequest(cat="不存在的分类"), _StubRequest()))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_backup_export_and_import_routes(self):
        exported = asyncio.run(main.export_library_backup(main.LibraryTokenRequest(), _StubRequest()))
        self.assertTrue(exported["ok"])
        self.assertEqual(exported["works"], 3)
        self.assertTrue(Path(exported["path"], exported["file"]).exists())
        # 清空后从备份恢复
        movie_library_db.delete_works([WORK_TV, WORK_TV2, WORK_MOVIE])
        self.assertEqual(movie_library_db.search("", 1, 50)[1], [])
        restored = asyncio.run(main.import_library_backup(
            main.LibraryBackupImportRequest(path=str(Path(exported["path"], exported["file"]))), _StubRequest()))
        self.assertEqual(restored["works"], 3)
        self.assertEqual(restored["files"], 5)
        works = {w["dir"] for w in movie_library_db.search("", 1, 50)[1]}
        self.assertEqual(works, {WORK_TV, WORK_TV2, WORK_MOVIE})

    def test_backup_routes_token_guard(self):
        asyncio.run(main.write_library_config(main.LibraryConfigRequest(token="t" * 24), _StubRequest()))
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.export_library_backup(main.LibraryTokenRequest(), _StubRequest(host="10.0.0.2")))
        self.assertEqual(ctx.exception.status_code, 401)
        with self.assertRaises(HTTPException):
            asyncio.run(main.import_library_backup(
                main.LibraryBackupImportRequest(path="/tmp/x.db"), _StubRequest(host="10.0.0.2")))


if __name__ == "__main__":
    unittest.main()
