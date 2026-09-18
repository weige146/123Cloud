"""影库播放模块测试：季/集解析、播放记录表、播放编排（转存/播放列表/续播/看完移回收站）、
mpv IPC 进度监听纯逻辑、路由（点播/播放列表/302 直链/进度/结构/季代理/配置新键）。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app import library_playback, main, movie_library_db
from app.movie_library import natural_key, order_play_items, parse_season_episode

# 模块导入时的原版 detect_player（各测试类的 setUp 会把它整体 mock 掉，分支测试用这份）
REAL_DETECT_PLAYER = library_playback.detect_player

WORK_TV = "剧集/欧美/权力的游戏 (2011) {tmdb-1399}"
WORK_MOVIE = "电影/外语/海王 (2018) {tmdb-297802}"


def _etag(i: int) -> str:
    return f"{i:032x}"


def _payload(common_path: str, files) -> dict:
    return {"commonPath": common_path, "usesBase62EtagsInExport": False,
            "files": [{"path": p, "etag": e, "size": s} for p, e, s in files]}


class _FakePlayClient:
    """记录调用的假 123 OpenAPI 客户端。"""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.reuse_calls = []
        self.trashed = []
        self.next_id = 9000

    async def ensure_path(self, root, parts):
        return "100:" + "/".join(str(p) for p in parts)

    async def list_files(self, parent):
        return [{"type": 0, "name": f["name"], "size": f["size"], "fileId": f["fileId"]}
                for f in self.existing]

    async def md5_reuse(self, parent, name, etag, size):
        self.reuse_calls.append((parent, name, etag, size))
        if name.startswith("miss"):
            return None
        self.next_id += 1
        return self.next_id

    async def trash_files(self, ids):
        self.trashed.extend(int(i) for i in ids)

    async def download_info(self, file_id):
        return f"https://dl.example.com/{int(file_id)}"


def _make_service(client, cfg=None, spawned=None):
    async def provider():
        return client
    return library_playback.PlaybackService(
        client_provider=provider,
        config_provider=lambda: dict(cfg or {}),
        spawn=(lambda cmd, **kw: spawned.append(cmd)),
    )


class SeasonEpisodeParsingTests(unittest.TestCase):
    def test_common_patterns(self):
        cases = [
            ("剧/Game {tmdb-1}/Season 1/Show.S01E02.mkv", (1, 2)),
            ("剧/Game {tmdb-1}/Season 1/Show.s1e10.mkv", (1, 10)),
            ("剧/Game {tmdb-1}/Season 1/Show.S01.E03.mkv", (1, 3)),
            ("剧/Game {tmdb-1}/Season 2/第03集.mp4", (2, 3)),
            ("剧/Game {tmdb-1}/Season 2/第 3 集.mp4", (2, 3)),
            ("剧/Game {tmdb-1}/Show 1x05.mkv", (1, 5)),
            ("剧/Game {tmdb-1}/Season 1/EP12.mp4", (1, 12)),
            ("剧/Game {tmdb-1}/Season 1/E04.mkv", (1, 4)),
            ("剧/Game {tmdb-1}/S02/E01.mkv", (2, 1)),
            ("剧/Game {tmdb-1}/第二季/E05.mkv", (2, 5)),
            ("剧/Game {tmdb-1}/Season 1/第2季 第03集.mp4", (2, 3)),
            ("剧/Game {tmdb-1}/第十一集.mkv", (1, 11)),
            ("剧/Game {tmdb-1}/Season 03/Show.S01E02E03.mkv", (1, 2)),
        ]
        for path, expected in cases:
            self.assertEqual(parse_season_episode(path), expected, path)

    def test_no_false_positive(self):
        for path in [
            "电影/海王 (2018) {tmdb-1}/Se7en.1995.1080p.mkv",
            "电影/海王 (2018) {tmdb-1}/Wallpaper.1920x1080.mp4",
            "电影/海王 (2018) {tmdb-1}/complete.mkv",
        ]:
            self.assertEqual(parse_season_episode(path), (None, None), path)

    def test_natural_key(self):
        self.assertLess(natural_key("E2.mkv"), natural_key("E10.mkv"))


class OrderPlayItemsTests(unittest.TestCase):
    def _files(self):
        return [
            {"path": f"{WORK_TV}/Season 1/GoT.S01E02.mkv", "fileName": "GoT.S01E02.mkv", "etag": _etag(2), "size": 200, "isVideo": True},
            {"path": f"{WORK_TV}/Season 1/GoT.S01E01.720p.mkv", "fileName": "GoT.S01E01.720p.mkv", "etag": _etag(11), "size": 50, "isVideo": True},
            {"path": f"{WORK_TV}/Season 1/GoT.S01E01.mkv", "fileName": "GoT.S01E01.mkv", "etag": _etag(1), "size": 100, "isVideo": True},
            {"path": f"{WORK_TV}/Season 2/GoT.S02E01.mkv", "fileName": "GoT.S02E01.mkv", "etag": _etag(3), "size": 300, "isVideo": True},
            {"path": f"{WORK_TV}/Season 1/poster.jpg", "fileName": "poster.jpg", "etag": _etag(4), "size": 5, "isVideo": False},
            {"path": f"{WORK_TV}/Season 1/Special.mkv", "fileName": "Special.mkv", "etag": _etag(5), "size": 60, "isVideo": True},
        ]

    def test_series_grouping_and_version_pick(self):
        ordered = order_play_items(self._files())
        self.assertTrue(ordered["isSeries"])
        self.assertEqual(sorted(ordered["seasons"]), [1, 2])
        season1 = ordered["seasons"][1]
        self.assertEqual([e["episode"] for e in season1], [1, 2])
        # 同集多版本：默认条目取体积最大，其余是备选
        self.assertEqual(season1[0]["file"]["fileName"], "GoT.S01E01.mkv")
        self.assertEqual([a["fileName"] for a in season1[0]["alternates"]], ["GoT.S01E01.720p.mkv"])

    def test_movie_picks_largest(self):
        files = [
            {"path": f"{WORK_MOVIE}/Aquaman.2018.1080p.mkv", "fileName": "Aquaman.2018.1080p.mkv", "etag": _etag(1), "size": 100, "isVideo": True},
            {"path": f"{WORK_MOVIE}/Aquaman.2018.2160p.mkv", "fileName": "Aquaman.2018.2160p.mkv", "etag": _etag(2), "size": 300, "isVideo": True},
        ]
        ordered = order_play_items(files)
        self.assertFalse(ordered["isSeries"])
        self.assertEqual(ordered["standalone"]["fileName"], "Aquaman.2018.2160p.mkv")


class PlaybackDbTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(Path(self._directory.name) / "cloud123.db")
        movie_library_db.import_payload("库.json", _payload("剧集/", [
            (f"{WORK_TV}/Season 1/GoT.S01E01.mkv", _etag(1), 100),
            (f"{WORK_TV}/Season 1/GoT.S01E02.mkv", _etag(2), 200),
        ]))

    def test_upsert_list_roundtrip(self):
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1,
                                         cloud_file_id=11, position_sec=60, duration_sec=600)
        # 只改一个字段，其余保留
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, position_sec=90)
        records = movie_library_db.list_playback(WORK_TV)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["cloudFileId"], 11)
        self.assertEqual(records[0]["positionSec"], 90)
        self.assertFalse(records[0]["watched"])
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, watched=True)
        self.assertTrue(movie_library_db.get_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv")["watched"])

    def test_summaries_and_latest(self):
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1,
                                         cloud_file_id=11, position_sec=60, watched=True)
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E02.mkv", 1, 2,
                                         cloud_file_id=12, position_sec=120)
        summaries = movie_library_db.playback_summaries([WORK_TV, "不存在的作品"])
        summary = summaries[WORK_TV]
        self.assertEqual(summary["recordCount"], 2)
        self.assertEqual(summary["watchedCount"], 1)
        self.assertEqual((summary["lastSeason"], summary["lastEpisode"]), (1, 2))
        self.assertFalse(summary["lastWatched"])
        # next = 下一个没看的集：E01 已看，E02 有记录未看 → next 指向 E02
        self.assertEqual((summary["nextSeason"], summary["nextEpisode"]), (1, 2))
        # 历史脏数据：season/episode 被清 0 的已看记录，重开库（迁移）后按路径修复、next 算对
        import sqlite3 as _sq
        conn = _sq.connect(Path(self._directory.name) / "cloud123.db")
        conn.execute(
            "UPDATE library_playback SET season = 0, episode = 0 WHERE file_path LIKE '%S01E01%'")
        conn.commit()
        conn.close()
        movie_library_db.init(Path(self._directory.name) / "cloud123.db")  # 重开触发迁移修复
        fixed = {r["filePath"]: r for r in movie_library_db.list_playback(WORK_TV)}
        e01 = fixed[f"{WORK_TV}/Season 1/GoT.S01E01.mkv"]
        self.assertEqual((e01["season"], e01["episode"]), (1, 1))
        summary2 = movie_library_db.playback_summaries([WORK_TV])[WORK_TV]
        self.assertEqual((summary2["nextSeason"], summary2["nextEpisode"]), (1, 2))
        works = movie_library_db.latest_playback_works(5)
        self.assertEqual(works[0]["dir"], WORK_TV)
        self.assertEqual(works[0]["title"], "权力的游戏")
        movie_library_db.clear_playback(WORK_TV)
        self.assertEqual(movie_library_db.list_playback(WORK_TV), [])


class PlaybackServiceTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_db = movie_library_db._default_db
        self.addCleanup(setattr, movie_library_db, "_default_db", self._original_db)
        movie_library_db.init(Path(self._directory.name) / "cloud123.db")
        movie_library_db.import_payload("库.json", _payload("", [
            (f"{WORK_TV}/Season 1/GoT.S01E01.mkv", _etag(1), 100),
            (f"{WORK_TV}/Season 1/GoT.S01E02.mkv", _etag(2), 200),
            (f"{WORK_TV}/Season 1/miss.S01E03.mkv", _etag(3), 300),
            (f"{WORK_TV}/Season 2/GoT.S02E01.mkv", _etag(4), 400),
            (f"{WORK_MOVIE}/Aquaman.2018.2160p.mkv", _etag(5), 500),
            (f"{WORK_MOVIE}/Aquaman.2018.1080p.mkv", _etag(6), 300),
        ]))
        # 固定播放器检测与启动（不打真播放器）
        self._player = {"kind": "vlc", "path": "/usr/bin/vlc", "label": "VLC"}
        patcher = unittest.mock.patch.object(library_playback, "detect_player", return_value=dict(self._player))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_play_transfers_and_builds_playlist(self):
        client = _FakePlayClient(existing=[{"name": "GoT.S01E01.mkv", "size": 100, "fileId": 777}])
        spawned = []
        service = _make_service(client, {"transferIntervalMs": 0}, spawned)
        result = asyncio.run(service.start_play(WORK_TV, base_url="http://127.0.0.1:8000"))
        # E01 网盘已有复用，E02 秒传，E03 miss 剔除 → 播放列表 2 条
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["transferred"], 1)
        self.assertEqual(result["itemCount"], 2)
        self.assertEqual(result["startLabel"], "S01E01")
        # 播放器拿到的是本地 m3u 文件（IINA/Infuse 对命令行 URL 的打开路径不可靠）
        playlist_path = spawned[0][-1]
        self.assertTrue(playlist_path.endswith(f"{result['sessionId']}.m3u"))
        self.assertIn("#EXTM3U", open(playlist_path, encoding="utf-8").read())
        m3u = service.playlist_m3u(result["sessionId"])
        self.assertIn("#EXTM3U", m3u)
        self.assertIn("权力的游戏 S01E02 · GoT.S01E02.mkv", m3u)
        self.assertIn(f"/api/library/play/{result['sessionId']}/1", m3u)
        # 未命中的文件不在播放列表里
        self.assertNotIn("miss.S01E03", m3u)
        self.assertEqual(len([l for l in m3u.splitlines() if l.startswith("http")]), 2)
        # 播放记录只落开播的第一集（E01 已有、复用 fileId 777），没播到的 E02 不写
        records = {r["filePath"]: r for r in movie_library_db.list_playback(WORK_TV)}
        self.assertEqual(records[f"{WORK_TV}/Season 1/GoT.S01E01.mkv"]["cloudFileId"], 777)
        self.assertNotIn(f"{WORK_TV}/Season 1/GoT.S01E02.mkv", records)

    def test_start_play_episode_all_missed_raises(self):
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        # E03 是秒传未命中文件：从它起播时转存全落空 → 明确报错
        with self.assertRaises(ValueError):
            asyncio.run(service.start_play(WORK_TV, episode=3, base_url="x"))

    def test_start_play_episode_slice(self):
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(WORK_TV, episode=2, base_url="http://127.0.0.1:8000"))
        self.assertEqual(result["itemCount"], 1)
        self.assertEqual(result["startLabel"], "S01E02")

    def test_start_play_season_two(self):
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(WORK_TV, season=2, base_url="http://127.0.0.1:8000"))
        self.assertEqual(result["season"], 2)
        self.assertEqual(result["itemCount"], 1)
        with self.assertRaises(ValueError):
            asyncio.run(service.start_play(WORK_TV, season=9, base_url="x"))

    def test_start_play_resume_skips_watched(self):
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1,
                                         cloud_file_id=777, watched=True)
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E02.mkv", 1, 2,
                                         cloud_file_id=778, position_sec=600, duration_sec=0)
        service = _make_service(_FakePlayClient(), {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(WORK_TV, resume=True, base_url="http://127.0.0.1:8000"))
        self.assertEqual(result["startLabel"], "S01E02")
        self.assertEqual(result["resumeSeconds"], 600)

    def test_start_play_movie(self):
        client = _FakePlayClient()
        spawned = []
        service = _make_service(client, {"transferIntervalMs": 0}, spawned)
        result = asyncio.run(service.start_play(WORK_MOVIE, base_url="http://127.0.0.1:8000"))
        self.assertEqual(result["itemCount"], 1)
        m3u = service.playlist_m3u(result["sessionId"])
        self.assertIn("Aquaman.2018.2160p.mkv", m3u)

    def test_start_play_file_path_promotes_alternate(self):
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(
            WORK_MOVIE, file_path=f"{WORK_MOVIE}/Aquaman.2018.1080p.mkv", base_url="x"))
        m3u = service.playlist_m3u(result["sessionId"])
        self.assertIn("Aquaman.2018.1080p.mkv", m3u)

    def test_start_play_file_path_crosses_season(self):
        # 文件列表里点「播放此文件」选的是第 2 季的文件：应切到第 2 季起播
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(
            WORK_TV, file_path=f"{WORK_TV}/Season 2/GoT.S02E01.mkv", base_url="x"))
        self.assertEqual(result["season"], 2)
        self.assertEqual(result["itemCount"], 1)

    def test_start_play_unknown_file_raises(self):
        service = _make_service(_FakePlayClient(), {"transferIntervalMs": 0}, [])
        with self.assertRaises(ValueError):
            asyncio.run(service.start_play(WORK_TV, file_path="不存在的文件.mkv", base_url="x"))

    def test_detect_player_custom_paths(self):
        # .app 分支只在 macOS 生效：CI 是 Linux，必须显式 mock platform.system
        with unittest.mock.patch.object(library_playback.platform, "system", return_value="Darwin"), \
                unittest.mock.patch.object(library_playback.os.path, "isdir", return_value=True), \
                unittest.mock.patch.object(library_playback.os.path, "isfile", side_effect=lambda p: p.endswith("Contents/MacOS/Infuse")), \
                unittest.mock.patch.object(library_playback.shutil, "which", return_value=None):
            detected = REAL_DETECT_PLAYER("/Applications/Infuse.app")
            # 「系统默认」也放进 mac mock 块：Linux 下会回退 xdg-open
            system_mac = REAL_DETECT_PLAYER("system")
        self.assertEqual(detected["kind"], "infuse")  # Infuse 特判：单集模式 + 官方跳转协议
        self.assertEqual(detected["label"], "Infuse")
        self.assertEqual(system_mac["kind"], "system")
        self.assertEqual(system_mac["path"], "/usr/bin/open")
        # 选了不存在的程序 → 空（start_play 会直接报错，不白转存）
        self.assertEqual(REAL_DETECT_PLAYER("/Applications/不存在.app")["kind"], "")

    def test_detect_player_iina_app_uses_cli(self):
        """选 IINA.app：自动改用内置 iina-cli（open -a 方式打不开网络流，报「找不到流」）"""
        def isfile_side(path):
            return path.endswith(("Contents/MacOS/IINA", "Contents/MacOS/iina-cli"))
        with unittest.mock.patch.object(library_playback.platform, "system", return_value="Darwin"), \
                unittest.mock.patch.object(library_playback.os.path, "isdir", return_value=True), \
                unittest.mock.patch.object(library_playback.os.path, "isfile", side_effect=isfile_side), \
                unittest.mock.patch.object(library_playback.shutil, "which", return_value=None):
            detected = REAL_DETECT_PLAYER("/Applications/IINA.app")
        self.assertEqual(detected["kind"], "iina")
        self.assertEqual(detected["path"], "/Applications/IINA.app/Contents/MacOS/iina-cli")

    def test_detect_player_windows_custom_exe(self):
        # Windows 自定义播放器：选一个 exe（含非 mpv/vlc 的名字，如 PotPlayer）→ other，命令 = [exe, 地址]
        with unittest.mock.patch.object(library_playback.platform, "system", return_value="Windows"), \
                unittest.mock.patch.object(library_playback.os.path, "isfile", return_value=True):
            detected = REAL_DETECT_PLAYER("D:\\Players\\PotPlayer\\PotPlayerMini64.exe")
        self.assertEqual(detected["kind"], "other")
        self.assertIn("PotPlayerMini64", detected["label"])
        with unittest.mock.patch.object(library_playback.platform, "system", return_value="Windows"), \
                unittest.mock.patch.object(library_playback.os.path, "isfile", return_value=True):
            mpv_exe = REAL_DETECT_PLAYER("C:\\tools\\mpv.exe")
        self.assertEqual(mpv_exe["kind"], "mpv")
        # Windows 的「系统默认」走 cmd start
        with unittest.mock.patch.object(library_playback.platform, "system", return_value="Windows"):
            system_win = REAL_DETECT_PLAYER("system")
        self.assertEqual(system_win["kind"], "system")
        self.assertEqual(system_win["path"], "cmd")

    def test_start_play_no_player_fails_before_transfer(self):
        # 没装播放器：立即报错，一个文件都不许转存（修「卡一会没动静」）
        with unittest.mock.patch.object(library_playback, "detect_player", return_value={"kind": "", "path": "", "label": ""}):
            client = _FakePlayClient()
            service = _make_service(client, {"transferIntervalMs": 0}, [])
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(service.start_play(WORK_TV, base_url="x"))
        self.assertIn("播放器", str(ctx.exception))
        self.assertEqual(client.reuse_calls, [])

    def test_start_play_infuse_single_episode(self):
        """Infuse：只转存/播起播那一集，命令走官方 x-callback 跳转协议"""
        infuse = {"kind": "infuse", "path": "/Applications/Infuse.app", "label": "Infuse"}
        client = _FakePlayClient()
        spawned = []
        service = _make_service(client, {"transferIntervalMs": 0}, spawned)
        with unittest.mock.patch.object(library_playback, "detect_player", return_value=infuse):
            result = asyncio.run(service.start_play(WORK_TV, base_url="http://127.0.0.1:8000"))
        self.assertEqual(result["player"], "infuse")
        self.assertEqual(result["itemCount"], 1)
        self.assertIn("连播", result["note"])
        command = spawned[0]
        self.assertEqual(command[0], "open")
        self.assertTrue(command[1].startswith("infuse://x-callback-url/play?url="))
        self.assertIn("127.0.0.1%3A8000", command[1])
        # 只转存了 1 个文件（E01），E02 没转
        self.assertEqual(len(client.reuse_calls), 1)

    def test_resolve_item_web_fallback(self):
        """OpenAPI 取直链失败 → 降级网页版接口（authorToken + etag/s3KeyFlag）"""
        class FailingClient:
            async def download_info(self, file_id):
                raise RuntimeError("OpenAPI 频控")

        service = _make_service(FailingClient(), {}, [])
        service._web_session_provider = lambda: {"authorToken": "tok", "loginUuid": "uuid"}
        service.sessions["s"] = {
            "sessionId": "s", "dir": "x", "title": "T", "season": 1, "isSeries": True,
            "items": [{"index": 0, "path": "p", "fileName": "a.mkv", "season": 1, "episode": 1,
                       "size": 10, "etag": "ab" * 16, "s3KeyFlag": "flag", "cloudFileId": 42}],
            "baseUrl": "http://127.0.0.1", "createdAt": __import__("time").time(),
            "alive": True, "currentIndex": 0, "positionSec": 0,
            "playerKind": "mpv", "playerLabel": "mpv", "sockPath": "",
        }
        captured = {}

        async def fake_web(author_token, login_uuid, **kwargs):
            captured.update(authorToken=author_token, loginUuid=login_uuid, **kwargs)
            return "https://web-dl.example.com/x"

        with unittest.mock.patch.object(library_playback, "web_download_url", fake_web):
            url = asyncio.run(service.resolve_item("s", 0))
        self.assertEqual(url, "https://web-dl.example.com/x")
        self.assertEqual(captured["authorToken"], "tok")
        self.assertEqual(captured["loginUuid"], "uuid")
        self.assertEqual(captured["file_id"], 42)
        self.assertEqual(captured["s3_key_flag"], "flag")

    def test_resolve_item_all_sources_fail(self):
        """两条路都取不到直链：报错带两边原因"""

        class FailingClient:
            async def download_info(self, file_id):
                raise RuntimeError("OpenAPI 挂了")

        service = _make_service(FailingClient(), {}, [])
        service._web_session_provider = lambda: None  # 没有网页登录态存档
        service.sessions["s"] = {
            "sessionId": "s", "dir": "x", "title": "T", "season": 1, "isSeries": True,
            "items": [{"index": 0, "path": "p", "fileName": "a.mkv", "season": 1, "episode": 1,
                       "size": 10, "etag": "ab" * 16, "s3KeyFlag": "", "cloudFileId": 42}],
            "baseUrl": "http://127.0.0.1", "createdAt": __import__("time").time(),
            "alive": True, "currentIndex": 0, "positionSec": 0,
            "playerKind": "mpv", "playerLabel": "mpv", "sockPath": "",
        }
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(service.resolve_item("s", 0))
        self.assertIn("OpenAPI", str(ctx.exception))

    def test_mark_watched_until(self):
        # 「看到第 N 集」：1..N 已看（新标已看的回收）、N 之后取消已看，其他季不动
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, cloud_file_id=777)
        client = _FakePlayClient()
        service = _make_service(client, {}, [])
        result = asyncio.run(service.mark_watched_until(WORK_TV, 1, 2))
        self.assertEqual(result["markedWatched"], 2)   # E1（有转存记录但未看）与 E2（无记录）都新标
        self.assertEqual(result["trashed"], 1)         # E1 转存过的文件回收
        records = {r["filePath"]: r for r in movie_library_db.list_playback(WORK_TV)}
        self.assertTrue(records[f"{WORK_TV}/Season 1/GoT.S01E01.mkv"]["watched"])
        self.assertEqual(records[f"{WORK_TV}/Season 1/GoT.S01E01.mkv"]["cloudFileId"], 0)
        self.assertTrue(records[f"{WORK_TV}/Season 1/GoT.S01E02.mkv"]["watched"])
        self.assertNotIn(f"{WORK_TV}/Season 2/GoT.S02E01.mkv", records)  # 其他季不受影响
        # 往回标：看到第 1 集 → E2 取消已看
        result2 = asyncio.run(service.mark_watched_until(WORK_TV, 1, 1))
        self.assertEqual(result2["markedUnwatched"], 1)
        self.assertFalse(movie_library_db.get_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E02.mkv")["watched"])
        # 没有的一季报错
        with self.assertRaises(ValueError):
            asyncio.run(service.mark_watched_until(WORK_TV, 9, 1))

    def test_resolve_item_and_cache(self):
        client = _FakePlayClient()
        service = _make_service(client, {"transferIntervalMs": 0}, [])
        result = asyncio.run(service.start_play(WORK_TV, base_url="http://127.0.0.1:8000"))
        url = asyncio.run(service.resolve_item(result["sessionId"], 1))
        self.assertEqual(url, f"https://dl.example.com/{client.next_id}")
        with self.assertRaises(ValueError):
            asyncio.run(service.resolve_item("不存在的会话", 0))

    def test_mark_watched_trashes_cloud_file(self):
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1,
                                         cloud_file_id=777, position_sec=60)
        client = _FakePlayClient()
        service = _make_service(client, {}, [])
        asyncio.run(service.mark_watched(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", True))
        self.assertEqual(client.trashed, [777])
        record = movie_library_db.get_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv")
        self.assertTrue(record["watched"])
        self.assertEqual(record["cloudFileId"], 0)
        # 关掉自动清理后不再移回收站
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E02.mkv", 1, 2, cloud_file_id=778)
        service_off = _make_service(_FakePlayClient(), {"autoTrash": False}, [])
        asyncio.run(service_off.mark_watched(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E02.mkv", True))
        self.assertEqual(service_off.sessions, {})

    def test_handle_watched_trashes_item(self):
        client = _FakePlayClient()
        service = _make_service(client, {}, [])
        result = asyncio.run(service.start_play(WORK_TV, base_url="x"))
        session = service.sessions[result["sessionId"]]
        item = session["items"][0]
        item["cloudFileId"] = 555
        asyncio.run(service._handle_watched(session, item))
        self.assertIn(555, client.trashed)
        self.assertEqual(item["cloudFileId"], 0)


class MpvIpcMonitorTests(unittest.TestCase):
    def _monitor(self, items):
        updates, watched = [], []

        def on_update(item, position, duration):
            updates.append((item["index"], position, duration))

        def on_watched(item):
            watched.append(item["index"])

        monitor = library_playback.MpvIpcMonitor(
            "/tmp/不存在.sock", items, on_update=on_update, on_watched=on_watched, on_exit=lambda: None)
        return monitor, updates, watched

    def test_percent_marks_watched_once(self):
        items = [{"index": 0, "path": "a", "season": 1, "episode": 1, "fileName": "a.mkv", "cloudFileId": 0}]
        monitor, updates, watched = self._monitor(items)
        monitor._apply_poll({1: 0, 2: 100.0, 3: 1000.0, 4: 10.0})
        self.assertEqual(watched, [])
        monitor._apply_poll({1: 0, 2: 930.0, 3: 1000.0, 4: 93.0})
        self.assertEqual(watched, [0])
        monitor._apply_poll({1: 0, 2: 950.0, 3: 1000.0, 4: 95.0})
        self.assertEqual(watched, [0])  # 不重复
        self.assertEqual(updates[-1], (0, 950.0, 1000.0))

    def test_index_advance_marks_previous_watched(self):
        items = [{"index": i, "path": f"p{i}", "season": 1, "episode": i + 1, "fileName": f"e{i}.mkv", "cloudFileId": 0}
                 for i in range(3)]
        monitor, _, watched = self._monitor(items)
        monitor._apply_poll({1: 0, 2: 10.0, 3: 100.0, 4: 10.0})
        monitor._apply_poll({1: 1, 2: 5.0, 3: 100.0, 4: 5.0})
        self.assertEqual(watched, [0])

    def test_position_ratio_marks_watched(self):
        items = [{"index": 0, "path": "a", "season": 1, "episode": 1, "fileName": "a.mkv", "cloudFileId": 0}]
        monitor, _, watched = self._monitor(items)
        monitor._apply_poll({1: 0, 2: 950.0, 3: 1000.0, 4: 95.0})
        self.assertEqual(watched, [0])

    def test_out_of_range_index_ignored(self):
        items = [{"index": 0, "path": "a", "season": 1, "episode": 1, "fileName": "a.mkv", "cloudFileId": 0}]
        monitor, updates, _ = self._monitor(items)
        monitor._apply_poll({1: 99, 2: 1.0, 3: 1.0, 4: 1.0})
        monitor._apply_poll({1: None, 2: 1.0})
        self.assertEqual(updates, [])

    def test_build_player_command(self):
        player = {"kind": "mpv", "path": "/usr/bin/mpv", "label": "mpv"}
        command = library_playback.build_player_command(player, "http://x/pl.m3u", "/tmp/s.sock", 600)
        self.assertIn("--input-ipc-server=/tmp/s.sock", command)
        self.assertIn("--start=600", command)
        self.assertIn("--ytdl=no", command)  # 禁用 youtube-dl（IINA 内置版在部分机器挂死会卡住拉流）
        self.assertNotIn("--start=600", library_playback.build_player_command(player, "u", "/tmp/s.sock", 10))
        iina = library_playback.build_player_command(
            {"kind": "iina", "path": "/Applications/IINA.app/Contents/MacOS/iina-cli", "label": "IINA"},
            "http://x/pl.m3u", "/tmp/s.sock", 300)
        self.assertTrue(any(arg.startswith("--mpv-input-ipc-server=") for arg in iina))
        self.assertIn("--mpv-start=300", iina)
        self.assertIn("--no-stdin", iina)
        self.assertIn("--mpv-ytdl=no", iina)
        vlc = library_playback.build_player_command(
            {"kind": "vlc", "path": "/usr/bin/vlc", "label": "VLC"}, "http://x/pl.m3u", "", 90)
        self.assertIn("--start-time=90", vlc)
        # .app 应用包（Infuse 等）与系统默认：走 open，无 IPC
        app_cmd = library_playback.build_player_command(
            {"kind": "app", "path": "/Applications/Infuse.app", "label": "Infuse"}, "http://x/pl.m3u", "", 0)
        self.assertEqual(app_cmd, ["open", "-a", "/Applications/Infuse.app", "http://x/pl.m3u"])
        sys_cmd = library_playback.build_player_command(
            {"kind": "system", "path": "/usr/bin/open", "label": "系统默认播放器"}, "http://x/pl.m3u", "", 0)
        self.assertEqual(sys_cmd, ["/usr/bin/open", "http://x/pl.m3u"])


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", authorization: str = "", base_url: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("Client", (), {"host": host})()
        self.base_url = base_url or "http://127.0.0.1:8000/"


class PlaybackRouteTests(unittest.TestCase):
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
        movie_library_db.import_payload("库.json", _payload("剧集/", [
            (f"{WORK_TV}/Season 1/GoT.S01E01.mkv", _etag(1), 100),
            (f"{WORK_TV}/Season 1/GoT.S01E02.mkv", _etag(2), 200),
            (f"{WORK_MOVIE}/Aquaman.2018.2160p.mkv", _etag(5), 500),
        ]))
        # 播放服务换上假客户端/假播放器，不打真播放器与真网盘
        service = main.playback_service
        self._original = (service._client_provider, service._config_provider, service._spawn)
        self.addCleanup(lambda: (setattr(service, "_client_provider", self._original[0]),
                                 setattr(service, "_config_provider", self._original[1]),
                                 setattr(service, "_spawn", self._original[2]),
                                 service.sessions.clear()))
        self.client = _FakePlayClient()
        self.spawned = []

        async def provider():
            return self.client

        service._client_provider = provider
        service._config_provider = lambda: {"transferIntervalMs": 0}
        service._spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.service = service
        player = {"kind": "vlc", "path": "/usr/bin/vlc", "label": "VLC"}
        patcher = unittest.mock.patch.object(library_playback, "detect_player", return_value=player)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._auth_patcher = unittest.mock.patch.object(main, "_authorized_pan123_client", provider)
        self._auth_patcher.start()
        self.addCleanup(self._auth_patcher.stop)

    def test_play_start_route(self):
        result = asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["itemCount"], 2)
        self.assertEqual(len(self.spawned), 1)

    def test_play_start_requires_auth(self):
        async def deny():
            raise RuntimeError("未授权")

        with unittest.mock.patch.object(main, "_authorized_pan123_client", deny):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(main.start_library_play(
                    main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest()))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_play_start_token_guard(self):
        asyncio.run(main.write_library_config(main.LibraryConfigRequest(token="t" * 24), _StubRequest()))
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.start_library_play(
                main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest(host="10.0.0.2")))
        self.assertEqual(ctx.exception.status_code, 401)
        ok = asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV, token="t" * 24), _StubRequest(host="10.0.0.2")))
        self.assertTrue(ok["ok"])

    def test_playlist_and_redirect_routes(self):
        result = asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest()))
        sid = result["sessionId"]
        response = asyncio.run(main.library_play_playlist(sid))
        self.assertIn("audio/x-mpegurl", response.media_type)
        self.assertIn("#EXTM3U", response.body.decode("utf-8"))
        with self.assertRaises(HTTPException):
            asyncio.run(main.library_play_playlist("不存在的会话"))
        redirect = asyncio.run(main.library_play_redirect(sid, 0))
        self.assertEqual(redirect.status_code, 302)
        self.assertTrue(str(redirect.headers["location"]).startswith("https://dl.example.com/"))
        with self.assertRaises(HTTPException):
            asyncio.run(main.library_play_redirect(sid, 99))

    def test_structure_route(self):
        result = asyncio.run(main.read_library_play_structure(_StubRequest(), dir=WORK_TV))
        self.assertTrue(result["isSeries"])
        self.assertEqual([s["season"] for s in result["seasons"]], [1])
        episodes = result["seasons"][0]["episodes"]
        self.assertEqual([e["episode"] for e in episodes], [1, 2])
        self.assertTrue(all(not e["watched"] for e in episodes))
        # 播过以后结构里带已看与断点
        asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV, filePath=f"{WORK_TV}/Season 1/GoT.S01E01.mkv"),
            _StubRequest()))
        movie_library_db.upsert_playback(WORK_TV, f"{WORK_TV}/Season 1/GoT.S01E01.mkv", 1, 1, watched=True)
        result = asyncio.run(main.read_library_play_structure(_StubRequest(), dir=WORK_TV))
        self.assertTrue(result["seasons"][0]["episodes"][0]["watched"])

    def test_progress_and_mark_routes(self):
        asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest()))
        progress = asyncio.run(main.read_library_progress(_StubRequest(), dir=WORK_TV))
        self.assertEqual(len(progress["records"]), 1)  # 只落开播的第一集
        self.assertIsNotNone(progress["active"])
        mark = asyncio.run(main.mark_library_progress(
            main.LibraryProgressMarkRequest(dir=WORK_TV, filePath=f"{WORK_TV}/Season 1/GoT.S01E01.mkv"),
            _StubRequest()))
        self.assertTrue(mark["ok"])
        records = {r["filePath"]: r for r in movie_library_db.list_playback(WORK_TV)}
        self.assertTrue(records[f"{WORK_TV}/Season 1/GoT.S01E01.mkv"]["watched"])

    def test_summary_and_continue_routes(self):
        asyncio.run(main.start_library_play(
            main.LibraryPlayStartRequest(dir=WORK_TV), _StubRequest()))
        summary = asyncio.run(main.read_library_progress_summary(_StubRequest(), dirs="|".join([WORK_TV, WORK_MOVIE])))
        self.assertEqual(summary["summaries"][WORK_TV]["recordCount"], 1)
        cont = asyncio.run(main.read_library_progress_continue(_StubRequest()))
        self.assertEqual(cont["items"][0]["dir"], WORK_TV)
        self.assertEqual(cont["items"][0]["summary"]["recordCount"], 1)

    def test_tmdb_season_route_with_cache(self):
        fixture = {"season_number": 1, "name": "第 1 季", "poster_path": "/abc.jpg",
                   "episodes": [{"episode_number": 1, "name": "凛冬将至", "air_date": "2011-04-17"}]}

        async def fake_fetch(tmdb_id, season):
            return fixture

        with unittest.mock.patch.object(main, "_tmdb_fetch_season", fake_fetch):
            result = asyncio.run(main.library_tmdb_season(_StubRequest(), tmdb_id=1399, season=1))
        self.assertTrue(result["ok"])
        self.assertEqual(result["season"]["episodes"][0]["name"], "凛冬将至")
        self.assertTrue(result["season"]["posterUrl"].endswith("/abc.jpg"))
        # 第二次命中 kv 缓存（fetch 换成抛错也能返回）
        async def deny(*_a, **_k):
            raise AssertionError("不应再请求 TMDB")

        with unittest.mock.patch.object(main, "_tmdb_fetch_season", deny):
            cached = asyncio.run(main.library_tmdb_season(_StubRequest(), tmdb_id=1399, season=1))
        self.assertEqual(cached["season"]["name"], "第 1 季")

    def test_config_new_playback_keys(self):
        saved = asyncio.run(main.write_library_config(main.LibraryConfigRequest(
            playerPath="/opt/homebrew/bin/mpv", autoTrash=False, playCachePath="我的播放"), _StubRequest()))
        self.assertEqual(saved["config"]["playerPath"], "/opt/homebrew/bin/mpv")
        self.assertFalse(saved["config"]["autoTrash"])
        self.assertEqual(saved["config"]["playCachePath"], "我的播放")
        # 无参 PUT = 全部保留（部分字段保存不误清其他设置）
        again = asyncio.run(main.write_library_config(main.LibraryConfigRequest(), _StubRequest()))
        self.assertEqual(again["config"]["playerPath"], "/opt/homebrew/bin/mpv")
        self.assertFalse(again["config"]["autoTrash"])
        self.assertEqual(again["config"]["playCachePath"], "我的播放")
        # 显式空串 = 清空（播放器回自动检测、缓存目录回默认「秒传」）；未传的 autoTrash 保持 False
        cleared = asyncio.run(main.write_library_config(
            main.LibraryConfigRequest(playerPath="", playCachePath=""), _StubRequest()))
        self.assertEqual(cleared["config"]["playerPath"], "")
        self.assertFalse(cleared["config"]["autoTrash"])
        self.assertEqual(cleared["config"]["playCachePath"], "秒传")

    def test_config_partial_update_keeps_player(self):
        """只更新别的字段（如导出目录）：播放器/自动回收/缓存目录不被误清（重进就丢的 BUG）"""
        asyncio.run(main.write_library_config(main.LibraryConfigRequest(
            playerPath="/Applications/IINA.app", autoTrash=True, playCachePath="秒传"), _StubRequest()))
        saved = asyncio.run(main.write_library_config(main.LibraryConfigRequest(
            exportDir="/tmp/导出"), _StubRequest()))
        self.assertEqual(saved["config"]["exportDir"], "/tmp/导出")
        self.assertEqual(saved["config"]["playerPath"], "/Applications/IINA.app")
        self.assertTrue(saved["config"]["autoTrash"])
        self.assertEqual(saved["config"]["playCachePath"], "秒传")

    def test_config_legacy_play_cache_path_migrates(self):
        # 早期本地构建存过「影库播放」的，自动迁移成默认「秒传」，不再另建目录
        saved = asyncio.run(main.write_library_config(main.LibraryConfigRequest(
            playCachePath="影库播放"), _StubRequest()))
        self.assertEqual(saved["config"]["playCachePath"], "秒传")


if __name__ == "__main__":
    unittest.main()
