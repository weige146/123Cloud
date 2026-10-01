"""v2 投稿路由规则引擎回归（submission_routing.decide_submission_channel）。

钉死：首条命中即停、条件间 AND/值内 OR、发布组三态（detected/stripped/none）、
v1 三槽迁移等价、手动优先与单频道短路、兜底链（fallbackChannelId → role → 默认）、
规则命中原因（决策说明）、路由词表 schema。
"""
from __future__ import annotations

import unittest

from app.submission import route_channel_label, select_submission_channel
from app.submission_routing import (
    decide_submission_channel,
    extract_routing_facts,
    normalize_routing,
    rule_matches,
    rule_summary,
    routing_schema,
)


CHANNELS = [
    {"id": "private", "title": "私有", "chatId": "-1001", "enabled": True, "role": "private"},
    {"id": "completed", "title": "公开-完结", "chatId": "-1002", "enabled": True, "role": "public_completed"},
    {"id": "updating", "title": "公开-更新", "chatId": "-1003", "enabled": True, "role": "public_updating"},
    {"id": "anime", "title": "动漫", "chatId": "-1004", "enabled": True, "role": ""},
    {"id": "off", "title": "停用", "chatId": "-1005", "enabled": False, "role": ""},
]


def tv_draft(release_group="", season_episodes=7, tmdb_episodes=12, media_type="tv", status="Returning Series", provider="123pan", extra=None):
    draft = {
        "ownerUserId": 123456,
        "share": {"provider": provider, "cleanUrl": "123FLCPV2$%f#1024#a.mkv" if provider == "123fastlink" else "https://www.123pan.com/s/x", "title": "京城奇探 (2026)"},
        "media": {"mediaType": media_type, "status": status, "tmdbId": 301345, "seasons": [{"seasonNumber": 1, "episodeCount": tmdb_episodes}]},
        "metadata": {"seasonEpisode": f"S01E01-E{season_episodes:02d}", "mediaType": media_type},
        "inspection": {"title": "京城奇探 (2026)", "fileNames": [f"Show.S01E{n:02d}.2026.2160p.WEB-DL-HiveWeb.mkv" for n in range(1, season_episodes + 1)]},
    }
    if release_group:
        draft["metadata"]["releaseGroup"] = release_group
    if extra:
        draft.update(extra)
    return draft


class RoutingFactsTests(unittest.TestCase):
    def test_basic_facts(self):
        facts = extract_routing_facts(tv_draft("HiveWeb"))
        self.assertEqual(facts["linkType"], "share")
        self.assertEqual(facts["mediaType"], "tv")
        self.assertEqual(facts["completion"], "updating")
        self.assertEqual(facts["releaseGroupState"], "detected")
        self.assertEqual(facts["releaseGroups"], ["hiveweb"])
        self.assertEqual(facts["tmdbId"], 301345)
        self.assertEqual(facts["submitter"], "self")

    def test_completed_and_movie_facts(self):
        self.assertEqual(extract_routing_facts(tv_draft("", 12, 12))["completion"], "completed")
        movie = extract_routing_facts(tv_draft("", media_type="movie", status="Ended"))
        self.assertEqual(movie["mediaType"], "movie")
        self.assertEqual(movie["completion"], "completed")

    def test_unknown_media_type_completion(self):
        draft = tv_draft()
        draft["media"] = {}
        draft["metadata"] = {"mediaType": "unknown"}
        facts = extract_routing_facts(draft)
        self.assertEqual(facts["mediaType"], "unknown")
        self.assertEqual(facts["completion"], "unknown")

    def test_release_group_tristate(self):
        # 脚本去组直投：即使 meta 带回原始组名，状态以 stripped 为准（频道主自己重发布=公开档），
        # 原始组名仍留在 releaseGroups 事实里供「stripped + 组∉白名单」这类细规则可选用
        with_meta = tv_draft(provider="123fastlink", extra={"routingMeta": {"releaseGroup": "WiKi", "releaseGroupStripped": "1", "skipReleaseGroup": "1"}})
        facts = extract_routing_facts(with_meta)
        self.assertEqual(facts["releaseGroupState"], "stripped")
        self.assertEqual(facts["releaseGroups"], ["wiki"], "原始组名仍保留在事实里")
        # 老脚本：只带 skipReleaseGroup、没有 meta 组名 → stripped、无组名
        legacy_stripped = tv_draft(provider="123fastlink", extra={"routingMeta": {"skipReleaseGroup": "1"}})
        legacy_facts = extract_routing_facts(legacy_stripped)
        self.assertEqual(legacy_facts["releaseGroupState"], "stripped")
        self.assertEqual(legacy_facts["releaseGroups"], [])
        # 非去组、客户端从文件名识别到组 → detected
        detected = tv_draft("WiKi", provider="123fastlink")
        self.assertEqual(extract_routing_facts(detected)["releaseGroupState"], "detected")
        # 普通未识别 → none
        self.assertEqual(extract_routing_facts(tv_draft())["releaseGroupState"], "none")

    def test_quality_and_source_facts(self):
        draft = tv_draft()
        draft["metadata"]["quality"] = "2160p"
        draft["metadata"]["source"] = "WEB-DL/BluRay"
        facts = extract_routing_facts(draft)
        self.assertIn("4k", facts["quality"])
        self.assertIn("WEB-DL", facts["source"])
        self.assertIn("BluRay", facts["source"])

    def test_submitter_collaborator(self):
        draft = tv_draft(extra={"submitter": {"userId": 999, "firstName": "协作者"}})
        self.assertEqual(extract_routing_facts(draft, owner_user_id=123456)["submitter"], "collaborator")
        self.assertEqual(extract_routing_facts(draft, owner_user_id=999)["submitter"], "self")


class RuleMatchingTests(unittest.TestCase):
    def test_and_between_keys_or_within_values(self):
        facts = extract_routing_facts(tv_draft("HiveWeb", provider="123fastlink"))
        rule = {"when": {"linkType": ["fastlink"], "mediaType": ["movie"]}, "channelId": "anime"}
        self.assertFalse(rule_matches(rule, facts, {}))
        rule["when"]["mediaType"] = ["tv"]
        self.assertTrue(rule_matches(rule, facts, {}))
        # 值内 OR
        rule["when"] = {"linkType": ["share", "fastlink"]}
        self.assertTrue(rule_matches(rule, facts, {}))

    def test_whitelist_semantics(self):
        routing = {"publicReleaseGroups": ["HiveWeb", "Mo Cuishle"]}
        facts = extract_routing_facts(tv_draft("HiveWeb"))
        self.assertFalse(rule_matches({"when": {"releaseGroupWhitelist": ["notIn"]}, "channelId": "x"}, facts, routing))
        self.assertTrue(rule_matches({"when": {"releaseGroupWhitelist": ["in"]}, "channelId": "x"}, facts, routing))
        outside = extract_routing_facts(tv_draft("WiKi"))
        self.assertTrue(rule_matches({"when": {"releaseGroupWhitelist": ["notIn"]}, "channelId": "x"}, outside, routing))
        # stripped/none 没有组名，∉/∈ 白名单都不命中
        stripped = extract_routing_facts(tv_draft(provider="123fastlink", extra={"routingMeta": {"skipReleaseGroup": "1"}}))
        self.assertFalse(rule_matches({"when": {"releaseGroupWhitelist": ["notIn"]}, "channelId": "x"}, stripped, routing))

    def test_custom_group_list_and_tmdb(self):
        facts = extract_routing_facts(tv_draft("HiveWeb"))
        self.assertTrue(rule_matches({"when": {"releaseGroups": ["HiveWeb", "WiKi"]}, "channelId": "x"}, facts, {}))
        self.assertFalse(rule_matches({"when": {"releaseGroups": ["WiKi"]}, "channelId": "x"}, facts, {}))
        self.assertTrue(rule_matches({"when": {"tmdbId": [301345]}, "channelId": "x"}, facts, {}))
        self.assertFalse(rule_matches({"when": {"tmdbId": [1]}, "channelId": "x"}, facts, {}))
        self.assertTrue(rule_matches({"when": {"tmdbId": "required"}, "channelId": "x"}, facts, {}))
        no_tmdb = extract_routing_facts(tv_draft())
        no_tmdb["tmdbId"] = 0
        self.assertFalse(rule_matches({"when": {"tmdbId": "required"}, "channelId": "x"}, no_tmdb, {}))


class DecideChannelTests(unittest.TestCase):
    def routing(self, rules, **extra):
        return {"version": 2, "rules": rules, "fallbackChannelId": "", "publicReleaseGroups": ["HiveWeb"], **extra}

    def test_first_hit_wins(self):
        rules = [
            {"id": "a", "name": "秒传先", "when": {"linkType": ["fastlink"]}, "channelId": "anime"},
            {"id": "b", "name": "秒传后", "when": {"linkType": ["fastlink"]}, "channelId": "completed"},
        ]
        channel, decision = decide_submission_channel(CHANNELS, self.routing(rules), tv_draft(provider="123fastlink"))
        self.assertEqual(channel["id"], "anime")
        self.assertEqual(decision["ruleId"], "a")
        self.assertEqual(decision["mode"], "自动")

    def test_disabled_rule_and_missing_channel_skipped(self):
        rules = [
            {"id": "a", "when": {"linkType": ["share"]}, "channelId": "anime", "enabled": False},
            {"id": "b", "when": {"linkType": ["share"]}, "channelId": "off"},  # 停用频道不参与
            {"id": "c", "when": {"linkType": ["share"]}, "channelId": "completed"},
        ]
        channel, decision = decide_submission_channel(CHANNELS, self.routing(rules), tv_draft())
        self.assertEqual(channel["id"], "completed")
        self.assertEqual(decision["ruleId"], "c")

    def test_manual_selection_wins_over_rules(self):
        rules = [{"id": "a", "when": {"linkType": ["fastlink"]}, "channelId": "anime"}]
        draft = tv_draft(provider="123fastlink", extra={"channelId": "updating"})
        channel, decision = decide_submission_channel(CHANNELS, self.routing(rules), draft)
        self.assertEqual(channel["id"], "updating")
        self.assertEqual(decision["mode"], "手动")

    def test_single_enabled_channel_short_circuit(self):
        rules = [{"id": "a", "when": {}, "channelId": "completed"}]
        single = [dict(CHANNELS[1]), {**CHANNELS[2], "enabled": False}]
        channel, decision = decide_submission_channel(single, self.routing(rules), tv_draft(provider="123fastlink"))
        self.assertEqual(channel["id"], "completed")
        self.assertEqual(decision["summary"], "仅启用一个频道")

    def test_fallback_chain(self):
        rules = [{"id": "a", "when": {"linkType": ["fastlink"]}, "channelId": "anime"}]
        # fallbackChannelId 命中
        channel, decision = decide_submission_channel(CHANNELS, self.routing(rules, fallbackChannelId="updating"), tv_draft())
        self.assertEqual(channel["id"], "updating")
        self.assertEqual(decision["summary"], "兜底频道")
        # 无兜底 → role 兜底（tv 未完结 → public_updating）
        channel, decision = decide_submission_channel(CHANNELS, self.routing(rules), tv_draft())
        self.assertEqual(channel["id"], "updating")
        self.assertEqual(decision["summary"], "频道类型兜底")
        # detected 发布组 → private 档
        channel, _ = decide_submission_channel(CHANNELS, self.routing(rules), tv_draft("WiKi"))
        self.assertEqual(channel["id"], "private")

    def test_no_rules_no_channel_returns_none(self):
        channel, decision = decide_submission_channel([], {}, tv_draft())
        self.assertIsNone(channel)


class LegacyMigrationTests(unittest.TestCase):
    LEGACY_ROUTING = {
        "publicReleaseGroups": ["HiveWeb"],
        "releaseGroupChannelId": "private",
        "noReleaseGroupCompletedChannelId": "completed",
        "noReleaseGroupUpdatingChannelId": "updating",
    }

    def assert_legacy_equivalent(self, config_draft_pairs):
        for draft in config_draft_pairs:
            legacy = select_submission_channel({"channels": CHANNELS, "routing": self.LEGACY_ROUTING}, draft)
            channel, _decision = decide_submission_channel(CHANNELS, self.LEGACY_ROUTING, draft)
            self.assertEqual((legacy or {}).get("id"), (channel or {}).get("id"), f"迁移结果不一致: {draft.get('metadata')}")

    def test_migration_equivalence(self):
        self.assert_legacy_equivalent([
            tv_draft("HiveWeb", 7, 12),        # 白名单内 + 连载 → updating
            tv_draft("HiveWeb", 12, 12),       # 白名单内 + 完结 → completed
            tv_draft("WiKi", 7, 12),           # 非白名单 → private
            tv_draft("", 12, 12),              # 无组 + 完结 → completed
            tv_draft("", 7, 12),               # 无组 + 连载 → updating
            tv_draft("", media_type="movie"),  # 电影 → completed
        ])

    def test_migration_shape(self):
        normalized = normalize_routing(self.LEGACY_ROUTING)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual([rule["id"] for rule in normalized["rules"]], ["migrated-release-group", "migrated-completed", "migrated-updating", "migrated-fallback"])
        # 影子字段保留，老消费者读三槽不受影响
        self.assertEqual(normalized["releaseGroupChannelId"], "private")
        self.assertEqual(normalized["publicReleaseGroups"], ["HiveWeb"])

    def test_v1_fallback_only_config(self):
        legacy = {"releaseGroupChannelId": "private", "fallbackChannelId": "anime"}
        for draft in (tv_draft("WiKi"), tv_draft(""), tv_draft("HiveWeb")):
            old = select_submission_channel({"channels": CHANNELS, "routing": legacy}, draft)
            new, _ = decide_submission_channel(CHANNELS, legacy, draft)
            self.assertEqual((old or {}).get("id"), (new or {}).get("id"))

    def test_v2_config_passthrough(self):
        v2 = normalize_routing({"version": 2, "rules": [{"name": "x", "when": {"linkType": "fastlink"}, "channelId": "anime"}], "fallbackChannelId": "completed"})
        self.assertEqual(v2["rules"][0]["when"], {"linkType": ["fastlink"]})
        channel, _ = decide_submission_channel(CHANNELS, v2, tv_draft(provider="123fastlink"))
        self.assertEqual(channel["id"], "anime")


class DecisionExplanationTests(unittest.TestCase):
    def test_route_label_contains_rule_name(self):
        config = {
            "channels": CHANNELS,
            "routing": {"version": 2, "rules": [{"id": "r1", "name": "秒传帖", "when": {"linkType": ["fastlink"]}, "channelId": "anime"}], "fallbackChannelId": "updating", "publicReleaseGroups": []},
        }
        label = route_channel_label(tv_draft(provider="123fastlink"), config)
        self.assertEqual(label, "动漫（自动 · 命中「秒传帖」）")

    def test_rule_summary_fallback_name(self):
        rule = {"name": "", "when": {"linkType": ["fastlink"], "completion": ["completed"]}, "channelId": "anime"}
        self.assertEqual(rule_summary(rule), "链接类型=秒传链接；完结状态=完结")

    def test_migrated_release_group_rule_summary_and_match(self):
        """白名单条件经 normalize_rule 后是列表 ["notIn"]，摘要与命中都要按列表处理（回归：列表形态被当字符串比较会丢摘要、迁移规则失效）。"""
        normalized = normalize_routing({
            "publicReleaseGroups": ["HiveWeb"],
            "releaseGroupChannelId": "private",
            "noReleaseGroupCompletedChannelId": "completed",
            "noReleaseGroupUpdatingChannelId": "updating",
        })
        rg_rule = next(rule for rule in normalized["rules"] if rule["id"] == "migrated-release-group")
        self.assertEqual(rg_rule["when"]["releaseGroupWhitelist"], ["notIn"], "迁移产物也过 normalize_rule → 列表形态")
        self.assertIn("发布组∉白名单", rule_summary(rg_rule))
        outside = extract_routing_facts(tv_draft("WiKi"))
        inside = extract_routing_facts(tv_draft("HiveWeb"))
        self.assertTrue(rule_matches(rg_rule, outside, normalized), "非白名单组命中迁移规则")
        self.assertFalse(rule_matches(rg_rule, inside, normalized), "白名单组不命中「∉白名单」规则")


class RoutingSchemaTests(unittest.TestCase):
    def test_schema_serializable_and_keys(self):
        import json

        schema = routing_schema()
        json.dumps(schema, ensure_ascii=False)
        keys = [field["key"] for field in schema["fields"]]
        for wanted in ("linkType", "mediaType", "completion", "releaseGroupState", "releaseGroupWhitelist", "quality", "submitter", "tmdbId"):
            self.assertIn(wanted, keys)


class RoutingSimulateRouteTests(unittest.TestCase):
    """规则模拟器接口：秒传链接本地解析出事实（不联网），分享链接拉目录失败只回 error。"""

    def test_simulate_fastlink_facts_and_decision(self):
        import asyncio
        import importlib
        import os
        import tempfile

        data_dir = tempfile.TemporaryDirectory()
        previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = data_dir.name
        # 整个用例用一条自带的事件循环：先建好并 set 到当前线程，再 import app.main
        # （app.main 模块加载期会取当前 loop），避免与前面用例的 loop 关闭顺序互相干扰
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            main = importlib.import_module("app.main")
            main.store.write_submission_config({"allowedUserIds": [456]})
            main.store.write_user_channel_config(
                456,
                {
                    "channels": [
                        {"id": "main", "title": "主频道", "chatId": "-1009", "enabled": True, "isDefault": True, "role": ""},
                        {"id": "other", "title": "备用频道", "chatId": "-1010", "enabled": True, "role": ""},
                    ],
                    "routing": {"version": 2, "rules": [{"name": "秒传帖", "when": {"linkType": ["fastlink"]}, "channelId": "main"}], "fallbackChannelId": "", "publicReleaseGroups": []},
                },
            )
            payload = main.ChannelRoutingSimulateRequest(
                text="123FLCPV2$%f#1024#Show.S01E01.2160p.WEB-DL-HiveWeb.mkv\n📄：Show.S01E01.2160p.WEB-DL-HiveWeb.mkv\n📄：Show.S01E02.2160p.WEB-DL-HiveWeb.mkv"
            )
            result = loop.run_until_complete(main.simulate_channel_routing(456, payload))
            self.assertTrue(result["ok"])
            item = result["results"][0]
            self.assertEqual(item["provider"], "123fastlink")
            self.assertEqual(item["facts"]["linkType"], "fastlink")
            self.assertEqual(item["channel"]["id"], "main")
            self.assertEqual(item["decision"]["ruleName"], "秒传帖")
            # 非频道主账号 → 门卫拒绝（模拟器与配置读写同一权限面）
            with self.assertRaises(main.HTTPException):
                loop.run_until_complete(main.simulate_channel_routing(777, payload))
            # 没有链接 → 400
            with self.assertRaises(main.HTTPException):
                loop.run_until_complete(main.simulate_channel_routing(456, main.ChannelRoutingSimulateRequest(text="没有链接")))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
            if previous is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = previous
            data_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
