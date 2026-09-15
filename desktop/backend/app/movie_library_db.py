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

from .movie_library import (
    VIDEO_EXT, fmt_size, new_tech_state, norm, parse_dir_name, pinyin_keys,
    split_category, split_work, tech_result, update_tech_state, video_ext_set,
)

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
    resolution TEXT NOT NULL DEFAULT '',
    edition TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT '',
    genres TEXT NOT NULL DEFAULT '[]',
    region TEXT NOT NULL DEFAULT '',
    poster_path TEXT NOT NULL DEFAULT '',
    vote_average REAL NOT NULL DEFAULT 0,
    overview TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    air_status TEXT NOT NULL DEFAULT '',
    popularity REAL NOT NULL DEFAULT 0,
    tmdb_status TEXT NOT NULL DEFAULT 'none',
    enrich_attempts INTEGER NOT NULL DEFAULT 0,
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
        self._migrate()

    def _migrate(self) -> None:
        """幂等迁移：给旧库 library_works 补分类/充实列、建新列索引、回填 tmdb_status。

        新库 _SCHEMA 已含全部列与旧索引；旧库 CREATE IF NOT EXISTS 不生效，
        新列在此 ALTER 补齐后，再安全创建依赖新列的索引。"""
        new_columns = {
            "media_type": "TEXT NOT NULL DEFAULT ''",
            "genres": "TEXT NOT NULL DEFAULT '[]'",
            "region": "TEXT NOT NULL DEFAULT ''",
            "poster_path": "TEXT NOT NULL DEFAULT ''",
            "vote_average": "REAL NOT NULL DEFAULT 0",
            "overview": "TEXT NOT NULL DEFAULT ''",
            "resolution": "TEXT NOT NULL DEFAULT ''",
            "edition": "TEXT NOT NULL DEFAULT ''",
            "language": "TEXT NOT NULL DEFAULT ''",
            "air_status": "TEXT NOT NULL DEFAULT ''",
            "popularity": "REAL NOT NULL DEFAULT 0",
            "tmdb_status": "TEXT NOT NULL DEFAULT 'none'",
            "enrich_attempts": "INTEGER NOT NULL DEFAULT 0",
        }
        with self._connect() as conn:
            existing = {str(r[1]) for r in conn.execute("PRAGMA table_info(library_works)").fetchall()}
            for name, decl in new_columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE library_works ADD COLUMN {name} {decl}")
            for stmt in (
                "CREATE INDEX IF NOT EXISTS idx_library_works_tmdb_status ON library_works(tmdb_status)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_media_type ON library_works(media_type)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_region ON library_works(region)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_year ON library_works(year)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_language ON library_works(language)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_air_status ON library_works(air_status)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_resolution ON library_works(resolution)",
                "CREATE INDEX IF NOT EXISTS idx_library_works_edition ON library_works(edition)",
            ):
                conn.execute(stmt)
            # 有 tmdb_id 却仍是默认状态的行（旧数据）排进回填队列；无 id 的落 none
            conn.execute(
                "UPDATE library_works SET tmdb_status = 'pending'"
                " WHERE tmdb_id IS NOT NULL AND (tmdb_status IS NULL OR tmdb_status = 'none')"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # ---------- 导入 ----------
    def import_payload(self, name: str, payload: Dict[str, Any], video_ext: Any = None) -> Dict[str, Any]:
        """把解析后的影库 payload 入库。dir 已存在 → 跳过（保留先入库的）；
        同名来源重新导入 → 先清该来源旧数据再插（可更新）。
        video_ext：视频扩展名集合（None=默认 VIDEO_EXT，可传 video_ext_set(用户自定义)）。"""
        ext_set = video_ext_set(video_ext) if video_ext else VIDEO_EXT
        common_path = str(payload.get("commonPath") or "").strip("/")
        files: List[Dict[str, Any]] = payload.get("files") or []
        # path 拼回 commonPath 再聚合，避免相对路径被拆散成多个作品
        if common_path:
            for f in files:
                p = str(f.get("path") or "")
                if p and not p.startswith(common_path + "/"):
                    f["path"] = common_path + "/" + p.lstrip("/")

        from .movie_library import aggregate_works

        works = aggregate_works(common_path, files, ext_set)
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
                        " file_count, video_count, total_size, resolution, edition, tmdb_status, source)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (work_dir, info["title"], norm(info["title"]), info["year"], info["tmdb_id"], cat, sub,
                         info["pinyin"], info["pinyin_first"], info["count"], info["video_count"], info["total_size"],
                         info.get("resolution") or "", info.get("edition") or "",
                         "pending" if info["tmdb_id"] else "none", name),
                    )
                    for f in info["files"]:
                        fpath = str(f.get("path") or "")
                        fname = str(f.get("fileName") or fpath.rsplit("/", 1)[-1])
                        connection.execute(
                            "INSERT OR REPLACE INTO library_work_files (dir, path, file_name, etag, size, s3_key_flag, is_video)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (work_dir, fpath or fname, fname, str(f.get("etag") or ""), int(f.get("size") or 0),
                             str(f.get("s3KeyFlag") or ""), 1 if fpath.lower().endswith(tuple(ext_set)) else 0),
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

    # ---------- 流式导入（巨型秒传 JSON） ----------
    def import_stream(self, name: str, common_path: str, files_iter: Iterable[Dict[str, Any]], batch_size: int = 20000, video_ext: Any = None) -> Dict[str, Any]:
        """流式导入：条目迭代器逐批 executemany 写 library_work_files，作品行按累计统计最后统一写。
        语义与 import_payload 一致：dir 已存在 → 跳过（保留先入库的）；同名来源先清旧数据再插（可更新）。
        千万级条目内存占用只与批次大小和作品数相关，不随文件条数线性增长。
        video_ext：视频扩展名集合（None=默认 VIDEO_EXT，可传 video_ext_set(用户自定义)）。"""
        ext_set = video_ext_set(video_ext) if video_ext else VIDEO_EXT
        imported_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        fallback_root = common_path.rsplit("/", 1)[-1] if common_path else ""
        stats: Dict[str, List[int]] = {}
        tech: Dict[str, Dict[str, Any]] = {}
        verdicts: Dict[str, bool] = {}
        added_files = added_size = 0
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM library_work_files WHERE dir IN (SELECT dir FROM library_works WHERE source = ?)", (name,))
                connection.execute("DELETE FROM library_works WHERE source = ?", (name,))
                batch: List[Tuple] = []
                for entry in files_iter:
                    path = str(entry.get("path") or "")
                    # 相对路径拼回 commonPath（与 import_payload 一致），避免被拆散成多个作品
                    if common_path and path and not path.startswith(common_path + "/"):
                        path = common_path + "/" + path.lstrip("/")
                    fname = str(entry.get("fileName") or "") or path.rsplit("/", 1)[-1]
                    split = split_work(path, fallback_root)
                    if split is None:
                        continue
                    root = split[0]
                    verdict = verdicts.get(root)
                    if verdict is None:
                        verdict = connection.execute("SELECT 1 FROM library_works WHERE dir = ?", (root,)).fetchone() is not None
                        verdicts[root] = verdict
                    if verdict:
                        continue
                    size = int(entry.get("size") or 0)
                    is_video = 1 if fname.lower().endswith(tuple(ext_set)) else 0
                    batch.append((root, path or fname, fname,
                                  str(entry.get("etag") or ""), size, str(entry.get("s3KeyFlag") or ""), is_video))
                    g = stats.get(root)
                    if g is None:
                        g = stats[root] = [0, 0, 0]
                    g[0] += 1
                    g[1] += is_video
                    g[2] += size
                    if is_video:
                        update_tech_state(tech.setdefault(root, new_tech_state()), fname)
                    if len(batch) >= batch_size:
                        connection.executemany(
                            "INSERT OR REPLACE INTO library_work_files (dir, path, file_name, etag, size, s3_key_flag, is_video)"
                            " VALUES (?,?,?,?,?,?,?)", batch)
                        added_files += len(batch)
                        added_size += sum(row[4] for row in batch)
                        batch.clear()
                if batch:
                    connection.executemany(
                        "INSERT OR REPLACE INTO library_work_files (dir, path, file_name, etag, size, s3_key_flag, is_video)"
                        " VALUES (?,?,?,?,?,?,?)", batch)
                    added_files += len(batch)
                    added_size += sum(row[4] for row in batch)
                    batch.clear()
                work_rows = []
                for root, (count, video_count, total_size) in stats.items():
                    cat, sub = split_category(root)
                    title, year, tmdb_id = parse_dir_name(root)
                    pinyin_full, pinyin_first = pinyin_keys(title)
                    resolution, edition = tech_result(tech.get(root) or new_tech_state())
                    work_rows.append((root, title, norm(title), year, tmdb_id, cat, sub,
                                      pinyin_full, pinyin_first, count, video_count, total_size,
                                      resolution, edition, "pending" if tmdb_id else "none", name))
                if work_rows:
                    connection.executemany(
                        "INSERT INTO library_works (dir, title, norm_title, year, tmdb_id, cat, sub, pinyin, pinyin_first,"
                        " file_count, video_count, total_size, resolution, edition, tmdb_status, source)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", work_rows)
                added = len(work_rows)
                skipped = sum(1 for v in verdicts.values() if v)
                connection.execute(
                    "INSERT OR REPLACE INTO library_sources (name, imported_at, common_path, work_count, file_count, total_size)"
                    " VALUES (?,?,?,?,?,?)",
                    (name, imported_at, common_path, added, added_files, added_size))
        finally:
            connection.close()
        logger.info(
            "影库流式导入：%s — 新增 %d 个作品、重复跳过 %d 个、%d 个文件",
            name, added, skipped, added_files,
        )
        return {"ok": True, "name": name, "added": added, "skipped": skipped,
                "fileCount": added_files, "totalSize": added_size}

    # ---------- 查询 ----------
    def list_sources(self) -> List[Dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT name, imported_at, common_path, work_count, file_count, total_size FROM library_sources ORDER BY imported_at DESC"
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

    def _match_clause(self, q: str) -> Tuple[str, List[Any]]:
        """q 归一化后的匹配 SQL 片段（标题/目录/全拼/首字母）；空 q 返回 ("", [])。"""
        nq = norm(q) if q else ""
        if not nq:
            return "", []
        like = f"%{nq}%"
        match = ("(norm_title LIKE ? OR dir LIKE ? OR (pinyin != '' AND pinyin LIKE ?)"
                 " OR (pinyin_first != '' AND length(?) >= 2 AND pinyin_first LIKE ?))")
        return match, [like, like, like, nq, like]

    def _classification_clauses(self, media_type: str, genre: str, region: str, decade: int,
                                language: str = "", air_status: str = "", resolution: str = "",
                                edition: str = "", rating: float = 0) -> Tuple[List[str], List[Any]]:
        where: List[str] = []
        params: List[Any] = []
        if media_type:
            where.append("media_type = ?")
            params.append(media_type)
        if region:
            where.append("region = ?")
            params.append(region)
        if genre:
            where.append("genres LIKE ?")
            params.append(f'%"{genre}"%')
        if decade:
            where.append("year >= ? AND year <= ?")
            params.extend([decade, decade + 9])
        if language:
            where.append("language = ?")
            params.append(language)
        if air_status:
            where.append("air_status = ?")
            params.append(air_status)
        if resolution:
            where.append("resolution = ?")
            params.append(resolution)
        if edition:
            where.append("edition = ?")
            params.append(edition)
        if rating and rating > 0:
            where.append("vote_average >= ?")
            params.append(float(rating))
        return where, params

    def search(self, q: str, page: int, size: int, cat: str = "", sub: str = "",
               libs: Optional[List[str]] = None, media_type: str = "", genre: str = "",
               region: str = "", decade: int = 0, sort: str = "", language: str = "",
               air_status: str = "", resolution: str = "", edition: str = "",
               rating: float = 0):
        """片名/拼音模糊搜索 + 分类维度筛选 + 分页。q 为空且无筛选=浏览。
        sort：popularity(热度)/rating(评分)/recent(入库顺序)/title(拼音)/year，空=默认。"""
        nq = norm(q) if q else ""
        where: List[str] = []
        params: List[Any] = []
        if cat and cat != "全部文件":
            where.append("cat = ?")
            params.append(cat)
            if sub:
                where.append("sub = ?")
                params.append(sub)
        cls_where, cls_params = self._classification_clauses(
            media_type, genre, region, decade, language, air_status, resolution, edition, rating)
        where += cls_where
        params += cls_params
        if libs:
            where.append(f"source IN ({','.join('?' for _ in libs)})")
            params.extend(libs)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        if sort == "popularity":
            order = "ORDER BY popularity DESC, vote_average DESC, year DESC, dir"
        elif sort == "recent":
            order = "ORDER BY rowid DESC"
        elif sort == "rating":
            order = "ORDER BY vote_average DESC, year DESC, video_count DESC, dir"
        elif sort == "title":
            order = "ORDER BY CASE WHEN pinyin != '' THEN pinyin ELSE title END, dir"
        elif nq:
            order = "ORDER BY video_count DESC, dir"
        else:
            order = ("ORDER BY CASE WHEN year IS NULL THEN 1 ELSE 0 END, year DESC, video_count DESC, dir")
        match_sql, match_params = self._match_clause(q)
        connection = self._connect()
        try:
            base_params = list(params)
            base_where = where_sql
            if match_sql:
                if where_sql:
                    base_where = f"{where_sql} AND {match_sql}"
                    base_params = base_params + match_params
                else:
                    base_where = f"WHERE {match_sql}"
                    base_params = match_params
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM library_works {base_where}", base_params).fetchone()["c"]
            rows = connection.execute(
                f"SELECT * FROM library_works {base_where} {order} LIMIT ? OFFSET ?",
                [*base_params, size, (page - 1) * size]).fetchall()
            results = [self._row_to_work(r) for r in rows]
            return total, results
        finally:
            connection.close()

    @staticmethod
    def _row_to_work(r: sqlite3.Row) -> Dict[str, Any]:
        import json as _json
        try:
            genres = [str(g) for g in _json.loads(r["genres"] or "[]")]
        except Exception:
            genres = []
        return {
            "dir": r["dir"], "title": r["title"], "year": r["year"], "tmdbId": r["tmdb_id"],
            "count": r["file_count"], "videoCount": r["video_count"], "totalSize": r["total_size"],
            "cat": r["cat"], "sub": r["sub"],
            "mediaType": r["media_type"] or "", "genres": genres, "region": r["region"] or "",
            "voteAverage": r["vote_average"] or 0, "posterPath": r["poster_path"] or "",
            "overview": r["overview"] or "", "tmdbStatus": r["tmdb_status"] or "none",
            "language": r["language"] or "", "airStatus": r["air_status"] or "",
            "resolution": r["resolution"] or "", "edition": r["edition"] or "",
            "popularity": r["popularity"] or 0,
        }

    def facets(self, media_type: str = "", genre: str = "", region: str = "",
               decade: int = 0, libs: Optional[List[str]] = None, q: str = "",
               language: str = "", air_status: str = "", resolution: str = "",
               edition: str = "", rating: float = 0) -> Dict[str, Any]:
        """各分类维度的候选计数，用于前端动态筛选条。某维度的选项反映其它维度的当前选择
        （交叉筛选），但不含该维度自身选择——像优爱腾点了某频道后其它筛选项随之收窄。"""
        import json as _json

        state = {
            "media_type": media_type, "genre": genre, "region": region, "decade": decade,
            "language": language, "air_status": air_status, "resolution": resolution,
            "edition": edition, "rating": rating,
        }

        def fetch(exclude: str) -> List[sqlite3.Row]:
            vals = {k: (type(state[k])() if k == exclude else state[k]) for k in state}
            where, params = self._classification_clauses(
                vals["media_type"], vals["genre"], vals["region"], vals["decade"],
                vals["language"], vals["air_status"], vals["resolution"], vals["edition"], vals["rating"],
            )
            if libs:
                where.append(f"source IN ({','.join('?' for _ in libs)})")
                params.extend(libs)
            match_sql, match_params = self._match_clause(q)
            if match_sql:
                where.append(match_sql)
                params += match_params
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            connection = self._connect()
            try:
                return connection.execute(
                    "SELECT media_type, genres, region, year, language, air_status, resolution,"
                    f" edition, vote_average FROM library_works {where_sql}", params).fetchall()
            finally:
                connection.close()

        def count_by(exclude: str, getter) -> Dict[Any, int]:
            out: Dict[Any, int] = {}
            for r in fetch(exclude):
                k = getter(r)
                if k:
                    out[k] = out.get(k, 0) + 1
            return out

        channels = count_by("media_type", lambda r: r["media_type"])
        regions = count_by("region", lambda r: r["region"])
        languages = count_by("language", lambda r: r["language"])
        statuses = count_by("air_status", lambda r: r["air_status"])
        resolutions = count_by("resolution", lambda r: r["resolution"])
        editions = count_by("edition", lambda r: r["edition"])

        genres: Dict[str, int] = {}
        for r in fetch("genre"):
            try:
                for g in _json.loads(r["genres"] or "[]"):
                    g = str(g)
                    if g:
                        genres[g] = genres.get(g, 0) + 1
            except Exception:
                continue

        decades: Dict[int, int] = {}
        for r in fetch("decade"):
            if r["year"]:
                d = (int(r["year"]) // 10) * 10
                decades[d] = decades.get(d, 0) + 1

        # 评分区间桶（阈值式：与 search 的 rating>=阈值 对齐，9 表示 9+，0 表示全部）
        ratings: Dict[int, int] = {}
        for r in fetch("rating"):
            v = float(r["vote_average"] or 0)
            if v >= 9:
                ratings[9] = ratings.get(9, 0) + 1
            if v >= 8:
                ratings[8] = ratings.get(8, 0) + 1
            if v >= 7:
                ratings[7] = ratings.get(7, 0) + 1

        def top(d: Dict[Any, int]) -> List[Dict[str, Any]]:
            return [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])]

        return {
            "channels": top(channels),
            "genres": top(genres),
            "regions": top(regions),
            "languages": top(languages),
            "statuses": top(statuses),
            "resolutions": top(resolutions),
            "editions": top(editions),
            "decades": [{"name": d, "count": c} for d, c in sorted(decades.items(), reverse=True)],
            "ratings": [{"name": b, "count": ratings[b]} for b in (9, 8, 7) if b in ratings],
        }

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

    # ---------- 分类充实（后台懒回填队列） ----------
    def enrich_stats(self) -> Dict[str, int]:
        """按充实状态统计作品数：{total, pending, ok, failed, none}。"""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT tmdb_status AS s, COUNT(*) AS c FROM library_works GROUP BY tmdb_status").fetchall()
        finally:
            connection.close()
        stats = {"total": 0, "pending": 0, "ok": 0, "failed": 0, "none": 0}
        for r in rows:
            key = str(r["s"] or "none")
            stats[key] = stats.get(key, 0) + r["c"]
            stats["total"] += r["c"]
        return stats

    def pending_works(self, limit: int = 20) -> List[Dict[str, Any]]:
        """取一批待回填作品（有 tmdb_id、状态 pending），重试次数少的优先。"""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT dir, tmdb_id, title, year FROM library_works"
                " WHERE tmdb_status = 'pending' AND tmdb_id IS NOT NULL"
                " ORDER BY enrich_attempts, year DESC, dir LIMIT ?",
                (max(1, int(limit)),)).fetchall()
            return [{"dir": r["dir"], "tmdb_id": r["tmdb_id"], "title": r["title"], "year": r["year"]} for r in rows]
        finally:
            connection.close()

    def apply_enrichment(self, dirname: str, fields: Dict[str, Any]) -> None:
        """写回 TMDB 分类字段并置 tmdb_status='ok'。fields 可含 media_type/genres/region/poster_path/vote_average/overview/year。"""
        import json as _json
        genres = fields.get("genres")
        if not isinstance(genres, str):
            genres = _json.dumps(list(genres or []), ensure_ascii=False)
        year = fields.get("year")
        connection = self._connect()
        try:
            with connection:
                # year 为空（TMDB 未给）时保留原解析年份
                year_sql = "year = COALESCE(?, year)," if year else ""
                params = [
                    str(fields.get("media_type") or ""), genres,
                    str(fields.get("region") or ""), str(fields.get("poster_path") or ""),
                    float(fields.get("vote_average") or 0), str(fields.get("overview") or ""),
                    str(fields.get("language") or ""), str(fields.get("air_status") or ""),
                    float(fields.get("popularity") or 0),
                ]
                if year:
                    params.append(int(year))
                params.append(dirname)
                connection.execute(
                    "UPDATE library_works SET media_type = ?, genres = ?, region = ?, poster_path = ?,"
                    " vote_average = ?, overview = ?, language = ?, air_status = ?, popularity = ?,"
                    f" {year_sql} tmdb_status = 'ok', enrich_attempts = 0 WHERE dir = ?",
                    params,
                )
        finally:
            connection.close()

    def mark_enrich_failure(self, dirname: str, max_attempts: int = 3) -> None:
        """回填失败：重试计数 +1，达阈值转 failed（交手动/下次重置），否则保持 pending 继续排。"""
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "UPDATE library_works SET enrich_attempts = enrich_attempts + 1,"
                    " tmdb_status = CASE WHEN enrich_attempts + 1 >= ? THEN 'failed' ELSE 'pending' END"
                    " WHERE dir = ?",
                    (max(1, int(max_attempts)), dirname),
                )
        finally:
            connection.close()

    def reset_enrichment(self, only_failed: bool = True) -> int:
        """重新入队：默认把 failed 打回 pending；only_failed=False 则全部有 tmdb_id 的重排（含 ok，用于刷新分类）。"""
        connection = self._connect()
        try:
            with connection:
                sql = ("UPDATE library_works SET tmdb_status = 'pending', enrich_attempts = 0"
                       " WHERE tmdb_id IS NOT NULL")
                if only_failed:
                    sql += " AND tmdb_status = 'failed'"
                cur = connection.execute(sql)
                return cur.rowcount
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


def import_payload(name: str, payload: Dict[str, Any], video_ext: Any = None) -> Dict[str, Any]:
    return _db().import_payload(name, payload, video_ext)


def import_stream(name: str, common_path: str, files_iter: Iterable[Dict[str, Any]], batch_size: int = 20000, video_ext: Any = None) -> Dict[str, Any]:
    return _db().import_stream(name, common_path, files_iter, batch_size, video_ext)


def delete_source(name: str) -> bool:
    return _db().delete_source(name)


def list_sources() -> List[Dict[str, Any]]:
    return _db().list_sources()


def totals() -> Dict[str, Any]:
    return _db().totals()


def search(q: str, page: int, size: int, cat: str = "", sub: str = "", libs: Optional[List[str]] = None,
           media_type: str = "", genre: str = "", region: str = "", decade: int = 0, sort: str = "",
           language: str = "", air_status: str = "", resolution: str = "", edition: str = "",
           rating: float = 0):
    return _db().search(q, page, size, cat, sub, libs, media_type, genre, region, decade, sort,
                        language, air_status, resolution, edition, rating)


def facets(media_type: str = "", genre: str = "", region: str = "", decade: int = 0,
           libs: Optional[List[str]] = None, q: str = "", language: str = "", air_status: str = "",
           resolution: str = "", edition: str = "", rating: float = 0) -> Dict[str, Any]:
    return _db().facets(media_type, genre, region, decade, libs, q, language, air_status,
                        resolution, edition, rating)


def categories(libs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    return _db().categories(libs)


def list_files(dirname: str) -> Optional[Dict[str, Any]]:
    return _db().list_files(dirname)


def enrich_stats() -> Dict[str, int]:
    return _db().enrich_stats()


def pending_works(limit: int = 20) -> List[Dict[str, Any]]:
    return _db().pending_works(limit)


def apply_enrichment(dirname: str, fields: Dict[str, Any]) -> None:
    return _db().apply_enrichment(dirname, fields)


def mark_enrich_failure(dirname: str, max_attempts: int = 3) -> None:
    return _db().mark_enrich_failure(dirname, max_attempts)


def reset_enrichment(only_failed: bool = True) -> int:
    return _db().reset_enrichment(only_failed)


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
