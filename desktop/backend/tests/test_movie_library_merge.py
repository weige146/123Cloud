"""影库增量合并导入、整理成果保留、重复作品合并、无标记选配队列（2026-09-19）。

背景：旧行为「dir 已存在整作品跳过」导致更新的导出（新集、新版本）永远进不来。
新默认「merge」只增不减：新文件并入已有作品、统计/技术属性重算、整理成果保留。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import movie_library, movie_library_db


def _etag(i: int) -> str:
    return f"{i:032x}"


def _fastlink_payload(common_path: str, files, **kwargs):
    return {
        "scriptVersion": "3.2.1",
        "exportVersion": "1.0",
        "usesBase62EtagsInExport": True,
        "commonPath": common_path,
        "totalFilesCount": len(files),
        "files": files,
        **kwargs,
    }


WORK_A = "电影/华语/海王 (2018) {tmdb-297802}"
_ENRICHED = {
    "media_type": "电影", "genres": ["动作", "科幻"], "region": "欧美",
    "poster_path": "https://image.tmdb.org/x.jpg", "vote_average": 7.5,
    "overview": "海底王国", "year": 2018,
}


class MergeImportTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db = movie_library_db.LibraryDb(Path(self._directory.name) / "cloud123.db")

    def _write_json(self, name: str, payload):
        f = Path(self._directory.name) / name
        f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return f

    def test_merge_stream_adds_new_episodes_and_keeps_enrichment(self):
        """不同来源的更新导出：新集并入已有作品，整理成果与播放记录保留。"""
        self.db.import_payload("旧来源.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]))
        self.db.apply_enrichment(WORK_A, _ENRICHED)
        self.db.upsert_playback(WORK_A, f"{WORK_A}/a.mkv", season=1, episode=1, watched=True)

        f = self._write_json("新来源.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            {"path": f"{WORK_A}/new.mkv", "fileName": "new.mkv", "etag": _etag(9), "size": 5},
        ]))
        meta, entries = movie_library.open_library_stream(f)
        r = self.db.import_stream("新来源.json", meta["commonPath"], entries)
        self.assertEqual(r["added"], 0)
        self.assertEqual(r["mergedWorks"], 1)
        self.assertEqual(r["mergedFiles"], 1)

        row = self.db.list_files(WORK_A)
        names = sorted(f["fileName"] for f in row["files"])
        self.assertEqual(names, ["a.mkv", "new.mkv"], "已有文件保留、新文件并入")
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT * FROM library_works WHERE dir = ?", (WORK_A,)).fetchone())
            playback = conn.execute(
                "SELECT * FROM library_playback WHERE dir = ? AND file_path = ?",
                (WORK_A, f"{WORK_A}/a.mkv")).fetchone()
        finally:
            conn.close()
        self.assertEqual(work["tmdb_status"], "ok", "合并不应重置整理状态")
        self.assertEqual(json.loads(work["genres"]), ["动作", "科幻"])
        self.assertEqual(work["file_count"], 2, "统计应按合并结果重算")
        self.assertEqual(work["total_size"], 105)
        self.assertIsNotNone(playback, "播放记录应保留")

    def test_merge_same_source_reimport_is_additive(self):
        """merge 模式同名来源重导：只增不减（旧文件不删），与 skip 模式的整包替换区分开。"""
        self.db.import_payload("库2.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]), mode="merge")
        f = self._write_json("库2.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(4), "size": 2},
            {"path": f"{WORK_A}/c2.mkv", "fileName": "c2.mkv", "etag": _etag(5), "size": 7},
        ]))
        meta, entries = movie_library.open_library_stream(f)
        r = self.db.import_stream("库2.json", meta["commonPath"], entries, mode="merge")
        self.assertEqual(r["added"], 0)
        self.assertEqual(r["mergedFiles"], 1)
        row = self.db.list_files(WORK_A)
        by_name = {f["fileName"]: f["etag"] for f in row["files"]}
        self.assertEqual(by_name, {"a.mkv": _etag(1), "c2.mkv": _etag(5)}, "只增不减：旧文件保留")

    def test_merge_recomputes_tech_from_merged_files(self):
        """后并入更高规格的文件时，作品级技术属性应被拉上去。"""
        self.db.import_payload("旧来源.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]))
        f = self._write_json("新来源.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/dv.mkv", "fileName": "2160p.DoVi.HDR10.HEVC.dv.mkv", "etag": _etag(9), "size": 500},
        ]))
        meta, entries = movie_library.open_library_stream(f)
        self.db.import_stream("新来源.json", meta["commonPath"], entries, mode="merge")
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT tech, resolution FROM library_works WHERE dir = ?", (WORK_A,)).fetchone())
        finally:
            conn.close()
        tech = json.loads(work["tech"] or "{}")
        self.assertEqual(tech.get("dolbyVision"), "DV", "并入杜比视界文件后作品级 DV 应生效")

    def test_merge_payload_mode_matches_stream(self):
        payload_a = _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ])
        self.db.import_payload("旧来源.json", payload_a)
        payload_b = _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            {"path": f"{WORK_A}/new.mkv", "fileName": "new.mkv", "etag": _etag(9), "size": 5},
        ])
        r = self.db.import_payload("新来源.json", payload_b, mode="merge")
        self.assertEqual((r["mergedWorks"], r["mergedFiles"]), (1, 1))
        row = self.db.list_files(WORK_A)
        self.assertEqual(sorted(f["fileName"] for f in row["files"]), ["a.mkv", "new.mkv"])

    def test_merge_large_root_falls_back_to_skip(self):
        """超大作品（路径集合超限）退回跳过，不并文件。"""
        self.db.import_payload("旧来源.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            {"path": f"{WORK_A}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 100},
        ]))
        limit = movie_library_db.LibraryDb.MERGE_PATH_SET_LIMIT
        movie_library_db.LibraryDb.MERGE_PATH_SET_LIMIT = 1
        try:
            f = self._write_json("新来源.json", _fastlink_payload("电影", [
                {"path": f"{WORK_A}/new.mkv", "fileName": "new.mkv", "etag": _etag(9), "size": 5},
            ]))
            meta, entries = movie_library.open_library_stream(f)
            r = self.db.import_stream("新来源.json", meta["commonPath"], entries, mode="merge")
            self.assertEqual(r["mergedFiles"], 0)
            self.assertEqual(r["skipped"], 1)
            row = self.db.list_files(WORK_A)
            self.assertEqual(len(row["files"]), 2, "超限作品不合并")
        finally:
            movie_library_db.LibraryDb.MERGE_PATH_SET_LIMIT = limit

    def test_skip_mode_replaces_source_and_restores_enrichment(self):
        """skip 模式（旧行为）：同名来源整包替换，但已整理字段快照后按 dir 恢复。"""
        self.db.import_payload("库.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]))
        self.db.apply_enrichment(WORK_A, _ENRICHED)

        r = self.db.import_payload("库.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a2.mkv", "fileName": "a2.mkv", "etag": _etag(4), "size": 2},
            {"path": f"{WORK_A}/d.mkv", "fileName": "d.mkv", "etag": _etag(5), "size": 3},
        ]), mode="skip")
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["mergedWorks"], 0)
        row = self.db.list_files(WORK_A)
        names = sorted(f["fileName"] for f in row["files"])
        self.assertEqual(names, ["a2.mkv", "d.mkv"], "skip 模式同名来源整包替换")
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT * FROM library_works WHERE dir = ?", (WORK_A,)).fetchone())
        finally:
            conn.close()
        self.assertEqual(work["tmdb_status"], "ok", "重导后整理成果应从快照恢复")
        self.assertEqual(json.loads(work["genres"]), ["动作", "科幻"])


class DuplicateWorkTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db = movie_library_db.LibraryDb(Path(self._directory.name) / "cloud123.db")

    def test_duplicate_groups_and_merge(self):
        work_b = "电影/外语/海王 (2018) {tmdb-297802}"  # 同 tmdb、不同目录
        self.db.import_payload("库A.json", _fastlink_payload("电影", [
            {"path": f"{WORK_A}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
        ]))
        self.db.apply_enrichment(WORK_A, _ENRICHED)
        self.db.upsert_playback(WORK_A, f"{WORK_A}/a.mkv", season=1, episode=1, watched=True)
        self.db.import_payload("库B.json", _fastlink_payload("电影", [
            {"path": f"{work_b}/a.mkv", "fileName": "a.mkv", "etag": _etag(1), "size": 100},
            {"path": f"{work_b}/extra.mkv", "fileName": "extra.mkv", "etag": _etag(2), "size": 50},
        ]))
        self.db.upsert_playback(work_b, f"{work_b}/extra.mkv", season=1, episode=1, watched=True)

        groups = self.db.duplicate_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["tmdbId"], 297802)
        self.assertEqual(len(groups[0]["works"]), 2)

        result = self.db.merge_duplicate_works(WORK_A, [work_b])
        self.assertEqual(result["mergedWorks"], 1)
        self.assertEqual(result["movedFiles"], 1, "path 相同的 a.mkv 保留先入库的，只移入 extra.mkv")

        row = self.db.list_files(WORK_A)
        names = sorted(f["fileName"] for f in row["files"])
        self.assertEqual(names, ["a.mkv", "extra.mkv"])
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT * FROM library_works WHERE dir = ?", (WORK_A,)).fetchone())
            dup_gone = conn.execute("SELECT 1 FROM library_works WHERE dir = ?", (work_b,)).fetchone()
            keep_playback = conn.execute("SELECT COUNT(*) AS c FROM library_playback WHERE dir = ?", (WORK_A,)).fetchone()["c"]
            dup_playback = conn.execute("SELECT COUNT(*) AS c FROM library_playback WHERE dir = ?", (work_b,)).fetchone()["c"]
        finally:
            conn.close()
        self.assertIsNone(dup_gone, "副本作品行应删除")
        self.assertEqual(work["file_count"], 2, "保留作品统计应重算")
        self.assertEqual(json.loads(work["genres"]), ["动作", "科幻"], "整理成果保留")
        self.assertEqual(keep_playback, 1, "保留作品的播放记录不动")
        self.assertEqual(dup_playback, 0, "副本播放记录清除")
        self.assertEqual(self.db.duplicate_groups(), [])


class UntaggedEnrichQueueTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db = movie_library_db.LibraryDb(Path(self._directory.name) / "cloud123.db")

    def test_untagged_queue_attempts_and_assign(self):
        untagged_dir = "电影/无名 2020"
        self.db.import_payload("库.json", _fastlink_payload("", [
            {"path": f"{untagged_dir}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 10},
        ]))
        rows = self.db.untagged_works(10)
        self.assertEqual([r["dir"] for r in rows], [untagged_dir])

        self.db.assign_tmdb_id(untagged_dir, 999)
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT tmdb_id, tmdb_status FROM library_works WHERE dir = ?", (untagged_dir,)).fetchone())
        finally:
            conn.close()
        self.assertEqual((work["tmdb_id"], work["tmdb_status"]), (999, "pending"))
        self.assertEqual(self.db.untagged_works(10), [])

    def test_untagged_failures_stop_then_reset_reopens(self):
        untagged_dir = "电影/无名 2020"
        self.db.import_payload("库.json", _fastlink_payload("", [
            {"path": f"{untagged_dir}/b.mkv", "fileName": "b.mkv", "etag": _etag(2), "size": 10},
        ]))
        for _ in range(3):
            self.db.mark_untagged_failure(untagged_dir)
        self.assertEqual(self.db.untagged_works(10), [], "达到尝试上限后不再排队")
        import sqlite3
        conn = sqlite3.connect(Path(self._directory.name) / "cloud123.db")
        conn.row_factory = sqlite3.Row
        try:
            work = dict(conn.execute("SELECT tmdb_status, enrich_attempts FROM library_works WHERE dir = ?", (untagged_dir,)).fetchone())
        finally:
            conn.close()
        self.assertEqual(work["tmdb_status"], "none", "搜索失败不改变状态，避免污染统计语义")
        self.db.reset_enrichment(only_failed=False)
        self.assertEqual(len(self.db.untagged_works(10)), 1, "刷新全部后清零尝试次数")


if __name__ == "__main__":
    unittest.main()
