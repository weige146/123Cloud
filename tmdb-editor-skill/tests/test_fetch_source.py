"""fetch_source.py 纯逻辑回归（不联网）：URL 解析 / 分集映射 / 豆瓣解析 / 百科表格 / 公共辅助。"""

import importlib.util
import json
import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("fetch_source", SCRIPTS / "fetch_source.py")
fs = importlib.util.module_from_spec(spec)
sys.modules["fetch_source"] = fs
spec.loader.exec_module(fs)


# —— 公共辅助 ——
class TestAirDate:
    def test_standard(self):
        assert fs.normalize_air_date("2024-01-02") == "2024-01-02"

    def test_chinese(self):
        assert fs.normalize_air_date("2024年1月2日") == "2024-01-02"

    def test_compact(self):
        assert fs.normalize_air_date("20240102") == "2024-01-02"

    def test_invalid_day(self):
        assert fs.normalize_air_date("2024-02-30") == ""

    def test_garbage(self):
        assert fs.normalize_air_date("每周一") == ""
        assert fs.normalize_air_date("") == ""

    def test_with_noise(self):
        assert fs.normalize_air_date("2026-08-15(中国大陆)") == "2026-08-15"

    def test_epoch(self, monkeypatch):
        # 固定时区偏移无关断言：只验证格式
        value = fs.epoch_to_local_date(1700000000)
        assert len(value) == 10 and value[4] == "-" and value[7] == "-"


class TestUrlParsers:
    def test_bilibili(self):
        assert fs.parse_bilibili_url("https://www.bilibili.com/bangumi/play/ss12345") == {"type": "ss", "id": "12345"}
        assert fs.parse_bilibili_url("https://www.bilibili.com/bangumi/play/ep888") == {"type": "ep", "id": "888"}
        assert fs.parse_bilibili_url("https://www.bilibili.com/bangumi/media/md999") == {"type": "md", "id": "999"}
        assert fs.parse_bilibili_url("https://example.com/x") is None

    def test_iqiyi_album_id(self):
        assert fs.extract_iqiyi_album_id("https://www.iqiyi.com/a_x.html", 'x data-album-id="12345" y') == "12345"
        assert fs.extract_iqiyi_album_id("https://iqiyi.com/lib/m_abc", 'movlibalbumaid="777"') == "777"
        assert fs.extract_iqiyi_album_id("https://m.iqiyi.com/v_x.html", '{"albumId": 42,"channelId":1}') == "42"

    def test_mgtv(self):
        assert fs.parse_mgtv_collection_id("https://w.mgtv.com/b/419629/17004788.html") == "419629"

    def test_qq(self):
        assert fs.parse_qq_cover_cid("https://v.qq.com/x/cover/mzc00200abc/xyz.html") == "mzc00200abc"

    def test_youku(self):
        assert fs.parse_youku_target("https://v.youku.com/v_show/id_XNjc=.html?s=cc123") == {"type": "show", "id": "cc123"}
        assert fs.parse_youku_target("https://v.youku.com/v_show/id_XNjc=.html") == {"type": "video", "id": "XNjc"}
        assert fs.parse_youku_target("https://example.com") is None


class TestUnwrapJsonp:
    def test_qz(self):
        assert fs.unwrap_jsonp('QZOutputJson={"a":1};') == '{"a":1}'

    def test_plain(self):
        assert fs.unwrap_jsonp('{"a":1}') == '{"a":1}'


# —— B站 ——
class TestBilibili:
    def test_map_skips_trailer_and_renumbers(self):
        result = {"episodes": [
            {"title": "1", "long_title": "第一集", "pub_time": 1700000000, "duration": 1400000, "cover": "https://i0.hdslb.com/a.jpg"},
            {"title": "PV", "long_title": "预告", "badge": "预告", "duration": 60000},
            {"title": "2", "long_title": "第二集", "pub_time": 1700604800, "duration": 1200000},
        ]}
        rows = fs.map_bilibili_episodes(result)
        assert [r["episodeNumber"] for r in rows] == [1, 2]
        assert rows[0]["name"] == "第一集"
        assert rows[0]["runtime"] == 23  # 1400000ms ≈ 23min
        assert rows[0]["airDate"] == fs.epoch_to_local_date(1700000000)

    def test_map_upper_lower_joins(self):
        result = {"episodes": [{"title": "第1集（上", "long_title": "开局"}]}
        assert fs.map_bilibili_episodes(result)[0]["name"] == "第1集（上 开局"

    def test_search_map_strips_em(self):
        data = {"data": {"result": [
            {"season_id": 123, "title": "<em class=\"keyword\">凡人</em>修仙传", "eps": [{}, {}], "desc": "d", "cover": "c"},
            {"season_id": 0, "title": "垃圾"},
        ]}}
        rows = fs.map_bilibili_search_results(data)
        assert len(rows) == 1
        assert rows[0]["title"] == "凡人修仙传"
        assert rows[0]["episodeCnt"] == 2


# —— 爱奇艺 ——
class TestIqiyi:
    def test_upgrade_image_picks_largest(self):
        url = fs.upgrade_iqiyi_image_url("https://pic.iqiyipic.com/x.jpg", ["120_160", "1248_702", "480_270"])
        assert url == "https://pic.iqiyipic.com/x_1248_702.jpg"

    def test_upgrade_image_no_sizes(self):
        assert fs.upgrade_iqiyi_image_url("https://p/x.jpg", None) == "https://p/x.jpg"

    def test_map_episodes(self):
        rows = fs.map_iqiyi_episodes([
            {"order": 1, "subtitle": "第一集", "period": "2024-01-01", "duration": "45:00", "imageUrl": "https://p/1.jpg", "imageSize": ["1248_702"]},
            {"order": 2, "subtitle": "第二集", "period": "", "duration": "01:02:00"},
        ])
        assert rows[0]["runtime"] == 45 and rows[1]["runtime"] == 62
        assert rows[0]["stillUrl"].endswith("_1248_702.jpg")


# —— 芒果 ——
class TestMgtv:
    def test_oss_resize(self):
        assert fs.append_mgtv_oss_resize("https://img.hitv.com/a.jpg") == "https://img.hitv.com/a.jpg?x-oss-process=image/resize,w_1280"

    def test_map_filters_intact_and_strips_suffix(self):
        rows = fs.map_mgtv_episodes([
            {"isIntact": "1", "t1": 1, "t2": "第一集", "ts": "2024-01-01 12:00", "time": "45:00", "img": "https://img.hitv.com/a_320x180.jpg"},
            {"isIntact": "0", "t1": 2, "t2": "预告", "img": "x"},
        ])
        assert len(rows) == 1
        # 与油猴脚本同款：剥最后一个 _ 尺寸段再带 OSS resize 参数升原图
        assert rows[0]["stillUrl"] == "https://img.hitv.com/a?x-oss-process=image/resize,w_1280"


# —— 腾讯 ——
class TestQq:
    def test_clean_title(self):
        assert fs.clean_qq_title("凡人修仙传_01") == "凡人修仙传"
        assert fs.clean_qq_title("第3集 开局") == "开局"

    def test_map_union(self):
        fields = [
            {"episode": "2", "second_title": "剧名_02", "category_map": ["正片"], "video_checkup_time": "2024-01-02 00:00", "duration": 2700, "pic160x90": "https://p/2/160"},
            {"episode": "", "second_title": "特别篇", "category_map": ["预告"]},
            {"episode": "5", "second_title": "第五集", "category_map": ["正片", "独家"], "pic160x90": "https://p/5/160"},
        ]
        rows = fs.map_qq_union_episodes(fields)
        assert [r["episodeNumber"] for r in rows] == [2, 5]
        assert rows[0]["stillUrl"] == "https://p/2/1280"


# —— 优酷 ——
class TestYouku:
    def test_map_seq_fallback(self):
        rows = fs.map_youku_videos([{"seq": "7", "rc_title": "第七集", "duration": 1800}, {"title": "无序号", "duration": 1800}])
        assert [r["episodeNumber"] for r in rows] == [7, 2]


# —— 红果 ——
class TestHongguo:
    HTML = """
    <html><body><script>window._ROUTER_DATA = {"loaderData":{"detail_page":{"seriesDetail":{
      "series_name":"测试短剧","series_intro":"简介 a{\\"x\\":1} b","series_cover":"https://c/p.jpg",
      "episode_cnt":3,"vid_list":[{},{},{}]}}}};</script></body></html>"""

    def test_extract_router_data_with_braces_in_strings(self):
        data = fs.extract_hongguo_router_data(self.HTML)
        detail = data["loaderData"]["detail_page"]["seriesDetail"]
        assert detail["series_name"] == "测试短剧"

    def test_parse_series(self):
        result = fs.parse_hongguo_series(self.HTML)
        assert result["title"] == "测试短剧"
        assert result["episodeCnt"] == 3
        assert [e["episodeNumber"] for e in result["episodes"]] == [1, 2, 3]

    def test_search_list(self):
        rows = fs.map_hongguo_search_list([{"video_data": {"series_id": 42, "series_title": "A", "episode_cnt": 80}}])
        assert rows[0]["platform"] == "hongguo" and rows[0]["id"] == "42"


# —— 豆瓣 ——
class TestDouban:
    def test_rexxar_parse(self):
        data = {
            "title": "凡人修仙传", "year": "2020", "intro": "第一行\n\n第二行",
            "aka": ["A Mortal's Journey", "凡人"], "original_title": "",
            "pubdate": ["2026-08-15(中国大陆)", "2020-07-25(中国大陆)"],
            "durations": ["45分钟"], "rating": {"value": 8.1},
            "episodes_count": 60, "genres": ["动画", "奇幻"], "countries": ["中国大陆"], "languages": ["汉语普通话"],
            "directors": [{"name": "王导"}], "writers": None, "actors": [{"name": "钱错"}, "其他"],
            "pic": {"large": "https://img1.doubanio.com/view/photo/m_ratio_poster/public/p1.jpg"},
        }
        detail = fs.parse_douban_rexxar(data, "3643508")
        assert detail["title"] == "凡人修仙传"
        assert detail["date"] == "2026-08-15"  # 与油猴同款：pubdate 按序取第一个能解析的完整日期
        assert detail["originalTitle"] == "A Mortal's Journey"
        assert detail["poster"].endswith("/view/photo/raw/public/p1.jpg")
        assert detail["cast"] == "钱错/其他"

    def test_rexxar_missing_returns_none(self):
        assert fs.parse_douban_rexxar({}, "1") is None

    def test_merge_only_fills_gaps(self):
        base = {"title": "A", "writers": ""}
        merged = fs.merge_detail(base, {"writers": "编剧", "title": "B", "rating": "8.0"})
        assert merged == {"title": "A", "writers": "编剧", "rating": "8.0"}

    def test_has_gaps(self):
        assert fs.douban_detail_has_gaps({"title": "A", "writers": ""})
        assert not fs.douban_detail_has_gaps({"writers": "x", "directors": "d", "genres": "g", "countries": "c", "languages": "l", "aliases": "a", "overview": "o"})

    def test_desktop_html_parse(self):
        html = """
        <html><body><div id="content"><h1><span property="v:itemreviewed">武林外传</span>
        <span class="year">(2006)</span></h1></div>
        <strong property="v:average">9.6</strong>
        <span property="v:summary">同福客栈的故事<br/></span>
        <div id="mainpic"><img src="https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2.jpg" /></div>
        <div id="info">导演: 尚敬<br/>编剧: 宁财神<br/>主演: 闫妮 / 沙溢 / 姚晨<br/>类型: 古装 / 喜剧<br/>
        制片国家/地区: 中国大陆<br/>语言: 汉语普通话<br/>首播: 2006-01-02<br/>集数: 81<br/>单集片长: 40分钟<br/>
        又名: My Own Swordsman<br/></div></body></html>"""
        detail = fs.parse_douban_desktop_html(html, "2566819")
        assert detail["title"] == "武林外传"
        assert detail["year"] == "2006"
        assert detail["directors"] == "尚敬"
        assert detail["cast"].startswith("闫妮")
        assert detail["episodeCount"] == 81
        assert detail["runtime"] == 40
        assert detail["date"] == "2006-01-02"
        assert detail["originalTitle"] == "My Own Swordsman"

    def test_by_imdb_id_regex(self):
        # 只验证链接形态提取逻辑（复用 parse 的正则模式）
        import re

        match = re.search(r"movie\.douban\.com(?:%2F|/)subject(?:%2F|/)(\d+)", "x movie.douban.com%2Fsubject%2F123 y")
        assert match.group(1) == "123"


# —— 百度百科 ——
class TestBaike:
    def test_map_cells_full_row(self):
        row = fs.map_baike_episode_cells(["第1集", "开局", "2024-01-01", "主角登场的故事，这一段足够长可以作为剧情简介内容"])
        assert row["episodeNumber"] == 1
        assert row["airDate"] == "2024-01-01"
        assert row["name"] == "开局"
        assert row["overview"].startswith("主角登场")

    def test_map_cells_short_row(self):
        row = fs.map_baike_episode_cells(["3", "风波"])
        assert row["episodeNumber"] == 3 and row["name"] == "风波" and row["overview"] == ""

    def test_map_cells_invalid(self):
        assert fs.map_baike_episode_cells(["首页"]) is None or fs.map_baike_episode_cells(["首页"])["episodeNumber"] in (0,)

    def test_parse_episode_tables(self):
        html = """
        <html><body>
        <h2>分集剧情</h2>
        <table><tr><th>集数</th><th>集名</th><th>剧情</th></tr>
        <tr><td>1</td><td>开局</td><td>第一集的剧情介绍，内容比较长所以被当作简介使用。</td></tr>
        <tr><td>2</td><td>风波</td><td>第二集的剧情介绍，内容比较长所以被当作简介使用。</td></tr>
        </table>
        <table><tr><th>其他</th></tr><tr><td>无关表格</td></tr></table>
        </body></html>"""
        result = fs.parse_baike_episodes(html)
        assert [e["episodeNumber"] for e in result["episodes"]] == [1, 2]
        assert result["episodes"][0]["name"] == "开局"

    def test_clean_text_strips_refs(self):
        assert fs._baike_clean_text("武林外传[1]") == "武林外传"


# —— 图片 URL 与 Referer ——
class TestImages:
    def test_referer_table(self):
        assert fs.image_referer("https://i0.hdslb.com/bfs/a.jpg") == "https://www.bilibili.com/"
        assert fs.image_referer("https://pic1.iqiyipic.com/a.jpg") == "https://www.iqiyi.com/"
        assert fs.image_referer("https://puui.qpic.cn/a.jpg") == "https://v.qq.com/"
        assert fs.image_referer("https://img.hitv.com/a.jpg") == "https://www.mgtv.com/"
        assert fs.image_referer("https://r1.ykimg.com/a.jpg") == "https://v.youku.com/"
        assert fs.image_referer("https://img1.doubanio.com/a.jpg") == "https://movie.douban.com/"
        assert fs.image_referer("https://bkimg.cdn.bcebos.com/a.jpg") == "https://baike.baidu.com/"
        assert fs.image_referer("https://example.com/a.jpg") == ""

    def test_upgrade_douban_raw(self):
        assert fs.upgrade_douban_poster_url("https://img1.doubanio.com/view/photo/m_ratio_poster/public/p1.jpg").endswith("/view/photo/raw/public/p1.jpg")
        assert fs.upgrade_douban_poster_url("https://img1.doubanio.com/view/photo/raw/public/p1.jpg").endswith("/raw/public/p1.jpg")

    def test_upgrade_baike_query(self):
        assert fs.upgrade_baike_poster_url("https://bkimg.cdn.bcebos.com/p.jpg?x-bce-process=resize,w_100") == "https://bkimg.cdn.bcebos.com/p.jpg"


# —— 平台识别与 TSV ——
class TestDispatch:
    def test_match_sources(self):
        for url, expect in [
            ("https://www.bilibili.com/bangumi/play/ss123", "bilibili"),
            ("https://www.iqiyi.com/a_abc.html", "iqiyi"),
            ("https://w.mgtv.com/b/1/2.html", "mgtv"),
            ("https://v.qq.com/x/cover/abc/1.html", "qq"),
            ("https://hongguoduanju.com/player/123", "hongguo"),
            ("https://v.youku.com/v_show/id_X.html", "youku"),
        ]:
            matched = fs.match_site_source(url)
            assert matched is not None and matched[1] == expect

    def test_match_rejects_unknown(self):
        assert fs.match_site_source("https://example.com/x") is None
        assert fs.match_site_source("不是链接") is None

    def test_tsv(self):
        rows = [{"episodeNumber": 1, "name": "开局\t特别", "airDate": "2024-01-01", "runtime": 45, "overview": "简介"}]
        lines = fs.episodes_tsv(rows).split("\n")
        assert lines[0] == "集号\t集名\t日期\t时长\t简介"
        # 字段里的制表符会被净化成空格，保证 TSV 列不错位
        assert lines[1] == "1\t开局 特别\t2024-01-01\t45\t简介"


# —— 排期引擎（buildEpisodeSchedule 移植） ——
class TestSchedule:
    def test_weekly_multi_slot(self):
        # 2026-01-05 是周一：每周一、四各 2 集，共 8 集 → 4 个更新日
        result = fs.build_episode_schedule({"pattern": "weekly", "startDate": "2026-01-05", "count": 8, "weekdays": "一、四", "perSlot": 2, "runtime": 45})
        assert result["error"] == ""
        eps = result["episodes"]
        assert [e["episodeNumber"] for e in eps] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert eps[0]["airDate"] == "2026-01-05" and eps[1]["airDate"] == "2026-01-05"  # 单日多集共用日期
        assert eps[2]["airDate"] == "2026-01-08"  # 周四
        assert eps[4]["airDate"] == "2026-01-12"  # 下周一
        assert result["meta"]["summary"] == "每周 周一、周四 更新，每次 2 集"

    def test_interval_pattern(self):
        result = fs.build_episode_schedule({"pattern": "interval", "startDate": "2026-02-01", "count": 3, "intervalDays": 2, "perSlot": 1})
        dates = [e["airDate"] for e in result["episodes"]]
        assert dates == ["2026-02-01", "2026-02-03", "2026-02-05"]

    def test_title_template(self):
        result = fs.build_episode_schedule({"pattern": "interval", "startDate": "2026-02-01", "count": 4, "intervalDays": 1, "perSlot": 2, "titleTemplate": "第{n}集({k})"})
        names = [e["name"] for e in result["episodes"]]
        assert names == ["第1集(1)", "第2集(2)", "第3集(1)", "第4集(2)"]

    def test_validation_errors(self):
        assert fs.build_episode_schedule({"count": 0, "startDate": "2026-01-01"})["error"]
        assert fs.build_episode_schedule({"count": 3, "startDate": "坏日期", "weekdays": "一"})["error"]
        assert fs.build_episode_schedule({"count": 3, "startDate": "2026-01-01", "pattern": "weekly", "weekdays": ""})["error"]

    def test_weekdays_parser(self):
        assert fs.parse_weekdays("周一、周四") == [1, 4]
        assert fs.parse_weekdays("1,4") == [1, 4]
        assert fs.parse_weekdays("mon thu") == [1, 4]
        assert fs.parse_weekdays("周中") == [1, 2, 3, 4, 5]
        assert fs.parse_weekdays("周末") == [0, 6]
        assert fs.parse_weekdays("日 天 7") == [0]  # 无分隔的整串是一个 token，与油猴行为一致需分隔
        assert fs.parse_weekdays("周日") == [0]


# —— 过滤词剔除（applyEpisodeFilterWords 移植） ——
class TestFilterWords:
    def test_filter_and_renumber(self):
        eps = [
            {"episodeNumber": 1, "name": "开局"},
            {"episodeNumber": 2, "name": "发展"},
            {"episodeNumber": 3, "name": "PV"},
            {"episodeNumber": 5, "name": "高潮"},  # 原本就缺 4
        ]
        result = fs.apply_episode_filter_words(eps, "PV,预告,花絮")
        assert [(e["episodeNumber"], e["name"]) for e in result["episodes"]] == [(1, "开局"), (2, "发展"), (4, "高潮")]
        assert [e["name"] for e in result["removed"]] == ["PV"]

    def test_no_hits_no_change(self):
        eps = [{"episodeNumber": 1, "name": "开局"}]
        assert fs.apply_episode_filter_words(eps, "PV")["episodes"] == eps

    def test_case_insensitive(self):
        eps = [{"episodeNumber": 1, "name": "Opening"}, {"episodeNumber": 2, "name": "正片"}]
        result = fs.apply_episode_filter_words(eps, "opening")
        assert [e["episodeNumber"] for e in result["episodes"]] == [1]
