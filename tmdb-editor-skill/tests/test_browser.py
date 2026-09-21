"""browser.py 纯逻辑回归（不起浏览器）：响应分类 / URL 拼接 / 计划 JSON 形态。"""

import importlib.util
import json
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("browser", SCRIPTS / "browser.py")
br = importlib.util.module_from_spec(spec)
sys.modules["browser"] = br
spec.loader.exec_module(br)


class TestClassify:
    def test_auth(self):
        assert br.classify({"status": 401, "isJson": False, "textSnippet": ""}) == "auth"
        assert br.classify({"status": 403, "isJson": False, "textSnippet": ""}) == "auth"

    def test_waf_or_login(self):
        result = {"status": 200, "isJson": False, "textSnippet": "<html><body><form id='login'>"}
        assert br.classify(result) == "waf_or_login"

    def test_tmdb_validation(self):
        assert br.classify({"status": 200, "isJson": True, "data": {"failure": {"errors": ["分辨率不足"]}}}) == "tmdb_validation"

    def test_success_no_flag(self):
        assert br.classify({"status": 200, "isJson": True, "data": {"success": True}}) is None
        assert br.classify({"status": 200, "isJson": True, "data": {"id": 1}}) is None


class TestBuildUrl:
    def test_relative_with_query(self):
        assert br._build_url({"url": "/tv/1/remote/episodes", "query": {"translate": "false", "language": "zh-CN"}}, br.BASE) == \
            "https://www.themoviedb.org/tv/1/remote/episodes?translate=false&language=zh-CN"

    def test_relative_appends_query(self):
        assert br._build_url({"url": "/x?a=1", "query": {"b": "2"}}, br.BASE).endswith("/x?a=1&b=2")

    def test_absolute_and_no_query(self):
        assert br._build_url({"url": "https://www.themoviedb.org/image"}, br.BASE) == "https://www.themoviedb.org/image"


class TestQuote:
    def test_chinese_and_specials(self):
        assert br._quote("a b&c") == "a%20b%26c"
        assert br._quote("中文") == "%E4%B8%AD%E6%96%87"


class TestPlanShape:
    def test_ops_plan_json_example(self, tmp_path):
        """文档里的计划 JSON 必须能被 run_plan 的解析路径接受（不真连浏览器）。"""
        plan = {
            "intervalMs": 500,
            "ops": [
                {"op": "upload_config", "pageUrl": "/tv/330017/images/posters"},
                {"op": "fetch", "method": "GET", "url": "/tv/330017/season/1/remote/episodes?translate=false"},
                {"op": "fetch", "method": "POST", "url": "/tv/330017/season/1/remote/episodes",
                 "query": {"language": "zh-CN", "translate": "false"},
                 "data": {"episode_number": 2, "name": "第2集", "overview": "", "air_date": "", "runtime": 13, "locked_fields": []}},
                {"op": "upload", "url": "/image", "file": "poster.jpg",
                 "fields": {"media_id": "x", "media_type": "TvSeries", "type": "poster", "translate": "false"}},
            ],
        }
        text = json.dumps(plan, ensure_ascii=False)
        parsed = json.loads(text)
        assert parsed["ops"][1]["method"] == "GET"
        assert parsed["ops"][2]["data"]["episode_number"] == 2
        assert parsed["ops"][3]["fields"]["media_type"] == "TvSeries"

    def test_data_body_encoding_contract(self):
        """写请求 body 契约：data=URL编码JSON（与官方编辑器一致）。"""
        from urllib.parse import quote, unquote

        payload = {"episode_number": 2, "name": "第2集"}
        body = "data=" + quote(json.dumps(payload, ensure_ascii=False))
        assert body.startswith("data=%7B")
        assert json.loads(unquote(body[5:])) == payload


class TestScriptsExist:
    def test_js_snippets_present(self):
        for name in ("FETCH_JS", "UPLOAD_JS", "UPLOAD_CONFIG_JS", "LOGIN_CHECK_JS"):
            assert hasattr(br, name) and len(getattr(br, name)) > 40
        assert "'same-origin'" in br.FETCH_JS
        assert "upload_files" in br.UPLOAD_JS
        assert "media_id" in br.UPLOAD_CONFIG_JS
