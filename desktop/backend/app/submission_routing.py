"""投稿自动路由引擎（v2）：有序规则列表 + 兜底频道，首条命中即停。

设计文档见 desktop/PLAN-submission-routing-v2.md。本模块只依赖 dict/list 纯数据
（草稿、频道列表、路由配置），事实提取需要的少量识别工具从 submission 函数内延迟导入
（submission 顶部导入本模块，避免模块级循环）。

路由配置形状（routing）：
- v1（旧）：{publicReleaseGroups, releaseGroupChannelId, noReleaseGroupCompletedChannelId,
  noReleaseGroupUpdatingChannelId, fallbackChannelId}
- v2：{version: 2, rules: [{id, name, enabled, when, channelId}], fallbackChannelId,
  publicReleaseGroups}（v1 三槽键保留为影子字段，只给外部老消费者看，引擎不读）

规则 when 条件：各键之间 AND，键内取值 OR；缺键 = 不参与。发布组白名单
（publicReleaseGroups）是「发布组 ∉/∈ 白名单」条件的数据源，随路由一起存。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

ROUTING_VERSION = 2
MAX_RULES = 40

LINK_TYPE_LABELS = {"share": "分享链接", "fastlink": "秒传链接"}
MEDIA_TYPE_LABELS = {"movie": "电影", "tv": "剧集", "unknown": "未识别"}
COMPLETION_LABELS = {"completed": "完结", "updating": "连载/未完结", "unknown": "完结状态未知"}
GROUP_STATE_LABELS = {"detected": "识别到发布组", "stripped": "导出时已去除", "none": "没有发布组"}
SUBMITTER_LABELS = {"self": "频道主本人", "collaborator": "协作者投稿"}
QUALITY_LABELS = {"8k": "8K", "4k": "4K/2160p", "1080p": "1080p", "720p": "720p", "sd": "SD/480p 及以下"}
SOURCE_MATCH_VALUES = ["UHD BluRay Remux", "BluRay Remux", "WEB-DL", "WEBRip", "BluRay", "Remux", "SD"]

RULE_FIELD_LABELS = {
    "linkType": "链接类型",
    "mediaType": "媒体类型",
    "completion": "完结状态",
    "releaseGroupState": "发布组状态",
    "releaseGroupWhitelist": "发布组与白名单",
    "releaseGroups": "发布组（自定义名单）",
    "quality": "画质档位",
    "source": "资源来源",
    "submitter": "投稿人",
    "tmdbId": "TMDB ID",
    "always": "无条件（总是命中）",
}


def safe_routing_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def normalize_release_group_variants(value: Any) -> List[str]:
    """发布组串（可能是 "A/B" 多版本变体）→ 去重小写名单。"""
    out: List[str] = []
    for item in str(value or "").split("/"):
        clean = re.sub(r"\s+", " ", item).strip()
        key = clean.lower()
        if clean and key not in out:
            out.append(key)
    return out


# ===== 事实提取 =====


def extract_routing_facts(draft: Dict[str, Any], owner_user_id: int = 0) -> Dict[str, Any]:
    from .submission import (
        compact_source,
        is_completed_media,
        normalize_submission_submitter,
        safe_int,
    )

    draft = draft if isinstance(draft, dict) else {}
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    routing_meta = draft.get("routingMeta") if isinstance(draft.get("routingMeta"), dict) else {}

    link_type = "fastlink" if str(share.get("provider") or "") == "123fastlink" else "share"
    media_type_value = str(media.get("mediaType") or metadata.get("mediaType") or "")
    media_type = media_type_value if media_type_value in {"movie", "tv"} else "unknown"
    if media_type == "unknown":
        completion = "unknown"
    else:
        completion = "completed" if is_completed_media(draft) else "updating"

    group_names = normalize_release_group_variants(routing_meta.get("releaseGroup")) or normalize_release_group_variants(metadata.get("releaseGroup"))
    # 「去组导出」的秒传（脚本带 releaseGroupStripped/skipReleaseGroup）优先判 stripped：
    # 这是频道主自己重新发布、已把组名洗掉的帖子，路由意图是「去组=公开」，不该再按原始组名
    # 落回 detected 走私有档。原始组名仍留在 releaseGroups 事实里，供「stripped + 组∉白名单」这类
    # 更细的规则可选用；但状态本身以 stripped 为准。
    is_stripped = bool(str(routing_meta.get("releaseGroupStripped") or "").strip() or routing_meta.get("skipReleaseGroup"))
    if is_stripped:
        group_state = "stripped"
    elif group_names:
        group_state = "detected"
    else:
        group_state = "none"

    quality_source = "/".join([str(metadata.get("quality") or ""), str(metadata.get("resolution") or ""), str(metadata.get("resourceType") or "")])
    upper = quality_source.upper()
    quality: List[str] = []
    if "8K" in quality_source or "4320" in upper:
        quality.append("8k")
    if "2160" in upper or "4K" in quality_source or "UHD" in upper:
        quality.append("4k")
    if "1080" in quality_source:
        quality.append("1080p")
    if "720" in quality_source:
        quality.append("720p")
    if any(token in upper for token in ("480P", "576P", "SDTV")) or ("SD" in quality_source and not quality):
        quality.append("sd")

    source_variants: List[str] = []
    for raw_source in [str(metadata.get("source") or ""), str(metadata.get("resourceType") or "")]:
        for item in raw_source.split("/"):
            if not str(item or "").strip():
                continue
            variant = (compact_source(item) or str(item)).strip()
            if variant == "WEB DL":
                variant = "WEB-DL"
            if variant and variant not in source_variants:
                source_variants.append(variant)

    tmdb_id = safe_int(media.get("tmdbId") or metadata.get("tmdbId"))
    submitter = normalize_submission_submitter(draft.get("submitter"))
    submitter_id = safe_int(submitter.get("userId") or submitter.get("id"))
    submitter_role = "self" if submitter_id <= 0 or submitter_id == safe_int(owner_user_id) else "collaborator"

    return {
        "linkType": link_type,
        "mediaType": media_type,
        "completion": completion,
        "releaseGroupState": group_state,
        "releaseGroups": group_names,
        "quality": quality,
        "source": source_variants,
        "submitter": submitter_role,
        "tmdbId": tmdb_id,
    }


# ===== 规则规范化与求值 =====


def _clean_channel_id(value: Any) -> str:
    return str(value or "").strip()


def normalize_rule(value: Any, index: int = 0) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    raw_when = value.get("when") if isinstance(value.get("when"), dict) else {}
    when: Dict[str, Any] = {}
    for key in ("linkType", "mediaType", "completion", "releaseGroupState", "releaseGroupWhitelist", "submitter", "quality", "source"):
        items = raw_when.get(key)
        if isinstance(items, str):
            items = [items]
        if isinstance(items, list):
            cleaned = [str(item or "").strip() for item in items if str(item or "").strip()]
            if cleaned:
                when[key] = cleaned
    groups = raw_when.get("releaseGroups")
    if isinstance(groups, str):
        groups = [groups]
    if isinstance(groups, list):
        names = [str(item or "").strip() for item in groups if str(item or "").strip()]
        if names:
            when["releaseGroups"] = names
    tmdb_values: List[int] = []
    if isinstance(raw_when.get("tmdbId"), list):
        for item in raw_when.get("tmdbId") or []:
            parsed = safe_routing_int(str(item).strip().lstrip("#")) if not isinstance(item, int) else item
            if parsed > 0 and parsed not in tmdb_values:
                tmdb_values.append(parsed)
    elif isinstance(raw_when.get("tmdbId"), bool):
        if raw_when.get("tmdbId"):
            when["tmdbId"] = "required"
    elif safe_routing_int(raw_when.get("tmdbId")) > 0:
        tmdb_values.append(safe_routing_int(raw_when.get("tmdbId")))
    if tmdb_values:
        when["tmdbId"] = tmdb_values
    rule: Dict[str, Any] = {
        "id": str(value.get("id") or f"rule_{index + 1}").strip() or f"rule_{index + 1}",
        "name": str(value.get("name") or "").strip()[:60],
        "enabled": value.get("enabled") is not False,
        "when": when,
        "channelId": _clean_channel_id(value.get("channelId")),
    }
    return rule if rule["channelId"] else None


def rule_matches(rule: Dict[str, Any], facts: Dict[str, Any], routing: Dict[str, Any]) -> bool:
    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}

    def any_of(key: str) -> bool:
        wanted = when.get(key)
        if not isinstance(wanted, list) or not wanted:
            return True
        fact = facts.get(key)
        if isinstance(fact, list):
            return bool(set(str(item) for item in fact) & {str(item) for item in wanted})
        return str(fact or "") in {str(item) for item in wanted}

    for key in ("linkType", "mediaType", "completion", "releaseGroupState", "submitter", "quality", "source"):
        if not any_of(key):
            return False

    raw_whitelist = routing.get("publicReleaseGroups") if isinstance(routing.get("publicReleaseGroups"), list) else []
    whitelist: List[str] = []
    for item in raw_whitelist:
        # 白名单条目可能本身带空格（"Mo Cuishle"），不归一化斜杠/点，只压空白 + 小写
        clean = re.sub(r"\s+", " ", str(item or "")).strip()
        if clean and clean.lower() not in whitelist:
            whitelist.append(clean.lower())
    groups = [str(item) for item in facts.get("releaseGroups") or []]
    whitelist_modes = when.get("releaseGroupWhitelist")
    if isinstance(whitelist_modes, list) and whitelist_modes:
        if "notIn" in whitelist_modes and not any(group not in whitelist for group in groups):
            return False
        if "in" in whitelist_modes and not (groups and all(group in whitelist for group in groups)):
            return False
    raw_custom = when.get("releaseGroups")
    if isinstance(raw_custom, list) and raw_custom:
        custom_groups: List[str] = []
        for item in raw_custom:
            clean = re.sub(r"\s+", " ", str(item or "")).strip()
            if clean and clean.lower() not in custom_groups:
                custom_groups.append(clean.lower())
        if not groups or not (set(groups) & set(custom_groups)):
            return False
    tmdb_condition = when.get("tmdbId")
    if isinstance(tmdb_condition, list) and tmdb_condition:
        wanted_ids = {safe_routing_int(item) for item in tmdb_condition}
        if safe_routing_int(facts.get("tmdbId")) not in wanted_ids:
            return False
    elif tmdb_condition == "required" or tmdb_condition is True:
        if safe_routing_int(facts.get("tmdbId")) <= 0:
            return False
    return True


def rule_summary(rule: Dict[str, Any]) -> str:
    """给预览/管理端展示的一行式规则描述（纯数据拼接，不依赖 UI 词表也行）。"""
    from .submission import enabled_submission_channels

    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    parts: List[str] = []
    for key in ("linkType", "mediaType", "completion", "releaseGroupState", "submitter", "quality"):
        values = when.get(key)
        if isinstance(values, list) and values:
            labels = RULE_FIELD_LABELS.get(key, key)
            table = {
                "linkType": LINK_TYPE_LABELS,
                "mediaType": MEDIA_TYPE_LABELS,
                "completion": COMPLETION_LABELS,
                "releaseGroupState": GROUP_STATE_LABELS,
                "submitter": SUBMITTER_LABELS,
                "quality": QUALITY_LABELS,
            }.get(key, {})
            value_text = "/".join(str(table.get(item) or item) for item in values)
            parts.append(f"{labels}={value_text}")
    whitelist_mode = when.get("releaseGroupWhitelist")
    modes = whitelist_mode if isinstance(whitelist_mode, list) else ([whitelist_mode] if whitelist_mode else [])
    if "notIn" in modes:
        parts.append("发布组∉白名单")
    elif "in" in modes:
        parts.append("发布组∈白名单")
    if isinstance(when.get("releaseGroups"), list) and when.get("releaseGroups"):
        parts.append("发布组=" + "/".join(str(item) for item in when["releaseGroups"]))
    if isinstance(when.get("source"), list) and when.get("source"):
        parts.append("来源=" + "/".join(str(item) for item in when["source"]))
    tmdb_condition = when.get("tmdbId")
    if isinstance(tmdb_condition, list) and tmdb_condition:
        parts.append("TMDB=" + "/".join(str(item) for item in tmdb_condition))
    elif tmdb_condition in ("required", True):
        parts.append("已匹配TMDB")
    return "；".join(parts) or "无条件"


# ===== 配置规范化与 v1 迁移 =====


def normalize_routing(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    public_groups: List[str] = []
    for item in raw.get("publicReleaseGroups") or []:
        clean = re.sub(r"\s+", " ", str(item or "")).strip()
        if clean and clean not in public_groups:
            public_groups.append(clean)
    if isinstance(raw.get("rules"), list) and any(isinstance(item, dict) and (item.get("channelId") or item.get("when")) for item in raw.get("rules") or []):
        rules = [normalized for normalized in (normalize_rule(item, index) for index, item in enumerate(raw.get("rules") or [])) if normalized]
        return {
            "version": ROUTING_VERSION,
            "rules": rules[:MAX_RULES],
            "fallbackChannelId": _clean_channel_id(raw.get("fallbackChannelId")),
            "publicReleaseGroups": public_groups,
        }
    return migrate_v1_routing(raw, public_groups)


def migrate_v1_routing(raw: Dict[str, Any], public_groups: Optional[List[str]] = None) -> Dict[str, Any]:
    """旧三槽路由 → 等价 v2 规则（顺序 = 旧决策顺序：非白名单发布组 > 完结 > 连载 > 兜底）。

    完结规则额外带 completion≠unknown：旧逻辑对未识别类型也按 is_completed_media=False
    落「连载」，v2 规则引擎对没有 TMDB 的草稿按 unknown 处理、交给后续兜底链——
    迁移时加这个条件既保住"未识别不冒充完结"，也不改变已识别内容的投递结果。
    """
    groups = public_groups if public_groups is not None else normalize_routing(raw)["publicReleaseGroups"]
    completed_id = _clean_channel_id(raw.get("noReleaseGroupCompletedChannelId"))
    updating_id = _clean_channel_id(raw.get("noReleaseGroupUpdatingChannelId"))
    release_group_id = _clean_channel_id(raw.get("releaseGroupChannelId"))
    rules: List[Dict[str, Any]] = []
    if release_group_id and groups:
        rules.append({
            "id": "migrated-release-group",
            "name": "发布组不在白名单",
            "enabled": True,
            "when": {"releaseGroupState": ["detected"], "releaseGroupWhitelist": "notIn"},
            "channelId": release_group_id,
        })
    if completed_id:
        rules.append({
            "id": "migrated-completed",
            "name": "完结内容",
            "enabled": True,
            "when": {"completion": ["completed"]},
            "channelId": completed_id,
        })
    if updating_id:
        rules.append({
            "id": "migrated-updating",
            "name": "连载内容",
            "enabled": True,
            "when": {"completion": ["updating"]},
            "channelId": updating_id,
        })
    fallback = _clean_channel_id(raw.get("fallbackChannelId")) or release_group_id
    if fallback:
        rules.append({
            "id": "migrated-fallback",
            "name": "默认投递",
            "enabled": True,
            "when": {},
            "channelId": fallback,
        })
    return {
        "version": ROUTING_VERSION,
        # 迁移产物同样过 normalize_rule，与用户提交的 v2 规则走同一规范化口径
        "rules": [normalized for normalized in (normalize_rule(rule, index) for index, rule in enumerate(rules)) if normalized],
        "fallbackChannelId": _clean_channel_id(raw.get("fallbackChannelId")),
        "publicReleaseGroups": groups,
        # 影子字段：老外部消费者（如 Telegram 旧设置页）读这三键仍然在
        "releaseGroupChannelId": release_group_id,
        "noReleaseGroupCompletedChannelId": completed_id,
        "noReleaseGroupUpdatingChannelId": updating_id,
    }


# ===== 决策主入口 =====


def decide_submission_channel(
    channels: List[Dict[str, Any]],
    routing: Dict[str, Any],
    draft: Dict[str, Any],
    owner_user_id: int = 0,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """返回 (频道, 决策说明)。决策说明含 mode/ruleId/ruleName/summary/facts。"""
    from .submission import enabled_submission_channels, find_channel, find_channel_by_role, find_default_channel, safe_int

    enabled_channels = enabled_submission_channels(channels or [])
    facts = extract_routing_facts(draft, owner_user_id)
    decision: Dict[str, Any] = {"facts": facts}

    # 手动选择对全部频道查（保持旧语义：先于启用过滤）
    manual_id = str((draft or {}).get("channelId") or "").strip()
    if manual_id:
        manual = find_channel(channels or [], manual_id)
        if manual:
            return manual, {**decision, "mode": "手动", "ruleName": "手动选择", "summary": "手动选择频道"}

    if len(enabled_channels) == 1:
        return enabled_channels[0], {**decision, "mode": "自动", "ruleName": "", "summary": "仅启用一个频道"}

    normalized = normalize_routing(routing)
    known_ids = {str(channel.get("id") or "") for channel in enabled_channels}
    for rule in normalized.get("rules") or []:
        if rule.get("enabled") is False:
            continue
        if rule["channelId"] not in known_ids:
            continue
        if rule_matches(rule, facts, normalized):
            channel = find_channel(enabled_channels, rule["channelId"])
            if channel:
                return channel, {
                    **decision,
                    "mode": "自动",
                    "ruleId": rule.get("id") or "",
                    "ruleName": rule.get("name") or rule_summary(rule),
                    "summary": rule_summary(rule),
                }

    fallback_id = normalized.get("fallbackChannelId") or ""
    if fallback_id and fallback_id in known_ids:
        fallback = find_channel(enabled_channels, fallback_id)
        if fallback:
            return fallback, {**decision, "mode": "自动", "ruleName": "", "summary": "兜底频道"}

    role = "private" if facts["releaseGroupState"] == "detected" else ("public_completed" if facts["completion"] == "completed" else "public_updating")
    channel = find_channel_by_role(enabled_channels, role) or find_default_channel(enabled_channels)
    return channel, {**decision, "mode": "自动", "ruleName": "", "summary": "频道类型兜底" if channel else "未匹配"}


def routing_schema() -> Dict[str, Any]:
    """下发给管理端 UI 的字段/取值词表（单一事实源，前端不硬编码）。"""

    def options(mapping: Dict[str, str]) -> List[Dict[str, str]]:
        return [{"value": key, "label": label} for key, label in mapping.items()]

    fields = [
        {"key": "linkType", "label": RULE_FIELD_LABELS["linkType"], "kind": "multi", "values": options(LINK_TYPE_LABELS)},
        {"key": "mediaType", "label": RULE_FIELD_LABELS["mediaType"], "kind": "multi", "values": options(MEDIA_TYPE_LABELS)},
        {"key": "completion", "label": RULE_FIELD_LABELS["completion"], "kind": "multi", "values": options(COMPLETION_LABELS)},
        {"key": "releaseGroupState", "label": RULE_FIELD_LABELS["releaseGroupState"], "kind": "multi", "values": options(GROUP_STATE_LABELS)},
        {
            "key": "releaseGroupWhitelist",
            "label": RULE_FIELD_LABELS["releaseGroupWhitelist"],
            "kind": "single",
            "values": [{"value": "notIn", "label": "不在白名单（走私有档）"}, {"value": "in", "label": "全部在白名单"}],
        },
        {"key": "releaseGroups", "label": RULE_FIELD_LABELS["releaseGroups"], "kind": "freeMulti", "values": []},
        {"key": "quality", "label": RULE_FIELD_LABELS["quality"], "kind": "multi", "values": options(QUALITY_LABELS)},
        {"key": "source", "label": RULE_FIELD_LABELS["source"], "kind": "freeMulti", "values": options({key: key for key in SOURCE_MATCH_VALUES})},
        {"key": "submitter", "label": RULE_FIELD_LABELS["submitter"], "kind": "multi", "values": options(SUBMITTER_LABELS)},
        {"key": "tmdbId", "label": RULE_FIELD_LABELS["tmdbId"], "kind": "idList", "values": []},
    ]
    return {"version": ROUTING_VERSION, "fields": fields, "maxRules": MAX_RULES}
