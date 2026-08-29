from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


VERSION_FIELD_ORDER = (
    "videoFormat",
    "highQuality",
    "dolbyVision",
    "mediaSource",
    "resourceType",
    "effect",
    "dynamicRange",
    "frameRate",
    "colorDepth",
    "originalEdition",
    "videoCodec",
    "audioCodec",
    "releaseGroup",
)

VERSION_ALIAS_FIELDS = {
    "videoFormat": "quality",
    "mediaSource": "webSource",
    "resourceType": "source",
    "effect": "effect",
    "dynamicRange": "effect",
    "originalEdition": "edition",
    "videoCodec": "videoCodec",
    "audioCodec": "audioCodec",
}


def version_rule_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    nested = source.get("ruleConfig")
    return nested if isinstance(nested, dict) else source


def version_alias_key(value: Any) -> str:
    return re.sub(r"[\s._+\-]+", "", str(value or "")).upper()


def version_alias_rules(config: Optional[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    rules = version_rule_config(config).get(key)
    if not isinstance(rules, list):
        return []
    return sorted(
        [rule for rule in rules if isinstance(rule, dict) and rule.get("enabled") is not False and str(rule.get("value") or "").strip()],
        key=lambda rule: int(rule.get("order") or 0),
        reverse=True,
    )


def canonical_version_alias(value: Any, config: Optional[Dict[str, Any]], key: str) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    wanted = version_alias_key(clean)
    if not wanted:
        return ""
    for rule in version_alias_rules(config, key):
        candidates = [rule.get("value"), *(rule.get("aliases") if isinstance(rule.get("aliases"), list) else [])]
        if any(version_alias_key(candidate) == wanted for candidate in candidates):
            return str(rule.get("value") or clean).strip()
    return clean


def extract_version_alias(text: Any, config: Optional[Dict[str, Any]], key: str) -> str:
    source = str(text or "")
    matches: List[tuple[int, int, str]] = []
    for rule in version_alias_rules(config, key):
        candidates = [rule.get("value"), *(rule.get("aliases") if isinstance(rule.get("aliases"), list) else [])]
        for candidate in candidates:
            clean = str(candidate or "").strip()
            parts = [re.escape(part) for part in re.split(r"[\s._-]+", clean) if part]
            if not parts:
                continue
            pattern = r"[\s._-]*".join(parts)
            match = re.search(rf"(?:^|[\s._\-\[\]()])({pattern})(?=$|[\s._\-\[\]()])", source, re.I)
            if match:
                matches.append((match.start(1), -len(match.group(1)), str(rule.get("value") or clean).strip()))
    return min(matches)[2] if matches else ""


def normalize_version_fields(fields: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for field in VERSION_FIELD_ORDER:
        clean = re.sub(r"\s+", " ", str(fields.get(field) or "")).strip()
        alias_field = VERSION_ALIAS_FIELDS.get(field)
        if clean and alias_field:
            clean = canonical_version_alias(clean, config, alias_field)
        normalized[field] = clean

    normalized["videoFormat"] = normalize_version_quality(normalized["videoFormat"])
    normalized["frameRate"] = normalize_version_fps(normalized["frameRate"])
    normalized["colorDepth"] = normalize_version_bit_depth(normalized["colorDepth"])
    normalized["audioCodec"] = normalize_version_audio_codec(normalized["audioCodec"], config)
    normalized["highQuality"] = "HQ" if normalized["highQuality"] else ""
    normalized["dolbyVision"] = "DV" if normalized["dolbyVision"] else ""

    effects = normalize_version_effects(normalized["effect"], config)
    dynamic_range = normalize_version_effects(normalized["dynamicRange"], config)
    represented = {
        version_alias_key(value)
        for value in (normalized["highQuality"], normalized["dolbyVision"], dynamic_range)
        if value
    }
    effects = [value for value in effects if not version_effect_represented(value, represented)]
    normalized["effect"] = ".".join(effects)
    normalized["dynamicRange"] = dynamic_range[0] if dynamic_range else ""
    return normalized


def normalize_version_quality(value: str) -> str:
    parts = [part for part in re.split(r"[&/|,，、\s]+", value) if part]
    values: List[str] = []
    seen = set()
    for part in parts:
        clean = re.sub(r"^(\d{3,4})([PI])$", lambda match: f"{match.group(1)}{match.group(2).lower()}", part, flags=re.I)
        key = version_alias_key(clean)
        if clean and key not in seen:
            seen.add(key)
            values.append(clean)
    return "&".join(values)


def normalize_version_fps(value: str) -> str:
    match = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)\s*(?:FPS|帧)?", str(value or "").strip(), re.I)
    return f"{match.group(1)}fps" if match else str(value or "").strip()


def normalize_version_bit_depth(value: str) -> str:
    match = re.fullmatch(r"(8|10|12)\s*BIT", str(value or "").strip(), re.I)
    return f"{match.group(1)}bit" if match else str(value or "").strip()


def normalize_version_effects(value: str, config: Optional[Dict[str, Any]]) -> List[str]:
    source = str(value or "")
    matches: List[tuple[int, int, str]] = []
    for rule in version_alias_rules(config, "effect"):
        candidates = [rule.get("value"), *(rule.get("aliases") if isinstance(rule.get("aliases"), list) else [])]
        for candidate in candidates:
            clean = str(candidate or "").strip()
            parts = [re.escape(part) for part in re.split(r"[\s._-]+", clean) if part]
            if not parts:
                continue
            pattern = r"[\s._-]*".join(parts)
            for match in re.finditer(rf"(?:^|[\s._\-/,])({pattern})(?=$|[\s._\-/,])", source, re.I):
                matches.append((match.start(1), -len(match.group(1)), str(rule.get("value") or clean).strip()))
    if matches:
        values: List[str] = []
        seen = set()
        for _index, _length, canonical in sorted(matches):
            key = version_alias_key(canonical)
            if key == "HDR" and seen & {"HDR10", "HDRVIVID"}:
                continue
            if key not in seen:
                seen.add(key)
                values.append(canonical)
        return values

    values: List[str] = []
    seen = set()
    for part in re.split(r"[/, ]+", str(value or "")):
        clean = part.strip()
        if not clean:
            continue
        canonical = canonical_version_alias(clean, config, "effect")
        key = version_alias_key(canonical)
        if key == "HDR" and any(item in seen for item in {"HDR10", "HDRVIVID"}):
            continue
        if canonical and key not in seen:
            seen.add(key)
            values.append(canonical)
    return values


def normalize_version_audio_codec(value: str, config: Optional[Dict[str, Any]]) -> str:
    clean = str(value or "").strip()
    for rule in version_alias_rules(config, "audioCodec"):
        candidates = [rule.get("value"), *(rule.get("aliases") if isinstance(rule.get("aliases"), list) else [])]
        for candidate in candidates:
            alias = str(candidate or "").strip()
            parts = [re.escape(part) for part in re.split(r"[\s._+\-]+", alias) if part]
            if not parts:
                continue
            pattern = r"[\s._+\-]*".join(parts)
            match = re.match(rf"^{pattern}(?=$|[\s._+\-]*\d|[\s._+\-]*(?:Atmos|JOC))", clean, re.I)
            if match:
                suffix = clean[match.end() :].lstrip(" ._-")
                canonical = str(rule.get("value") or alias).strip()
                return f"{canonical}{suffix}" if suffix and suffix[0].isdigit() else (f"{canonical} {suffix}" if suffix else canonical)
    return clean


def version_effect_represented(value: str, represented: set[str]) -> bool:
    key = version_alias_key(value)
    if key in represented:
        return True
    if key == "HDR" and represented & {"HDR10", "HDRVIVID"}:
        return True
    return False


def version_semantic_key(fields: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> str:
    normalized = normalize_version_fields(fields, config)
    return "|".join(version_alias_key(normalized[field]) for field in VERSION_FIELD_ORDER)


def version_display_parts(fields: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> List[str]:
    normalized = normalize_version_fields(fields, config)
    values: List[str] = []
    seen = set()
    for field in VERSION_FIELD_ORDER:
        clean = normalized[field]
        key = version_alias_key(clean)
        if clean and key not in seen:
            seen.add(key)
            values.append(clean)
    return values


def format_episode_ranges(episodes: Iterable[int]) -> str:
    values = sorted({int(episode) for episode in episodes if int(episode) > 0})
    ranges: List[str] = []
    start = previous = 0
    for episode in values:
        if not start:
            start = previous = episode
            continue
        if episode == previous + 1:
            previous = episode
            continue
        ranges.append(format_episode_range(start, previous))
        start = previous = episode
    if start:
        ranges.append(format_episode_range(start, previous))
    return "、".join(ranges)


def format_episode_range(first: int, last: int) -> str:
    return f"E{first:02d}" if first == last else f"E{first:02d}-E{last:02d}"
