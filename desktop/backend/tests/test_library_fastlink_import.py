"""影库「123 助手式」秒传导入回归：粘贴多条切段、二级链接识别与联网展开。

覆盖三层：
* `movie_library` 的纯文本函数（切段 / 来源名 / 种子判定）
* `library_fastlink_import` 的展开流程（转存 → 下载 → 回收，含残留复用、失败隔离、体积护栏）
* `main` 的导入路由与小文件接线（未授权只坏这一条、其余照常入库；非种子绝不碰网盘）
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from fastapi import HTTPException

from app import library_fastlink_import as seeds
from app import main, movie_library, movie_library_db

SEED_ETAG = "2qAQE3V0d1nZB9xK2pLmA"          # base62 MD5（123 助手二级链接里的种子 etag）
WORK_ETAG = "3rBQF6W1e2oAC0yL3qMnB"
WORK_DIR = "剧集/国产/凡人修仙传 (2020) {tmdb-96675}"


def _seed_link(name: str = "凡人修仙传 2020.123fastlink.json", etag: str = SEED_ETAG, size: int = 35970) -> str:
    return f"123FLCPV2$%{etag}#{size}#{name}"


def _fastlink_text() -> str:
    return (
        f"123FLCPV2$%{WORK_ETAG}#100#{WORK_DIR}/Season 1/FxxS01E01.mkv$"
        f"{WORK_ETAG}b#200#{WORK_DIR}/Season 1/FxxS01E02.mkv"
    )


def _reader(text: str):
    """假下载：expand_payload/fetch_seed_text 注入的 read_text 必须是协程函数。"""
    async def read(url):
        return text
    return read


def _flaky_reader(first: str, rest: str):
    calls = {"n": 0}

    async def read(url):
        calls["n"] += 1
        return first if calls["n"] == 1 else rest
    return read


def _seed_payload_json() -> str:
    """种子文件的内容：标准 123FastLink JSON（hex etag）。"""
    return json.dumps({
        "name": "凡人修仙传 2020",
        "commonPath": WORK_DIR,
        "usesBase62EtagsInExport": False,
        "files": [
            {"etag": f"{1:032x}", "size": 100, "path": "Season 1/FxxS01E01.mkv"},
            {"etag": f"{2:032x}", "size": 200, "path": "Season 1/FxxS01E02.mkv"},
            {"etag": f"{3:032x}", "size": 300, "path": "Season 1/FxxS01E03.mkv"},
        ],
    }, ensure_ascii=False)


_UNSET = object()


class _FakeClient:
    """假 123 OpenAPI 客户端：记录调用顺序，可注入「秒传未命中 / 目录里有残留 / 下载报错」。"""

    def __init__(self, *, reuse_id=8801, existing=None, reuse_result=_UNSET, download_error=None, list_error=None):
        self.calls = []
        self.reuse_id = reuse_id
        # 显式传 None 表示「秒传未命中」，不传才回落到 reuse_id
        self.reuse_result = reuse_id if reuse_result is _UNSET else reuse_result
        self.existing = existing or []
        self.download_error = download_error
        self.list_error = list_error
        self.trashed = []

    async def ensure_path(self, root, parts):
        self.calls.append(("ensure_path", tuple(parts)))
        return "700"

    async def list_files(self, parent_id):
        self.calls.append(("list_files", parent_id))
        if self.list_error:
            raise RuntimeError(self.list_error)
        return list(self.existing)

    async def md5_reuse(self, parent_file_id, filename, etag, size):
        self.calls.append(("md5_reuse", parent_file_id, filename, etag, size))
        return self.reuse_result

    async def download_info(self, file_id):
        self.calls.append(("download_info", file_id))
        if self.download_error:
            raise RuntimeError(self.download_error)
        return f"https://cdn.example/{file_id}"

    async def trash_files(self, file_ids):
        self.calls.append(("trash_files", tuple(file_ids)))
        self.trashed.extend(file_ids)


class FastlinkTextHelpersTests(unittest.TestCase):
    def test_split_multiple_pasted_payloads(self):
        text = "\n".join([_seed_link(), _fastlink_text(), _seed_link("另一部.123fastlink.txt")])
        segments = movie_library.split_fastlink_payloads(text)
        self.assertEqual(len(segments), 3, "秒传链接一行一条，各自成段")
        self.assertTrue(all(seg.startswith("123FLCPV2$") for seg in segments))

    def test_split_mixed_json_and_fastlink(self):
        payload = json.dumps({
            "name": "海王 (2018) {tmdb-297802}",
            "commonPath": "电影/外语/海王 (2018) {tmdb-297802}",
            "files": [{"etag": f"{7:032x}", "size": 10, "path": "Aquaman.mkv"}],
        }, ensure_ascii=False, indent=2)
        segments = movie_library.split_fastlink_payloads(f"{payload}\n{_fastlink_text()}\n")
        self.assertEqual(len(segments), 2, "多行美化 JSON 按括号配平整块取走")
        self.assertTrue(segments[0].startswith("{"))
        self.assertEqual(json.loads(segments[0])["name"], "海王 (2018) {tmdb-297802}")
        self.assertTrue(segments[1].startswith("123FLCPV2$"))

    def test_single_payload_returns_input_unchanged(self):
        text = _fastlink_text()
        self.assertEqual(movie_library.split_fastlink_payloads(text), [text.strip()])
        self.assertEqual(movie_library.split_fastlink_payloads("   "), [])
        # 截断 JSON：剩余内容全归本段，交给解析器按原样报错（不静默吞掉）
        broken = '{"name":"x","files":[{"etag":"a","size":1,"path":"a.mkv"'
        self.assertEqual(movie_library.split_fastlink_payloads(broken), [broken])

    def test_seed_entry_detection(self):
        parsed = movie_library.parse_library_content(_seed_link())
        seed = movie_library.fastlink_seed_entry(parsed)
        self.assertIsNotNone(seed, "单条且文件名像种子 → 判定二级链接")
        self.assertEqual(seed["fileName"], "凡人修仙传 2020.123fastlink.json")
        self.assertEqual(len(parsed["files"]), 1)
        # 真只有 1 个视频文件的作品不误判；多条也不判
        self.assertIsNone(movie_library.fastlink_seed_entry(
            {"files": [{"fileName": "Aquaman.2018.mkv", "etag": "a", "size": 1}]}))
        self.assertIsNone(movie_library.fastlink_seed_entry(movie_library.parse_library_content(_fastlink_text())))
        self.assertTrue(movie_library.looks_like_fastlink_seed_text(_seed_link()))
        self.assertFalse(movie_library.looks_like_fastlink_seed_text(_fastlink_text()),
                         "粗筛不含种子记号时不必二次解析（文件夹批量导入靠它省开销）")

    def test_payload_labels(self):
        self.assertEqual(movie_library.fastlink_payload_label(_seed_link()), "凡人修仙传 2020",
                         "二级链接的来源名取种子文件名、去掉 .123fastlink 后缀")
        with_common = f"123FLCPV2${WORK_DIR}%{WORK_ETAG}#100#Season 1/FxxS01E01.mkv"
        self.assertEqual(movie_library.fastlink_payload_label(with_common), "凡人修仙传 (2020) {tmdb-96675}",
                         "带公共路径的秒传链接用公共路径末段当来源名，比首个文件名好认")
        self.assertEqual(movie_library.fastlink_payload_label(_fastlink_text()), "FxxS01E01.mkv",
                         "没有公共路径时退回首个文件名")
        self.assertEqual(
            movie_library.fastlink_payload_label(json.dumps({"name": "海王 {tmdb-1}", "files": []})), "海王 {tmdb-1}")


class SeedTempPathTests(unittest.TestCase):
    def test_default_parts(self):
        self.assertEqual(seeds.seed_temp_parts({}), ["秒传", seeds.SEED_TEMP_SUBDIR])

    def test_custom_base_split(self):
        self.assertEqual(seeds.seed_temp_parts({"seedTempPath": "影库/临时/"}), ["影库", "临时", seeds.SEED_TEMP_SUBDIR])
        self.assertEqual(seeds.seed_temp_parts({"seedTempPath": "../x"}), ["x", seeds.SEED_TEMP_SUBDIR],
                         "越界段剔除，但仍固定在临时子目录里")


class FetchSeedTextTests(unittest.TestCase):
    def test_transfer_download_then_trash(self):
        client = _FakeClient()
        seen = {}

        async def read(url):
            seen["url"] = url
            return _seed_payload_json()

        text = asyncio.run(seeds.fetch_seed_text(
            movie_library.fastlink_seed_entry(movie_library.parse_library_content(_seed_link())),
            client=client, temp_parts=["秒传", "影库导入临时"], read_text=read))
        self.assertEqual(text, _seed_payload_json())
        self.assertEqual(client.trashed, [8801], "读完立刻把临时种子移入回收站")
        kinds = [call[0] for call in client.calls]
        self.assertEqual(kinds, ["ensure_path", "list_files", "md5_reuse", "download_info", "trash_files"])
        reuse = next(call for call in client.calls if call[0] == "md5_reuse")
        self.assertEqual(reuse[3], movie_library.etag_hex(SEED_ETAG), "base62 种子 etag 必须反解成 hex 才喂秒传")
        self.assertEqual(reuse[4], 35970)
        self.assertTrue(seen["url"].startswith("https://cdn.example/"))

    def test_reuses_leftover_instead_of_transferring_again(self):
        client = _FakeClient(existing=[{"type": 0, "name": "凡人修仙传 2020.123fastlink.json", "size": 35970, "fileId": 6001}])
        asyncio.run(seeds.fetch_seed_text(
            movie_library.fastlink_seed_entry(movie_library.parse_library_content(_seed_link())),
            client=client, temp_parts=["秒传", "影库导入临时"], read_text=_reader(_seed_payload_json())))
        self.assertNotIn("md5_reuse", [call[0] for call in client.calls],
                         "临时目录已有同名同体积残留时不再转存第二份（防「名字 (1)」副本）")
        self.assertEqual(client.trashed, [6001])

    def test_size_mismatch_leftover_is_not_reused(self):
        client = _FakeClient(existing=[{"type": 0, "name": "凡人修仙传 2020.123fastlink.json", "size": 1, "fileId": 6001}])
        asyncio.run(seeds.fetch_seed_text(
            movie_library.fastlink_seed_entry(movie_library.parse_library_content(_seed_link())),
            client=client, temp_parts=["秒传"], read_text=_reader(_seed_payload_json())))
        self.assertIn("md5_reuse", [call[0] for call in client.calls])

    def test_miss_raises_friendly_error(self):
        client = _FakeClient(reuse_result=None)
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(seeds.fetch_seed_text(
                movie_library.fastlink_seed_entry(movie_library.parse_library_content(_seed_link())),
                client=client, temp_parts=["秒传"], read_text=_reader("")))
        self.assertIn("秒传未命中", str(ctx.exception))
        self.assertEqual(client.trashed, [], "没转存成功就不该有回收动作")

    def test_download_failure_still_trashes(self):
        client = _FakeClient(download_error="网络抖动")
        with self.assertRaises(RuntimeError):
            asyncio.run(seeds.fetch_seed_text(
                movie_library.fastlink_seed_entry(movie_library.parse_library_content(_seed_link())),
                client=client, temp_parts=["秒传"], read_text=_reader("")))
        self.assertEqual(client.trashed, [8801], "读取失败也不能把临时种子留在网盘里")

    def test_broken_seed_metadata_rejected(self):
        with self.assertRaises(ValueError):
            asyncio.run(seeds.fetch_seed_text({"fileName": "x.123fastlink.json", "etag": "", "size": 0},
                                              client=_FakeClient(), temp_parts=["秒传"]))


class ReadUrlTextGuardTests(unittest.TestCase):
    @staticmethod
    def _run(handler, **kwargs):
        async def go():
            transport = httpx.MockTransport(handler)
            return await seeds.read_url_text("https://cdn.example/seed", transport=transport, **kwargs)
        return asyncio.run(go())

    def test_reads_body(self):
        self.assertEqual(self._run(lambda request: httpx.Response(200, text="hello")), "hello")

    def test_http_error_reported(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(lambda request: httpx.Response(403))
        self.assertIn("403", str(ctx.exception))

    def test_oversized_body_aborted(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(lambda request: httpx.Response(200, content=b"x" * 5000), max_bytes=1024)
        self.assertIn("超过", str(ctx.exception))


class ExpandPayloadTests(unittest.TestCase):
    def test_secondary_expands_to_real_list(self):
        client = _FakeClient()
        payload, notes = asyncio.run(seeds.expand_payload(
            movie_library.parse_library_content(_seed_link()),
            client=client, temp_parts=["秒传"], read_text=_reader(_seed_payload_json())))
        self.assertEqual(len(payload["files"]), 3)
        self.assertEqual({f["fileName"] for f in payload["files"]},
                         {"FxxS01E01.mkv", "FxxS01E02.mkv", "FxxS01E03.mkv"})
        self.assertEqual(payload["commonPath"], WORK_DIR, "种子内容里的 commonPath 要参与作品聚合根")
        self.assertIn("3 个条目", notes[0])

    def test_nested_secondary_resolves_recursively(self):
        client = _FakeClient()
        payload, notes = asyncio.run(seeds.expand_payload(
            movie_library.parse_library_content(_seed_link()), client=client, temp_parts=["秒传"],
            read_text=_flaky_reader(_seed_link("内层.123fastlink.json"), _fastlink_text())))
        self.assertEqual(len(payload["files"]), 2, "种子内容还是二级链接时继续展开一层")
        self.assertEqual(len(notes), 2)

    def test_plain_payload_untouched(self):
        client = _FakeClient()
        payload, notes = asyncio.run(seeds.expand_payload(
            movie_library.parse_library_content(_fastlink_text()), client=client, temp_parts=["秒传"],
            read_text=_reader("")))
        self.assertEqual(len(payload["files"]), 2)
        self.assertEqual(client.calls, [], "非二级链接绝不碰网盘")
        self.assertEqual(notes, [])


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("Client", (), {"host": host})()


class FastlinkImportRouteTests(unittest.TestCase):
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
        self._original_client = main._authorized_pan123_client
        self.addCleanup(setattr, main, "_authorized_pan123_client", self._original_client)
        self._original_read = seeds.read_url_text
        self.addCleanup(setattr, seeds, "read_url_text", self._original_read)

    def _use_fake_client(self, client, seed_text=None):
        """路由层拿不到注入点（展开器在路由内部构造），按惯例 monkeypatch 模块属性。"""
        async def factory():
            return client
        main._authorized_pan123_client = factory
        seeds.read_url_text = _reader(seed_text if seed_text is not None else _seed_payload_json())

    def _import(self, text, **kwargs):
        return asyncio.run(main.import_library_fastlink(
            main.LibraryImportFastlinkRequest(text=text, **kwargs), _StubRequest()))

    def test_paste_two_links_creates_two_sources(self):
        self._use_fake_client(_FakeClient())

        result = self._import("\n".join([_fastlink_text(), _seed_link()]))
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["failed"], 0, "普通秒传 + 可展开的二级链接都该成功")
        self.assertEqual({r["label"] for r in result["results"]}, {"FxxS01E01.mkv", "凡人修仙传 2020"})
        sources = {str(s["name"]) for s in movie_library_db.list_sources()}
        self.assertIn("凡人修仙传 2020", sources)

    def test_secondary_link_imports_expanded_files(self):
        client = _FakeClient()
        self._use_fake_client(client)
        result = self._import(_seed_link())
        self.assertTrue(result["results"][0]["ok"])
        self.assertEqual(result["added"], 1, "展开后按真实清单聚合成 1 部作品")
        works = movie_library_db.search("", 1, 10)[1]
        self.assertEqual(works[0]["dir"], WORK_DIR)
        self.assertEqual(len(movie_library_db.list_files(WORK_DIR)["files"]), 3)
        self.assertEqual(client.trashed, [8801], "临时种子已移入回收站")

    def test_missing_authorization_fails_only_that_segment(self):
        async def unauthorized():
            raise RuntimeError("尚未授权 123 开放平台")
        main._authorized_pan123_client = unauthorized
        result = self._import("\n".join([_fastlink_text(), _seed_link()]))
        self.assertEqual(result["failed"], 1)
        self.assertTrue(result["results"][0]["ok"], "普通秒传链接不碰网盘，照常入库")
        self.assertIn("123 网盘授权", result["results"][1]["error"])

    def test_expand_disabled_never_touches_cloud(self):
        client = _FakeClient()
        self._use_fake_client(client)
        result = self._import(_seed_link(), expand=False)
        self.assertEqual(client.calls, [], "关掉展开开关就一次网盘请求都不发")
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["added"], 0,
                         "不展开时二级链接只是那条种子记录本身，聚不出作品（旧语义），所以必须开展开"
        )

    def test_bad_segment_does_not_abort_batch(self):
        self._use_fake_client(_FakeClient())
        result = self._import("123FLCPV2$%notavalidetag\n" + _fastlink_text())
        self.assertEqual(result["failed"], 1)
        self.assertTrue(result["results"][1]["ok"])

    def test_empty_and_oversized_payloads_rejected(self):
        with self.assertRaises(HTTPException):
            self._import("   ")
        with self.assertRaises(HTTPException) as ctx:
            self._import(_fastlink_text() + "\n" * 0 + "x" * (seeds.MAX_PASTE_BYTES + 1))
        self.assertIn("过大", ctx.exception.detail)

    def test_too_many_segments_rejected(self):
        text = "\n".join(_seed_link(f"作品{i}.123fastlink.json") for i in range(seeds.MAX_PAYLOADS + 1))
        with self.assertRaises(HTTPException) as ctx:
            self._import(text)
        self.assertIn("分批", ctx.exception.detail)


class SmallFileWiringTests(unittest.TestCase):
    """影库文件/文件夹导入的小文件预检接线：二级链接展开，普通文件一字不变、绝不碰网盘。"""

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
        self._original_client = main._authorized_pan123_client
        self.addCleanup(setattr, main, "_authorized_pan123_client", self._original_client)
        self._original_read = seeds.read_url_text
        self.addCleanup(setattr, seeds, "read_url_text", self._original_read)

    def _write(self, name: str, content: str) -> str:
        path = Path(self._directory.name) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_plain_library_file_never_touches_cloud(self):
        async def explode():
            raise AssertionError("普通影库文件不该触发 123 授权与转存")
        main._authorized_pan123_client = explode
        path = self._write("普通库.json", _seed_payload_json())
        result = asyncio.run(main._import_library_file(
            path, "普通库.json", movie_library.video_ext_set(None), "merge", main._FastlinkSeedExpander()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["fileCount"], 3)

    def test_secondary_seed_file_expands_on_import(self):
        client = _FakeClient()

        async def factory():
            return client
        main._authorized_pan123_client = factory
        seeds.read_url_text = _reader(_seed_payload_json())
        path = self._write("凡人修仙传 2020.123fastlink.json", _seed_link())
        result = asyncio.run(main._import_library_file(
            path, "网盘文件夹", movie_library.video_ext_set(None), "merge", main._FastlinkSeedExpander()))
        self.assertEqual(result["fileCount"], 3, "按展开后的真实清单入库")
        self.assertEqual(result["name"], "网盘文件夹", "文件夹导入沿用共享来源名，不被种子名打散")
        self.assertEqual(client.trashed, [8801])

    def test_non_library_file_still_skipped(self):
        async def explode():
            raise AssertionError("普通影库文件不该触发 123 授权与转存")
        main._authorized_pan123_client = explode
        path = self._write("readme.txt", "这只是个说明文件")
        with self.assertRaises(ValueError):
            asyncio.run(main._import_library_file(
                path, "readme.txt", movie_library.video_ext_set(None), "merge", main._FastlinkSeedExpander()))

    def test_multi_entry_list_mentioning_seed_name_is_not_expanded(self):
        """清单里恰好有一个 `.123fastlink.json` 条目（有人把种子文件也一起导出了）：
        不是二级链接，必须照常整表入库、一次网盘请求都不发。"""
        async def explode():
            raise AssertionError("多条清单不该触发 123 授权与转存")
        main._authorized_pan123_client = explode
        data = json.loads(_seed_payload_json())
        data["files"].append({"etag": f"{9:032x}", "size": 10, "path": "凡人修仙传 2020.123fastlink.json"})
        path = self._write("混合库.json", json.dumps(data, ensure_ascii=False))
        result = asyncio.run(main._import_library_file(
            path, "混合库.json", movie_library.video_ext_set(None), "merge", main._FastlinkSeedExpander()))
        self.assertEqual(result["fileCount"], 4, "多条清单按原路径完整入库，不被种子名带偏")


if __name__ == "__main__":
    unittest.main()
