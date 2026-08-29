"""TMDB / 豆瓣查询与标题匹配工具（自 organize 模块抽取，供投稿系统使用）。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import unicodedata
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d|p)", re.I)


TMDB_RE = re.compile(r"(?:tmdbid|tmdb)[=\-_: ]?(\d{2,10})", re.I)


TMDB_API_BASE = "https://api.themoviedb.org/3"


_TMDB_CACHE_TTL = 86400.0


_TMDB_CACHE_MAX_ENTRIES = 256


_tmdb_details_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


_tmdb_search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


_tmdb_details_inflight: Dict[str, asyncio.Task[Any]] = {}


_tmdb_search_inflight: Dict[str, asyncio.Task[Any]] = {}


def _tmdb_cache_key(tmdb_id: int, media_type: str, language: str) -> str:
    return f"{media_type}:{tmdb_id}:{language}"


def _tmdb_cache_get(key: str) -> Optional[Dict[str, Any]] | None:
    """Return cached value or None. Sentinel: returns False-like tuple element if expired."""
    entry = _tmdb_details_cache.pop(key, None)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _TMDB_CACHE_TTL:
        _tmdb_details_cache.pop(key, None)
        return None
    _tmdb_details_cache[key] = entry
    return copy.deepcopy(value)


def _tmdb_cache_set(key: str, value: Optional[Dict[str, Any]]) -> None:
    _tmdb_details_cache.pop(key, None)
    _tmdb_details_cache[key] = (time.monotonic(), copy.deepcopy(value))
    _tmdb_cache_prune(_tmdb_details_cache)


def _tmdb_list_cache_get(
    cache: Dict[str, Tuple[float, List[Dict[str, Any]]]],
    key: str,
) -> Optional[List[Dict[str, Any]]]:
    entry = cache.pop(key, None)
    if entry is None:
        return None
    ts, values = entry
    if time.monotonic() - ts > _TMDB_CACHE_TTL:
        cache.pop(key, None)
        return None
    cache[key] = entry
    return copy.deepcopy(values)


def _tmdb_list_cache_set(
    cache: Dict[str, Tuple[float, List[Dict[str, Any]]]],
    key: str,
    values: List[Dict[str, Any]],
) -> None:
    cache.pop(key, None)
    cache[key] = (time.monotonic(), copy.deepcopy(values))
    _tmdb_cache_prune(cache)


def _tmdb_cache_prune(cache: Dict[str, Any]) -> None:
    now = time.monotonic()
    for cache_key, (timestamp, _) in list(cache.items()):
        if now - timestamp > _TMDB_CACHE_TTL:
            cache.pop(cache_key, None)
    while len(cache) > _TMDB_CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)), None)


async def _tmdb_singleflight(
    inflight: Dict[str, asyncio.Task[Any]],
    key: str,
    loader: Callable[[], Awaitable[Any]],
) -> Any:
    task = inflight.get(key)
    if task is not None and task.done():
        inflight.pop(key, None)
        task = None
    if task is None:
        task = asyncio.create_task(loader())
        inflight[key] = task

        def remove_completed(completed: asyncio.Task[Any]) -> None:
            if inflight.get(key) is completed:
                inflight.pop(key, None)

        task.add_done_callback(remove_completed)
    return await asyncio.shield(task)


_DOUBAN_CACHE_TTL = 7200.0


_douban_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


_DOUBAN_LAST_REQUEST_TIME = 0.0


_DOUBAN_MIN_INTERVAL = 2.0


_douban_logger = logging.getLogger(__name__)


def _douban_cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _douban_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _DOUBAN_CACHE_TTL:
        _douban_cache.pop(key, None)
        return None
    return value


def _douban_cache_set(key: str, value: Optional[Dict[str, Any]]) -> None:
    _douban_cache[key] = (time.monotonic(), value)
    if len(_douban_cache) > 200:
        now = time.monotonic()
        expired = [k for k, (ts, _) in _douban_cache.items() if now - ts > _DOUBAN_CACHE_TTL]
        for k in expired:
            _douban_cache.pop(k, None)


async def _douban_response(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[httpx.Response]:
    global _DOUBAN_LAST_REQUEST_TIME

    elapsed = time.monotonic() - _DOUBAN_LAST_REQUEST_TIME
    if elapsed < _DOUBAN_MIN_INTERVAL:
        await asyncio.sleep(_DOUBAN_MIN_INTERVAL - elapsed)
    _DOUBAN_LAST_REQUEST_TIME = time.monotonic()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://movie.douban.com/",
    }
    try:
        if client is not None:
            response = await client.get(url, headers=headers, params=params, follow_redirects=True)
        else:
            async with httpx.AsyncClient(timeout=15.0) as own_client:
                response = await own_client.get(url, headers=headers, params=params, follow_redirects=True)
        if response.status_code >= 400:
            _douban_logger.debug("豆瓣 API %s 返回 %d: %s", url, response.status_code, response.text[:200])
            return None
        return response
    except httpx.HTTPError as exc:
        _douban_logger.debug("豆瓣请求失败 %s: %s", url, exc)
        return None


async def _douban_request(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Any]:
    response = await _douban_response(url, params, client=client)
    if response is None:
        return None
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        _douban_logger.debug("豆瓣响应不是 JSON %s: %s", url, exc)
        return None
    return data if isinstance(data, (dict, list)) else None


def _douban_rating_result(subject: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    subject_id = str(subject.get("id") or "").strip()
    rating_text = str(subject.get("rate") or "").strip()
    try:
        rating = float(rating_text)
    except (TypeError, ValueError):
        return None
    if not subject_id or rating <= 0:
        return None
    return {
        "doubanRating": rating,
        "doubanUrl": str(subject.get("url") or f"https://movie.douban.com/subject/{subject_id}/").split("?", 1)[0],
        "doubanId": subject_id,
        "doubanTitle": str(subject.get("title") or "").strip(),
    }


async def _fetch_douban_subject(subject_id: str) -> Optional[Dict[str, Any]]:
    data = await _douban_request(
        "https://movie.douban.com/j/subject_abstract",
        {"subject_id": subject_id},
    )
    subject = data.get("subject") if isinstance(data, dict) else None
    return _douban_rating_result(subject) if isinstance(subject, dict) else None


async def fetch_douban_rating_by_imdb(imdb_id: str) -> Optional[Dict[str, Any]]:
    """通过豆瓣网页搜索把 IMDb ID 映射到条目。"""
    if not imdb_id or not re.match(r"^tt\d+$", imdb_id):
        return None
    cache_key = f"imdb:{imdb_id}"
    cached = _douban_cache_get(cache_key)
    if cached is not None:
        return cached

    response = await _douban_response("https://www.douban.com/search", {"cat": "1002", "q": imdb_id})
    match = re.search(r"movie\.douban\.com/subject/(\d+)", response.text if response is not None else "")
    result = await _fetch_douban_subject(match.group(1)) if match else None
    _douban_cache_set(cache_key, result)
    return result


async def fetch_douban_rating_by_search(title: str, year: str = "", media_type: str = "") -> Optional[Dict[str, Any]]:
    """通过豆瓣建议列表定位条目，再读取条目摘要中的评分。"""
    if not title or not title.strip():
        return None
    query = title.strip()
    cache_key = f"search:{media_type}:{year}:{query.casefold()}"
    cached = _douban_cache_get(cache_key)
    if cached is not None:
        return cached

    data = await _douban_request("https://movie.douban.com/j/subject_suggest", {"q": query})
    suggestions = data if isinstance(data, list) else []
    normalized_query = re.sub(r"[\W_]+", "", query, flags=re.UNICODE).casefold()

    def candidate_score(subject: Dict[str, Any], index: int) -> Tuple[int, int]:
        score = 0
        subject_year = str(subject.get("year") or "").strip()
        if year and subject_year == year:
            score += 20
        elif year and subject_year:
            score -= 10
        for candidate_title in (subject.get("title"), subject.get("sub_title")):
            normalized_title = re.sub(r"[\W_]+", "", str(candidate_title or ""), flags=re.UNICODE).casefold()
            if normalized_title and normalized_title == normalized_query:
                score += 12
            elif normalized_title and (normalized_title in normalized_query or normalized_query in normalized_title):
                score += 6
        return score, -index

    valid = [item for item in suggestions if isinstance(item, dict) and str(item.get("id") or "").isdigit()]
    selected = max(enumerate(valid), key=lambda pair: candidate_score(pair[1], pair[0]))[1] if valid else None
    result = await _fetch_douban_subject(str(selected.get("id"))) if selected else None
    _douban_cache_set(cache_key, result)
    return result


async def fetch_douban_rating(
    imdb_id: str = "",
    title: str = "",
    year: str = "",
    media_type: str = "",
) -> Optional[Dict[str, Any]]:
    """查询豆瓣评分。优先通过 IMDb ID 查，失败则按标题和年份搜索。"""
    if imdb_id:
        result = await fetch_douban_rating_by_imdb(imdb_id)
        if result:
            return result
    # 回退：标题搜索
    if title:
        return await fetch_douban_rating_by_search(title, year, media_type)
    return None


def infer_title(value: str) -> str:
    stem = strip_extension(str(value or ""))
    stem = re.sub(r"[\[{(【]?\s*(?:tmdbid|tmdb)[=\-_: ]?\d{2,10}\s*[\]})】]?", " ", stem, flags=re.I)
    stem = stem.replace("_", " ").replace(".", " ")
    year_match = YEAR_RE.search(stem)
    if year_match:
        prefix = stem[: year_match.start()].strip(" -_()[]【】")
        if len(prefix) >= 2:
            stem = prefix
    token_match = re.search(r"\b(?:S\d{1,3}E\d{1,5}|Season\s*\d+|EP?\d{1,5}|2160p|1080p|720p|WEB[- ]?DL|WEBRip|Blu[- ]?Ray|REMUX|HDTV|H[ .]?26[45]|HEVC|AVC|DDP?|AAC|FLAC)\b", stem, re.I)
    if token_match and token_match.start() >= 2:
        stem = stem[: token_match.start()]
    stem = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return clean_path_part(stem)


def extract_year(value: str) -> str:
    match = YEAR_RE.search(str(value or ""))
    return match.group(1) if match else ""


def format_season_episode(season: int, episode: int, last: Optional[int]) -> str:
    base = f"S{season:02d}E{episode:02d}"
    if last and last != episode:
        return f"{base}-E{last:02d}"
    return base


def extension_of(name: str) -> str:
    match = re.search(r"(\.[^.\\/]+)$", str(name or ""))
    return match.group(1) if match else ""


def strip_extension(name: str) -> str:
    ext = extension_of(name)
    return str(name or "")[: -len(ext)] if ext else str(name or "")


def clean_name_part(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", str(value or ""))).strip(". ")


def clean_path_part(value: str) -> str:
    return clean_name_part(value)


async def tmdb_find_by_id(token: str, language: str, tmdb_id: int, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
    types = [media_type] if media_type in {"movie", "tv"} else ["movie", "tv"]
    candidates: List[Dict[str, Any]] = []
    for kind in types:
        media = await tmdb_get_details(token, language, kind, tmdb_id)
        if media:
            candidates.append(media)
    return candidates


async def tmdb_search_candidates(
    token: str,
    language: str,
    query: str,
    year: str = "",
    media_type: Optional[str] = None,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    cache_key = ":".join([
        language,
        str(media_type or "multi"),
        str(year or ""),
        str(limit),
        query.strip().casefold(),
    ])
    cached = _tmdb_list_cache_get(_tmdb_search_cache, cache_key)
    if cached is not None:
        return cached

    async def load() -> List[Dict[str, Any]]:
        candidates = await _load_tmdb_search_candidates(
            token,
            language,
            query,
            year,
            media_type,
            limit,
        )
        _tmdb_list_cache_set(_tmdb_search_cache, cache_key, candidates)
        return candidates

    result = await _tmdb_singleflight(_tmdb_search_inflight, cache_key, load)
    return copy.deepcopy(result)


async def _load_tmdb_search_candidates(
    token: str,
    language: str,
    query: str,
    year: str,
    media_type: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:

    candidates: List[Dict[str, Any]] = []
    seen = set()
    endpoints: List[Tuple[Optional[str], str]] = (
        [(media_type, f"/search/{media_type}")] if media_type in {"movie", "tv"} else [(None, "/search/multi")]
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        for candidate_query in build_tmdb_search_queries(query):
            for endpoint_type, path in endpoints:
                params: Dict[str, Any] = {
                    "query": candidate_query,
                    "include_adult": "false",
                    "language": language,
                }
                if year and endpoint_type == "movie":
                    params["year"] = year
                if year and endpoint_type == "tv":
                    params["first_air_date_year"] = year

                data = await tmdb_request_json(token, path, params, client=client)
                pending_items: List[Tuple[str, int, Dict[str, Any]]] = []
                for item in data.get("results") or []:
                    kind = endpoint_type or str(item.get("media_type") or "")
                    if kind not in {"movie", "tv"}:
                        continue
                    key = f"{kind}:{item.get('id')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    item_id = positive_int(item.get("id")) or 0
                    pending_items.append((kind, item_id, item))
                    if len(pending_items) >= 6 or len(candidates) + len(pending_items) >= limit:
                        break

                # 并发获取所有详情
                if pending_items:
                    detail_tasks = [
                        tmdb_get_details(token, language, kind, item_id, client=client)
                        for kind, item_id, _ in pending_items
                    ]
                    details = await asyncio.gather(*detail_tasks)
                    for (kind, item_id, item), detail in zip(pending_items, details):
                        candidates.append(detail or normalize_tmdb_media(item, kind, []))

                if len(candidates) >= limit:
                    return candidates[:limit]
            if candidates and media_type in {"movie", "tv"}:
                break
    return candidates


async def tmdb_get_details(token: str, language: str, media_type: str, tmdb_id: int, *, client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, Any]]:
    if media_type not in {"movie", "tv"} or tmdb_id <= 0:
        return None
    cache_key = _tmdb_cache_key(tmdb_id, media_type, language)
    cached = _tmdb_cache_get(cache_key)
    if cached is not None:
        return cached

    async def load() -> Optional[Dict[str, Any]]:
        result = await _load_tmdb_details(token, language, media_type, tmdb_id, client=client)
        if result is not None:
            _tmdb_cache_set(cache_key, result)
        return result

    result = await _tmdb_singleflight(_tmdb_details_inflight, cache_key, load)
    return copy.deepcopy(result) if isinstance(result, dict) else None


async def _load_tmdb_details(
    token: str,
    language: str,
    media_type: str,
    tmdb_id: int,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    try:
        data = await tmdb_request_json(
            token,
            f"/{media_type}/{tmdb_id}",
            {
                "language": language,
                "append_to_response": "external_ids,alternative_titles,translations",
            },
            client=client,
        )
    except Exception:
        return None
    genres = [str(genre.get("name") or "") for genre in data.get("genres") or [] if str(genre.get("name") or "").strip()]
    return normalize_tmdb_media(data, media_type, genres)


async def tmdb_request_json(token: str, path: str, params: Optional[Dict[str, Any]] = None, *, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    headers = {"accept": "application/json"}
    query = dict(params or {})
    if token.startswith("ey"):
        headers["authorization"] = f"Bearer {token}"
    else:
        query["api_key"] = token
    url = f"{TMDB_API_BASE}{path}"
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            if client is not None:
                response = await client.get(url, headers=headers, params=query)
            else:
                async with httpx.AsyncClient(timeout=20.0) as own_client:
                    response = await own_client.get(url, headers=headers, params=query)
            if response.status_code == 429 or response.status_code >= 500:
                raise ValueError(f"TMDB {response.status_code}: {response.text[:300]}")
            if response.status_code >= 400:
                raise ValueError(f"TMDB {response.status_code}: {response.text[:300]}")
            data = response.json()
            return data if isinstance(data, dict) else {}
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))
        except ValueError as exc:
            error_text = str(exc)
            # 仅对 5xx/429 重试，4xx 直接抛出
            if attempt < 2 and ("429" in error_text or any(f"{code}" in error_text for code in ("500", "502", "503", "504"))):
                last_error = exc
                await asyncio.sleep(1.0 * (attempt + 1))
            else:
                raise
    raise last_error or ValueError("TMDB request failed")


def parse_tmdb_lookup_query(value: str) -> Dict[str, Any]:
    return parse_organize_manual_lookup_query(value)


def parse_organize_manual_lookup_query(value: str) -> Dict[str, Any]:
    raw_query = unicodedata.normalize("NFKC", to_basic_simplified_chinese(str(value or ""))).strip()
    result: Dict[str, Any] = {"query": raw_query}
    if not raw_query:
        return result

    working = raw_query
    typed_id = re.match(r"^(?P<type>movie|tv|电影|电视剧|剧集|动漫|动画)\s*[:：]\s*(?P<id>\d{2,10})$", working, re.I)
    if typed_id:
        result["mediaType"] = organize_lookup_media_type_from_token(typed_id.group("type"))
        result["tmdbId"] = int(typed_id.group("id"))
        result["query"] = typed_id.group("id")
        return result

    prefix = re.match(r"^(?P<type>movie|film|tv|show|series)\s*[:：]\s*(?P<rest>.+)$", working, re.I)
    if not prefix:
        prefix = re.match(r"^(?P<type>电影|电视剧|剧集|动漫|动画)\s*[:：]?\s*(?P<rest>.+)$", working, re.I)
    if prefix:
        result["mediaType"] = organize_lookup_media_type_from_token(prefix.group("type"))
        working = prefix.group("rest").strip()

    marker = TMDB_RE.search(working)
    if marker:
        result["tmdbId"] = int(marker.group(1))
        working = TMDB_RE.sub(" ", working).strip(" {}[]()：:.-_")

    direct = re.match(r"^\{?\s*(\d{2,10})\s*\}?$", working)
    if direct:
        result["tmdbId"] = int(direct.group(1))
        result["query"] = direct.group(1)
        return result

    normalized = normalize_organize_lookup_episode_numbers(working)
    season_episode = extract_organize_lookup_season_episode(normalized)
    if season_episode:
        result["mediaType"] = "tv"
        result["seasonEpisode"] = season_episode

    season_number = extract_organize_lookup_season_number(normalized)
    if season_number is not None:
        result["mediaType"] = "tv"
        result["season"] = season_number

    episode_hint = parse_organize_season_episode_hint(normalized)
    if episode_hint.get("episode") is not None:
        result["episode"] = episode_hint["episode"]

    year = extract_year(normalized)
    if year:
        result["year"] = year

    cleaned = clean_organize_lookup_query(normalized)
    result["query"] = cleaned or ("" if result.get("tmdbId") else raw_query)
    return result


def organize_lookup_media_type_from_token(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if text in {"movie", "film", "电影"}:
        return "movie"
    if text in {"tv", "show", "series", "电视剧", "剧集", "动漫", "动画"}:
        return "tv"
    return None


def normalize_organize_lookup_episode_numbers(value: str) -> str:
    number_pattern = r"[零〇一二两三四五六七八九十百千万]+"

    def replace_number(match: re.Match[str]) -> str:
        number = chinese_number_to_arabic(match.group("number"))
        if number is None:
            return match.group(0)
        return f"第{number}{match.group('unit')}"

    text = to_basic_simplified_chinese(str(value or ""))
    return re.sub(rf"第\s*(?P<number>{number_pattern})\s*(?P<unit>季|集|期|话|話)", replace_number, text)


def extract_organize_lookup_season_episode(value: str) -> str:
    text = normalize_organize_lookup_episode_numbers(value)
    season_number = extract_organize_lookup_season_number(text)
    range_hint = parse_organize_season_episode_range_hint(text)
    if range_hint:
        season = int(season_number if season_number is not None else range_hint["season"])
        return format_season_episode(season, int(range_hint["firstEpisode"]), int(range_hint["lastEpisode"]))
    single = parse_organize_single_season_episode_hint(text)
    if single:
        season = int(season_number if season_number is not None else single["season"])
        return format_season_episode(season, int(single["episode"]), None)
    return ""


def extract_organize_lookup_season_number(value: str) -> Optional[int]:
    text = normalize_organize_lookup_episode_numbers(value)
    explicit = re.search(r"(?:^|[^A-Za-z0-9])S(?P<season>\d{1,3})(?=$|[^A-Za-z0-9])", text, re.I)
    if explicit:
        return positive_int(explicit.group("season"))
    named = re.search(r"\bSeason\s*(?P<season>\d{1,3})\b", text, re.I)
    if named:
        return positive_int(named.group("season"))
    chinese = re.search(r"第\s*(?P<season>\d{1,3})\s*季", text)
    if chinese:
        return positive_int(chinese.group("season"))
    return None


def clean_organize_lookup_query(value: str) -> str:
    text = normalize_organize_lookup_episode_numbers(value)
    text = TMDB_RE.sub(" ", text)
    text = re.sub(r"\{?\s*\d{2,10}\s*\}?", " ", text) if re.fullmatch(r"\s*\{?\s*\d{2,10}\s*\}?\s*", text) else text
    text = re.sub(r"第\s*\d{1,3}\s*季", " ", text)
    text = re.sub(r"第\s*\d{1,4}\s*(?:[-~到至]\s*(?:第\s*)?\d{1,4}\s*)?[集期话話]", " ", text)
    text = re.sub(r"\bSeason\s*\d{1,3}\b", " ", text, flags=re.I)
    text = re.sub(r"\bS\d{1,3}\s*(?:E|EP)\s*\d{1,5}(?:\s*[-~]\s*(?:E|EP)?\s*\d{1,5})?\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:EP?|Episode)\s*\d{1,5}(?:\s*[-~]\s*(?:EP?|Episode)?\s*\d{1,5})?\b", " ", text, flags=re.I)
    text = re.sub(r"[\s(（]+(?:19|20)\d{2}[\s)）]*", " ", text)
    text = strip_tmdb_search_noise(text)
    cleaned = infer_title(text) or text
    return re.sub(r"\s+", " ", cleaned).strip(" -_./[](){}:：")


def normalize_tmdb_media_type(value: Optional[Any]) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in {"movie", "tv"} else None


def build_tmdb_search_queries(value: str) -> List[str]:
    normalized = re.sub(r"[._]+", " ", str(value or "")).strip()
    queries: List[str] = []
    for candidate in [
        normalized,
        infer_title(normalized),
        strip_tmdb_search_noise(normalized),
        strip_tmdb_search_year_tail(normalized),
    ]:
        add_tmdb_query(queries, candidate)

    chinese = " ".join(re.findall(r"[\u4e00-\u9fff]+", normalized))
    latin = " ".join(match.group(0) for match in re.finditer(r"[A-Za-z][A-Za-z0-9'’:-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'’:-]*)*", normalized))
    add_tmdb_query(queries, chinese)
    add_tmdb_query(queries, latin)
    return queries or [normalized]


def strip_tmdb_search_noise(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return ""
    match = re.search(
        r"(?:^|\s)(?:S\d{1,3}(?:E\d{1,5})?|EP?\d{1,5}|Complete|8K|6K|5K|4K|2K|UHD|QHD|FHD|HD|4320p|2160p|1440p|1080p|1080i|720p|576p|480p|WEB[- .]?DL|WEBRip|Blu[- .]?Ray|REMUX|HDTV|BDRip|HDRip|DVDRip|HEVC|AVC|H[. ]?265|H[. ]?264|x265|x264|HDR10\+?|DV|DoVi|HLG|SDR)\b",
        normalized,
        re.I,
    )
    cut = normalized[: match.start()].strip() if match and match.start() > 0 else normalized
    return strip_tmdb_search_year_tail(cut)


def strip_tmdb_search_year_tail(value: str) -> str:
    return re.sub(r"\b(?:19|20)\d{2}\b.*$", " ", str(value or "")).strip(" -_")


def add_tmdb_query(queries: List[str], value: str) -> None:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return
    key = normalized.lower()
    if key not in {item.lower() for item in queries}:
        queries.append(normalized)


def normalize_tmdb_media(item: Dict[str, Any], media_type: str, genres: List[str]) -> Dict[str, Any]:
    primary = str(item.get("title") if media_type == "movie" else item.get("name") or "")
    original = str(item.get("original_title") if media_type == "movie" else item.get("original_name") or "")
    localized = best_tmdb_localized_title(item, media_type)
    title = pick_tmdb_display_title(localized, primary, original) or f"TMDB {item.get('id') or ''}".strip()
    alternate = pick_tmdb_alternate_title(title, localized, primary, original)
    date = str(item.get("release_date") if media_type == "movie" else item.get("first_air_date") or "")
    seasons = []
    if media_type == "tv":
        for season in item.get("seasons") or []:
            number = positive_int(season.get("season_number"))
            count = positive_int(season.get("episode_count"))
            if number is not None and number >= 0 and count:
                seasons.append({"seasonNumber": number, "episodeCount": count})
    genre_ids = []
    for genre in item.get("genres") or []:
        genre_id = positive_int(genre.get("id")) if isinstance(genre, dict) else None
        if genre_id and genre_id not in genre_ids:
            genre_ids.append(genre_id)
    for raw_genre_id in item.get("genre_ids") or []:
        genre_id = positive_int(raw_genre_id)
        if genre_id and genre_id not in genre_ids:
            genre_ids.append(genre_id)
    return {
        "tmdbId": positive_int(item.get("id")) or 0,
        "imdbId": ((item.get("external_ids") or {}).get("imdb_id") or None) if isinstance(item.get("external_ids"), dict) else None,
        "mediaType": media_type,
        "title": title,
        "originalTitle": alternate,
        "aliases": collect_tmdb_aliases(item, media_type),
        "chineseTitles": collect_tmdb_chinese_titles(item, media_type),
        "englishTitles": collect_tmdb_english_titles(item, media_type),
        "year": date[:4] if re.match(r"^(?:19|20)\d{2}", date) else "",
        "overview": str(item.get("overview") or ""),
        "posterUrl": tmdb_image_url(item.get("poster_path"), "w780"),
        "backdropUrl": tmdb_image_url(item.get("backdrop_path"), "w1280"),
        "voteAverage": item.get("vote_average"),
        "genres": genres,
        "genreIds": genre_ids,
        "status": str(item.get("status") or ""),
        "seasons": seasons,
        "tmdbUrl": f"https://www.themoviedb.org/{media_type}/{item.get('id') or ''}",
    }


def best_tmdb_localized_title(item: Dict[str, Any], media_type: str) -> str:
    alternatives = []
    alternative_titles = item.get("alternative_titles") if isinstance(item.get("alternative_titles"), dict) else {}
    alternatives.extend(alternative_titles.get("titles") or [])
    alternatives.extend(alternative_titles.get("results") or [])
    translations = ((item.get("translations") or {}).get("translations") or []) if isinstance(item.get("translations"), dict) else []
    primary = item.get("title") if media_type == "movie" else item.get("name")

    for candidate in [primary, *titles_by_region(alternatives, ["CN", "SG"]), *translations_by_region(translations, ["CN", "SG"], media_type)]:
        clean = clean_tmdb_alias(candidate)
        if is_likely_chinese_title(clean):
            return clean
    return clean_tmdb_alias(primary)


def pick_tmdb_display_title(localized: str, primary: str, original: str) -> str:
    for candidate in (localized, primary, original):
        clean = clean_tmdb_alias(candidate)
        if is_likely_chinese_title(clean):
            return clean
    for candidate in (localized, primary, original):
        clean = clean_tmdb_alias(candidate)
        if clean:
            return clean
    return ""


def pick_tmdb_alternate_title(preferred: str, *values: str) -> str:
    for value in values:
        clean = clean_tmdb_alias(value)
        if clean and clean != preferred:
            return clean
    return ""


def collect_tmdb_aliases(item: Dict[str, Any], media_type: str) -> List[str]:
    aliases: List[str] = []
    alternative_titles = item.get("alternative_titles") if isinstance(item.get("alternative_titles"), dict) else {}
    alternatives = [*(alternative_titles.get("titles") or []), *(alternative_titles.get("results") or [])]
    translations = ((item.get("translations") or {}).get("translations") or []) if isinstance(item.get("translations"), dict) else []
    primary = item.get("title") if media_type == "movie" else item.get("name")
    original = item.get("original_title") if media_type == "movie" else item.get("original_name")
    values = [
        primary,
        original,
        *[item.get("title") for item in alternatives],
        *[translation.get("data", {}).get("title" if media_type == "movie" else "name") for translation in translations if isinstance(translation.get("data"), dict)],
    ]
    for value in values:
        clean = clean_tmdb_alias(value)
        if clean and clean.lower() not in {item.lower() for item in aliases}:
            aliases.append(clean)
    return aliases[:20]


def collect_tmdb_chinese_titles(item: Dict[str, Any], media_type: str) -> List[str]:
    values: List[str] = []
    alternative_titles = item.get("alternative_titles") if isinstance(item.get("alternative_titles"), dict) else {}
    alternatives = [*(alternative_titles.get("titles") or []), *(alternative_titles.get("results") or [])]
    translations = ((item.get("translations") or {}).get("translations") or []) if isinstance(item.get("translations"), dict) else []
    primary = item.get("title") if media_type == "movie" else item.get("name")
    candidates = [
        best_tmdb_localized_title(item, media_type),
        primary,
        *titles_by_region(alternatives, ["CN", "SG"]),
        *translations_by_region(translations, ["CN", "SG"], media_type),
    ]
    for candidate in candidates:
        clean = clean_tmdb_alias(to_basic_simplified_chinese(str(candidate or "")))
        if is_likely_chinese_title(clean):
            add_unique_title(values, clean)
    return values


def collect_tmdb_english_titles(item: Dict[str, Any], media_type: str) -> List[str]:
    values: List[str] = []
    alternative_titles = item.get("alternative_titles") if isinstance(item.get("alternative_titles"), dict) else {}
    alternatives = [*(alternative_titles.get("titles") or []), *(alternative_titles.get("results") or [])]
    translations = ((item.get("translations") or {}).get("translations") or []) if isinstance(item.get("translations"), dict) else []
    primary = item.get("title") if media_type == "movie" else item.get("name")
    original = item.get("original_title") if media_type == "movie" else item.get("original_name")
    candidates = [
        *[
            (translation.get("data") or {}).get("title" if media_type == "movie" else "name")
            for translation in translations
            if str(translation.get("iso_639_1") or "").lower() == "en"
        ],
        *titles_by_region(alternatives, ["US", "GB", "CA", "AU", "NZ", "IE"]),
    ]
    if str(item.get("original_language") or "").lower() == "en":
        candidates.extend([original, primary])
    for candidate in candidates:
        clean = clean_english_title(str(candidate or ""))
        if clean:
            add_unique_title(values, clean)
    return values


def titles_by_region(titles: List[Dict[str, Any]], regions: List[str]) -> List[str]:
    region_set = {region.upper() for region in regions}
    return [str(item.get("title") or "") for item in titles if str(item.get("iso_3166_1") or "").upper() in region_set]


def translations_by_region(translations: List[Dict[str, Any]], regions: List[str], media_type: str) -> List[str]:
    region_set = {region.upper() for region in regions}
    key = "title" if media_type == "movie" else "name"
    return [
        str((item.get("data") or {}).get(key) or "")
        for item in translations
        if str(item.get("iso_639_1") or "").lower() == "zh" and str(item.get("iso_3166_1") or "").upper() in region_set
    ]


def pick_best_tmdb_candidate(candidates: List[Dict[str, Any]], query: str, year: str, media_type: Optional[str]) -> Dict[str, Any]:
    return sorted(
        candidates,
        key=lambda item: tmdb_candidate_score(item, query, year, media_type),
        reverse=True,
    )[0]


def tmdb_candidate_score(item: Dict[str, Any], query: str, year: str, media_type: Optional[str]) -> int:
    score = 0
    if media_type and item.get("mediaType") == media_type:
        score += 20
    if year and str(item.get("year") or "") == str(year):
        score += 24
    query_key = tmdb_title_key(query)
    for title in [item.get("title"), item.get("originalTitle"), *(item.get("aliases") or [])]:
        title_key = tmdb_title_key(str(title or ""))
        if not query_key or not title_key:
            continue
        if title_key == query_key:
            score += 40
        elif query_key in title_key or title_key in query_key:
            score += 16
    try:
        score += int(float(item.get("voteAverage") or 0))
    except (TypeError, ValueError):
        pass
    return score


def tmdb_title_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def clean_tmdb_alias(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_english_title(value: str) -> str:
    normalized = clean_tmdb_alias(value)
    if not normalized or not re.search(r"[A-Za-z]", normalized):
        return ""
    letters = [char for char in normalized if char.isalpha()]
    if any(not ("A" <= char.upper() <= "Z") for char in letters):
        return ""
    return normalized


def add_unique_title(values: List[str], value: str) -> None:
    clean = clean_tmdb_alias(value)
    if not clean:
        return
    key = tmdb_title_key(clean)
    if not key or any(tmdb_title_key(item) == key for item in values):
        return
    values.append(clean)


def is_likely_chinese_title(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def tmdb_image_url(value: Any, size: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"https://image.tmdb.org/t/p/{size}/{path.lstrip('/')}"


def positive_int(value: Any) -> Optional[int]:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_organize_season_episode_hint(value: str) -> Dict[str, int]:
    range_hint = parse_organize_season_episode_range_hint(value)
    if range_hint:
        return {"season": int(range_hint["season"]), "episode": int(range_hint["firstEpisode"])}
    single = parse_organize_single_season_episode_hint(value)
    return single or {}


def parse_organize_season_episode_range_hint(value: str) -> Dict[str, int]:
    text = str(value or "")
    normalized = re.sub(r"\.[^.]+$", "", text)
    explicit = re.search(r"(?:^|[^A-Za-z0-9])S(?P<season>\d{1,3})[\s._-]*(?:E|EP)(?P<first>\d{1,4})(?:\s*(?:E|EP)\s*(?P<embyLast>\d{1,4})|\s*[-~到至]\s*(?:S\d{1,3}[\s._-]*)?(?:E|EP)?\s*(?P<rangeLast>\d{1,4}))(?=$|[^A-Za-z0-9])", normalized, re.I)
    if explicit:
        return {
            "season": int(explicit.group("season")),
            "firstEpisode": int(explicit.group("first")),
            "lastEpisode": int(explicit.group("embyLast") or explicit.group("rangeLast")),
        }

    episode_range = re.search(r"(?:^|[^A-Za-z0-9])(?:E|EP|Episode)\s*(?P<first>\d{1,4})\s*[-~到至]\s*(?:E|EP|Episode)?\s*(?P<last>\d{1,4})(?=$|[^A-Za-z0-9])", normalized, re.I)
    if episode_range:
        return {
            "season": 1,
            "firstEpisode": int(episode_range.group("first")),
            "lastEpisode": int(episode_range.group("last")),
        }

    number_pattern = r"[0-9零〇一二两兩三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟廿卅卌]+"
    cn_range = re.search(
        rf"第\s*(?P<first>{number_pattern})\s*(?:[-~到至])\s*(?:第\s*)?(?P<last>{number_pattern})\s*[集期话話]",
        normalized,
    )
    if cn_range:
        first = chinese_number_to_arabic(cn_range.group("first"))
        last = chinese_number_to_arabic(cn_range.group("last"))
        if first is None or last is None:
            return {}
        return {
            "season": 1,
            "firstEpisode": first,
            "lastEpisode": last,
        }

    single = parse_organize_single_season_episode_hint(normalized)
    if single and has_organize_combined_part_hint(normalized):
        return {
            "season": int(single["season"]),
            "firstEpisode": int(single["episode"]),
            "lastEpisode": int(single["episode"]) + 1,
        }

    return {}


def parse_organize_single_season_episode_hint(value: str) -> Dict[str, int]:
    text = re.sub(r"\.[^.]+$", "", str(value or ""))
    explicit = re.search(r"(?:^|[^A-Za-z0-9])S(?P<season>\d{1,3})[\s._-]*(?:E|EP)(?P<episode>\d{1,4})(?:\.\d+|[A-H])?(?=$|[^A-Za-z0-9])", text, re.I)
    if explicit:
        return to_organize_season_episode_hint(explicit.group("season"), explicit.group("episode"))

    compact = re.search(r"(?:^|[^A-Za-z0-9])(?P<season>\d{1,3})x(?P<episode>\d{1,4})(?=$|[^A-Za-z0-9])", text, re.I)
    if compact:
        return to_organize_season_episode_hint(compact.group("season"), compact.group("episode"))

    episode = re.search(r"(?:^|[^A-Za-z0-9])(?:EP|Episode)\s*(?P<episode>\d{1,4})(?=$|[^A-Za-z0-9])", text, re.I) or re.search(r"(?:^|[^A-Za-z0-9])E(?P<episode>\d{1,4})(?=$|[^A-Za-z0-9])", text, re.I)
    if episode:
        return to_organize_season_episode_hint("1", episode.group("episode"))

    cn_episode = re.search(
        r"第\s*(?P<episode>[0-9零〇一二两兩三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟廿卅卌]+)\s*[集期话話]",
        text,
    )
    if cn_episode:
        episode_number = chinese_number_to_arabic(cn_episode.group("episode"))
        return {"season": 1, "episode": episode_number} if episode_number and episode_number > 0 else {}

    return {}


def to_organize_season_episode_hint(season_value: str, episode_value: str) -> Dict[str, int]:
    try:
        season = int(str(season_value).strip())
    except (TypeError, ValueError):
        season = -1
    episode = positive_int(episode_value)
    if season < 0 or episode is None:
        return {}
    return {"season": season, "episode": episode}


def has_organize_combined_part_hint(value: str) -> bool:
    text = unicodedata.normalize("NFKC", to_basic_simplified_chinese(str(value or "")))
    return bool(
        re.search(r"(?:上\s*(?:[&+＋/／、,，和及]|and)\s*下|上下(?:篇|集|期|部|半场)?|上\s*下)", text, re.I)
        or re.search(r"(?:下\s*(?:[&+＋/／、,，和及]|and)\s*上|下上(?:篇|集|期|部|半场)?)", text, re.I)
        or re.search(r"(?:Part|Pt)[\s._-]*1\s*(?:[&+＋/／、,，和及]|and|-)\s*(?:Part|Pt)?[\s._-]*2", text, re.I)
    )


def chinese_number_to_arabic(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "兩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "壹": 1,
        "贰": 2,
        "貳": 2,
        "叁": 3,
        "參": 3,
        "肆": 4,
        "伍": 5,
        "陆": 6,
        "陸": 6,
        "柒": 7,
        "捌": 8,
        "玖": 9,
    }
    units = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in {"廿", "卅", "卌"}:
            section += {"廿": 20, "卅": 30, "卌": 40}[char]
            number = 0
            continue
        if char in digits:
            number = digits[char]
            continue
        if char in units:
            section += (number or 1) * units[char]
            number = 0
            continue
        if char in {"万", "萬"}:
            total += (section + number or 1) * 10000
            section = 0
            number = 0
            continue
        return None
    return total + section + number


def to_basic_simplified_chinese(value: str) -> str:
    mapping = {
        "鏈": "链",
        "鋸": "锯",
        "體": "体",
        "劇": "剧",
        "節": "节",
        "國": "国",
        "與": "与",
        "門": "门",
        "風": "风",
        "雲": "云",
        "馬": "马",
        "龍": "龙",
        "廣": "广",
        "東": "东",
        "臺": "台",
        "灣": "湾",
        "萬": "万",
        "幾": "几",
        "個": "个",
        "會": "会",
        "來": "来",
        "過": "过",
        "對": "对",
        "開": "开",
        "關": "关",
        "實": "实",
        "見": "见",
        "學": "学",
        "後": "后",
        "間": "间",
        "歡": "欢",
        "樂": "乐",
        "號": "号",
        "裡": "里",
        "裏": "里",
        "臥": "卧",
        "話": "话",
    }
    return re.sub(r"[鏈鋸體劇節國與門風雲馬龍廣東臺灣萬幾個會來過對開關實見學後間歡樂號裡裏臥話]", lambda match: mapping.get(match.group(0), match.group(0)), str(value or ""))
