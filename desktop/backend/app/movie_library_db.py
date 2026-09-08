"""影库数据库层：影库信息入 sqlite（同一个 cloud123.db），源 JSON 删除后仍可查询。

三张表：
- library_sources       已导入来源（一个上传/批量导入的文件 = 一个来源）
- library_works         作品（dir 唯一；同 dir 重复导入保留先入库的）
- library_work_files    作品内文件明细（etag/size 等，秒传/导出用）

搜索走 norm_title/pinyin 索引 + SQL 分页；分类/统计 GROUP BY/SUM。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .movie_library import fmt_size, norm, parse_dir_name, pinyin_keys, split_category

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_sources (
    name TEXT PRIMARY KEY,
    imported_at TEXT NOT NULL,
    common_path TEXT NOT NULL DEFAULT '',
    work_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS library_works (
    dir TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    year INTEGER,
    tmdb_id INTEGER,
    cat TEXT NOT NULL DEFAULT '',
    sub TEXT NOT NULL DEFAULT '',
    pinyin TEXT NOT NULL DEFAULT '',
    pinyin_first TEXT NOT NULL DEFAULT '',
    file_count INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_work_files (
    dir TEXT NOT NULL,
    path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    etag TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    s3_key_flag TEXT NOT NULL DEFAULT '',
    is_video INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dir, path)
);
CREATE INDEX IF NOT EXISTS idx_library_works_norm ON library_works(norm_title);
CREATE INDEX IF NOT EXISTS idx_library_works_pinyin ON library_works(pinyin);
CREATE INDEX IF NOT EXISTS idx_library_works_pinyin_first ON library_works(pinyin_first);
CREATE INDEX IF NOT EXISTS idx_library_works_cat ON library_works(cat, sub);
CREATE INDEX IF NOT EXISTS idx_library_works_source ON library_works(source);
CREATE INDEX IF NOT EXISTS idx_library_work_files_dir ON library_work_files(dir);
"""


class LibraryDb:
    """影库数据库操作。db_path 指向 cloud123.db（WAL）。"""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # ---------- 导入 ----------
    def import_payload(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """把解析后的影库 payload 入库。dir 已存在 → 跳过（保留先入库的）；
        同名来源重新导入 → 先清该来源旧数据再插（可更新）。"""
        common_path = str(payload.get("commonPath") or "").strip("/")
        files: List[Dict[str, Any]] = payload.get("files") or []
        # path 拼回 commonPath 再聚合，避免相对路径被拆散成多个作品
        if common_path:
            for f in files:
                p = str(f.get("path") or "")
                if p and not p.startswith(common_path + "/"):
                    f["path"] = common_path + "/" + p.lstrip("/")

        from .movie_library import aggregate_works

        works = aggregate_works(common_path, files)
        imported_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

        added = skipped = 0
        added_files = added_size = 0
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM library_work_files WHERE dir IN (SELECT dir FROM library_works WHERE source = ?)", (name,))
                connection.execute("DELETE FROM library_works WHERE source = ?", (name,))
                for work_dir, info in works.items():
                    exists = connection.execute("SELECT 1 FROM library_works WHERE dir = ?", (work_dir,)).fetchone()
                    if exists:
                        skipped += 1
                        continue
                    cat, sub = split_category(work_dir)
                    connection.execute(
                        "INSERT INTO library_works (dir, title, norm_title, year, tmdb_id, cat, sub, pinyin, pinyin_first,"
                        " file_count, video_count, total_size, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (work_dir, info["title"], norm(info["title"]), info["year"], info["tmdb_id"], cat, sub,
                         info["pinyin"], info["pinyin_first"], info["count"], info["video_count"], info["total_size"], name),
                    )
                    for f in info["files"]:
                        fpath = str(f.get("path") or "")
                        fname = str(f.get("fileName") or fpath.rsplit("/", 1)[-1])
                        connection.execute(
                            "INSERT OR REPLACE INTO library_work_files (dir, path, file_name, etag, size, s3_key_flag, is_video)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (work_dir, fpath or fname, fname, str(f.get("etag") or ""), int(f.get("size") or 0),
                             str(f.get("s3KeyFlag") or ""), 1 if fpath.lower().endswith((".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".rm", ".rmvb", ".iso", ".vob", ".3gp", ".asf", ".divx", ".f4v")) else 0),
                        )
                    added += 1
                    added_files += info["count"]
                    added_size += info["total_size"]
                connection.execute(
                    "INSERT OR REPLACE INTO library_sources (name, imported_at, common_path, work_count, file_count, total_size)"
                    " VALUES (?,?,?,?,?,?)",
                    (name, imported_at, common_path, added, added_files, added_size),
                )
        finally:
            connection.close()
        logger.info(
            "影库导入：%s — 新增 %d 个作品、重复跳过 %d 个、%d 个文件",
            name, added, skipped, added_files,
        )
        return {"ok": True, "name": name, "added": added, "skipped": skipped,
                "fileCount": added_files, "totalSize": added_size}

    def delete_source(self, name: str) -> bool:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM library_work_files WHERE dir IN (SELECT dir FROM library_works WHERE source = ?)", (name,))
                deleted = connection.execute("DELETE FROM library_works WHERE source = ?", (name,)).rowcount
                row = connection.execute("DELETE FROM library_sources WHERE name = ?", (name,)).rowcount
            return bool(row or deleted)
        finally:
            connection.close()

    def clear_sources(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM library_work_files")
                connection.execute("DELETE FROM library_works")
                connection.execute("DELETE FROM library_sources")
        finally:
            connection.close()

    # ---------- 查询 ----------
    def list_sources(self) -> List[Dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT name, imported_at, work_count, file_count, total_size FROM library_sources ORDER BY imported_at DESC"
            ).fetchall()
            return [{
                "name": r["name"], "loadDate": r["imported_at"][:10],
                "fileCount": r["work_count"], "totalSize": r["total_size"],
            } for r in rows]
        finally:
            connection.close()

    def totals(self) -> Dict[str, Any]:
        connection = self._connect()
        try:
            works = connection.execute(
                "SELECT COUNT(*) AS work_count, COALESCE(SUM(file_count),0) AS file_count,"
                " COALESCE(SUM(video_count),0) AS video_count, COALESCE(SUM(total_size),0) AS total_size FROM library_works"
            ).fetchone()
            lib_count = connection.execute("SELECT COUNT(*) AS c FROM library_sources").fetchone()["c"]
            return {
                "libCount": lib_count,
                "workCount": works["work_count"],
                "fileCount": works["file_count"],
                "videoCount": works["video_count"],
                "totalSize": works["total_size"],
                "totalSizeLabel": fmt_size(works["total_size"]),
            }
        finally:
            connection.close()

    def search(self, q: str, page: int, size: int, cat: str = "", sub: str = "", libs: Optional[List[str]] = None):
        """q 非空：片名/拼音模糊搜索（归一化标题、目录、全拼、首字母）；q 为空：浏览模式年份降序。"""
        nq = norm(q) if q else ""
        where = []
        params: List[Any] = []
        if cat and cat != "全部文件":
            where.append("cat = ?")
            params.append(cat)
            if sub:
                where.append("sub = ?")
                params.append(sub)
        if libs:
            where.append(f"source IN ({','.join('?' for _ in libs)})")
            params.extend(libs)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        order = ("ORDER BY CASE WHEN year IS NULL THEN 1 ELSE 0 END, year DESC, video_count DESC, dir"
                 if not nq else
                 "ORDER BY video_count DESC, dir")
        connection = self._connect()
        try:
            if nq:
                like = f"%{nq}%"
                match = ("(norm_title LIKE ? OR dir LIKE ? OR (pinyin != '' AND pinyin LIKE ?)"
                         " OR (pinyin_first != '' AND length(?) >= 2 AND pinyin_first LIKE ?))")
                match_params = [like, like, like, nq, like]
                if where:
                    total = connection.execute(
                        f"SELECT COUNT(*) AS c FROM library_works WHERE {where_sql} AND {match}",
                        [*params, *match_params]).fetchone()["c"]
                    rows = connection.execute(
                        f"SELECT * FROM library_works WHERE {where_sql} AND {match} {order} LIMIT ? OFFSET ?",
                        [*params, *match_params, size, (page - 1) * size]).fetchall()
                else:
                    total = connection.execute(
                        f"SELECT COUNT(*) AS c FROM library_works WHERE {match}", match_params).fetchone()["c"]
                    rows = connection.execute(
                        f"SELECT * FROM library_works WHERE {match} {order} LIMIT ? OFFSET ?",
                        [*match_params, size, (page - 1) * size]).fetchall()
            else:
                total = connection.execute(
                    f"SELECT COUNT(*) AS c FROM library_works {where_sql}", params).fetchone()["c"]
                rows = connection.execute(
                    f"SELECT * FROM library_works {where_sql} {order} LIMIT ? OFFSET ?",
                    [*params, size, (page - 1) * size]).fetchall()
            results = [{
                "dir": r["dir"], "title": r["title"], "year": r["year"], "tmdbId": r["tmdb_id"],
                "count": r["file_count"], "videoCount": r["video_count"], "totalSize": r["total_size"],
                "cat": r["cat"], "sub": r["sub"],
            } for r in rows]
            return total, results
        finally:
            connection.close()

    def categories(self, libs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        where = ""
        params: List[Any] = []
        if libs:
            where = f"WHERE source IN ({','.join('?' for _ in libs)})"
            params = list(libs)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT cat, sub, COUNT(*) AS count, SUM(total_size) AS size FROM library_works {where}"
                " GROUP BY cat, sub ORDER BY NULL", params).fetchall()
        finally:
            connection.close()
        cats: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cat = r["cat"] or ""
            if not cat:
                continue
            e = cats.setdefault(cat, {"count": 0, "size": 0, "subs": {}})
            e["count"] += r["count"]
            e["size"] += r["size"] or 0
            if r["sub"]:
                sub = e["subs"].setdefault(r["sub"], {"count": 0, "size": 0})
                sub["count"] += r["count"]
                sub["size"] += r["size"] or 0
        out = []
        for cat, e in sorted(cats.items(), key=lambda kv: -kv[1]["count"]):
            if e["count"] < 2:
                continue
            out.append({
                "name": cat, "count": e["count"], "size": e["size"],
                "subs": [{"name": s, "count": n["count"], "size": n["size"]}
                         for s, n in sorted(e["subs"].items(), key=lambda kv: -kv[1]["count"])],
            })
        if not out:
            all_count = sum(e["count"] for e in cats.values())
            all_size = sum(e["size"] for e in cats.values())
            out.append({"name": "全部文件", "count": all_count, "size": all_size, "subs": []})
        return out

    def list_files(self, dirname: str) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        try:
            work = connection.execute(
                "SELECT dir, title, year, tmdb_id FROM library_works WHERE dir = ?", (dirname,)).fetchone()
            if work is None:
                return None
            rows = connection.execute(
                "SELECT path, file_name, etag, size, is_video FROM library_work_files WHERE dir = ? ORDER BY path",
                (dirname,)).fetchall()
            files = [{
                "fileName": r["file_name"],
                "path": r["path"],
                "etag": r["etag"],
                "size": r["size"],
                "isVideo": bool(r["is_video"]),
            } for r in rows]
            return {
                "dir": dirname, "title": work["title"], "year": work["year"],
                "tmdbId": work["tmdb_id"], "files": files,
            }
        finally:
            connection.close()

    # ---------- 导出 ----------
    def _payload(self, common_path: str, files: List[Dict[str, Any]], script_version: str = "") -> Dict[str, Any]:
        total = sum(int(f.get("size") or 0) for f in files)
        return {
            "scriptVersion": script_version,
            "exportVersion": "1.0",
            "usesBase62EtagsInExport": False,
            "commonPath": common_path,
            "totalFilesCount": len(files),
            "totalSize": total,
            "formattedTotalSize": fmt_size(total),
            "files": files,
        }

    @staticmethod
    def _file_row(f: Dict[str, Any], strip_prefix: str = "") -> Dict[str, Any]:
        p = str(f.get("path") or "")
        if strip_prefix and p.startswith(strip_prefix):
            p = p[len(strip_prefix):]
        name = str(f.get("file_name") or p.rsplit("/", 1)[-1])
        return {"path": p, "fileName": name, "etag": str(f.get("etag") or ""),
                "size": int(f.get("size") or 0), "type": 0, "s3KeyFlag": str(f.get("s3_key_flag") or "")}

    def export_work(self, dirname: str) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        try:
            work = connection.execute(
                "SELECT dir, source FROM library_works WHERE dir = ?", (dirname,)).fetchone()
            if work is None:
                return None
            src = connection.execute(
                "SELECT common_path FROM library_sources WHERE name = ?", (work["source"],)).fetchone()
            cp = str(src["common_path"]) if src else ""
            rel = dirname.strip("/")
            if cp and (rel == cp or rel.startswith(cp + "/")):
                rel = ""
            elif cp and rel.startswith(cp):
                rel = rel[len(cp):].lstrip("/")
            common = (cp + "/" + rel).strip("/") if rel or cp else ""
            rows = connection.execute(
                "SELECT path, file_name, etag, size, s3_key_flag FROM library_work_files WHERE dir = ? ORDER BY path",
                (dirname,)).fetchall()
            files = [self._file_row(dict(r)) for r in rows]
            return self._payload(common if not common or common.endswith("/") else common + "/", files)
        finally:
            connection.close()

    def _iter_category(self, connection, cat: str, sub: str, libs: Optional[List[str]]):
        prefix = cat + "/"
        if sub:
            prefix += sub + "/"
        # LIKE 必须带通配：dir LIKE '电影/%'，否则只会匹配到字面量 '电影/'
        where = ["(dir LIKE ? OR dir = ?)"]
        params = [prefix + "%", prefix.rstrip("/")]
        if libs:
            where.append(f"source IN ({','.join('?' for _ in libs)})")
            params.extend(libs)
        return connection.execute(
            f"SELECT dir, path, file_name, etag, size, s3_key_flag FROM library_work_files"
            f" WHERE dir IN (SELECT dir FROM library_works WHERE {' AND '.join(where)})"
            " ORDER BY dir, path", params).fetchall()

    def export_category(self, cat: str, sub: str = "", libs: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if cat == "全部文件":
            return self._export_all(libs)
        prefix = cat + "/"
        if sub:
            prefix += sub + "/"
        connection = self._connect()
        try:
            rows = self._iter_category(connection, cat, sub, libs)
            if not rows:
                return None
            common = prefix
            files = [self._file_row(dict(r), strip_prefix=prefix) for r in rows]
            return self._payload(common, files)
        finally:
            connection.close()

    def _export_all(self, libs: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        connection = self._connect()
        try:
            if libs:
                marks = ",".join("?" for _ in libs)
                rows = connection.execute(
                    f"SELECT dir, path, file_name, etag, size, s3_key_flag FROM library_work_files"
                    f" WHERE dir IN (SELECT dir FROM library_works WHERE source IN ({marks})) ORDER BY dir, path",
                    libs).fetchall()
            else:
                rows = connection.execute(
                    "SELECT dir, path, file_name, etag, size, s3_key_flag FROM library_work_files ORDER BY dir, path").fetchall()
            if not rows:
                return None
            files = [self._file_row(dict(r)) for r in rows]
            return self._payload("", files)
        finally:
            connection.close()

    def export_multi(self, cat_sub_pairs: List[Tuple[str, str]], libs: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        seen_etags: set = set()
        connection = self._connect()
        try:
            for cat, sub in cat_sub_pairs:
                rows = self._iter_category(connection, cat, sub, libs)
                for r in rows:
                    etag = str(r["etag"] or "")
                    if etag and etag in seen_etags:
                        continue
                    if etag:
                        seen_etags.add(etag)
                    files.append(self._file_row(dict(r)))
        finally:
            connection.close()
        if not files:
            return None
        total = sum(f["size"] for f in files)
        return {
            "scriptVersion": "", "exportVersion": "1.0", "usesBase62EtagsInExport": False,
            "commonPath": "", "totalFilesCount": len(files), "totalSize": total,
            "formattedTotalSize": fmt_size(total), "files": files,
        }

    def transfer_files(self, dirs: List[str], include_files: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """转存用的作品文件列表（含 includeFiles 按路径/文件名过滤）。"""
        works = []
        keep = set(include_files or [])
        connection = self._connect()
        try:
            for d in dirs:
                work = connection.execute("SELECT dir FROM library_works WHERE dir = ?", (d,)).fetchone()
                if work is None:
                    continue
                rows = connection.execute(
                    "SELECT path, file_name, etag, size, s3_key_flag FROM library_work_files WHERE dir = ? ORDER BY path",
                    (d,)).fetchall()
                files = [self._file_row(dict(r)) for r in rows]
                if keep:
                    files = [f for f in files if f["path"] in keep or f["fileName"] in keep]
                if files:
                    works.append({"dir": d, "files": files})
            return works
        finally:
            connection.close()

    def works_exist(self, dirs: List[str]) -> bool:
        connection = self._connect()
        try:
            for d in dirs:
                if connection.execute("SELECT 1 FROM library_works WHERE dir = ?", (d,)).fetchone():
                    return True
            return False
        finally:
            connection.close()


# ---- 模块级单例（main 启动时 init 一次） ----
_default_db: Optional[LibraryDb] = None


def init(db_path) -> None:
    global _default_db
    _default_db = LibraryDb(db_path)


def _db() -> LibraryDb:
    if _default_db is None:
        raise RuntimeError("影库数据库未初始化")
    return _default_db


def import_payload(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _db().import_payload(name, payload)


def delete_source(name: str) -> bool:
    return _db().delete_source(name)


def list_sources() -> List[Dict[str, Any]]:
    return _db().list_sources()


def totals() -> Dict[str, Any]:
    return _db().totals()


def search(q: str, page: int, size: int, cat: str = "", sub: str = "", libs: Optional[List[str]] = None):
    return _db().search(q, page, size, cat, sub, libs)


def categories(libs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    return _db().categories(libs)


def list_files(dirname: str) -> Optional[Dict[str, Any]]:
    return _db().list_files(dirname)


def export_work(dirname: str) -> Optional[Dict[str, Any]]:
    return _db().export_work(dirname)


def export_category(cat: str, sub: str = "", libs: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _db().export_category(cat, sub, libs)


def export_multi(cat_sub_pairs: List[Tuple[str, str]], libs: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _db().export_multi(cat_sub_pairs, libs)


def transfer_files(dirs: List[str], include_files: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    return _db().transfer_files(dirs, include_files)


def works_exist(dirs: List[str]) -> bool:
    return _db().works_exist(dirs)
