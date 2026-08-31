from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx

from .defaults import DEFAULT_SUBMISSION_CONFIG
from .pan115 import (
    extract_pan115_offline_links,
    submit_115_offline_from_text,
)
from .session_store import SessionStore, utc_now_iso
from .version_semantics import (
    extract_version_alias,
    format_episode_ranges,
    normalize_version_fields,
    version_semantic_key,
)


PAN123_SHARE_HOST_PATTERN = (
    r"(?:"
    r"(?:www\.|yun\.)?123pan\.(?:cn|com)"
    r"|(?:www\.)?(?:123912|123635|123684|123865)\.com"
    r"|[A-Za-z0-9-]+\.share\.(?:123pan\.(?:cn|com)|(?:123912|123635|123684|123865)\.com)"
    r")"
)
PAN123_WEB_SHARE_PATTERN = rf"https?://{PAN123_SHARE_HOST_PATTERN}/(?:(?:s|ps|123pan)/|gsb/s/)[^\s<>'\"]+"
SHARE_LINK_RE = re.compile(rf"(123FLCPV2\$%?[^\s<>'\"]+|{PAN123_WEB_SHARE_PATTERN})", re.I)
WEB_SHARE_LINK_RE = re.compile(PAN123_WEB_SHARE_PATTERN, re.I)
FASTLINK_RE = re.compile(r"123FLCPV2\$%?[^\r\n<>'\"]+", re.I)
PWD_RE = re.compile(r"(?:pwd=|提取码[:：\s]*|访问码[:：\s]*|密码[:：\s]*)([A-Za-z0-9]{4,8})", re.I)
TMDB_ID_RE = re.compile(r"\{?\b(?:tmdbid|tmdb)[=\-_: ]?(?P<id>\d{2,10})\}?", re.I)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d|p)", re.I)
VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|mov|rmvb|wmv|flv|webm|m4v|mpeg|mpg|3gp|ts|m2ts|mts)$", re.I)
SEASON_FOLDER_NAME_RE = re.compile(r"^Season\s*(\d+)\s*(.*)", re.I)
EPISODE_NUMBER_RE = re.compile(r"[SE](\d{1,4})(?:E(\d{1,4}))?|EP?(\d{1,4})", re.I)
TELEGRAM_API_BASE = "https://api.telegram.org"
SUBMISSION_DRAFTS_KEY = "submission_drafts"
SUBMISSION_MEDIA_CACHE_KEY = "submission_share_media_cache"
RICH_NOTE_CAPABILITY_KEY_PREFIX = "submission_rich_note_capability:"
CHANNEL_PUBLISH_QUEUES: Dict[str, asyncio.Future[None]] = {}
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
logger = logging.getLogger(__name__)


class _TelegramNoteHTMLParser(HTMLParser):
    """Convert editor HTML into the conservative HTML subset Telegram accepts."""

    _tag_map = {"strong": "b", "em": "i", "strike": "s", "del": "s"}
    _allowed = {"b", "i", "u", "s", "code", "pre", "blockquote", "a"}
    _block = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}
    _ignored_content = {"head", "style", "script", "title", "template", "noscript"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        # One source element can expand to several Telegram tags.  For
        # example, HarmonyOS persists bold/italic/underline as CSS on a
        # ``span`` instead of using ``<strong>``/``<em>`` elements.
        self.stack: List[tuple[str, List[str]]] = []
        self.ignored_depth = 0

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    @staticmethod
    def _style_tags(attrs: List[tuple[str, Optional[str]]]) -> List[str]:
        """Map the inline styles emitted by native note editors to Telegram HTML.

        ArkTS ``StyledString.toHtml`` writes styles such as
        ``font-weight: bold`` and ``text-decoration: underline`` on spans.
        Telegram does not accept a style attribute, so retaining that span
        would make a correctly saved database note look like plain text.
        """
        style = next((str(value or "") for key, value in attrs if key.lower() == "style"), "")
        properties: Dict[str, str] = {}
        for declaration in style.split(";"):
            key, separator, value = declaration.partition(":")
            if separator and key.strip():
                properties[key.strip().lower()] = value.strip().lower()

        tags: List[str] = []
        weight = properties.get("font-weight", "")
        if weight == "bold" or re.search(r"(?:^|\D)[5-9]00(?:\D|$)", weight):
            tags.append("b")
        if properties.get("font-style", "") in {"italic", "oblique"}:
            tags.append("i")
        decoration = properties.get("text-decoration", "").replace("-", " ")
        if "underline" in decoration:
            tags.append("u")
        if "line through" in decoration or "strikethrough" in decoration:
            tags.append("s")
        if "monospace" in properties.get("font-family", ""):
            # Telegram does not allow other formatting inside code.  Keep the
            # code semantic rather than generating invalid nested entities.
            return ["code"]
        return tags

    @staticmethod
    def _deduplicated(tags: List[str]) -> List[str]:
        result: List[str] = []
        for tag in tags:
            if tag not in result:
                result.append(tag)
        return result

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        name = self._tag_map.get(tag.lower(), tag.lower())
        if self.ignored_depth:
            if name not in self._void:
                self.ignored_depth += 1
            return
        if name in self._ignored_content:
            self.ignored_depth = 1
            return
        if name == "br":
            self._newline()
            return
        if name in self._void:
            return
        opened: List[str] = []
        semantic_opened: List[str] = []
        if name in self._block:
            self._newline()
            if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                semantic_opened.append("b")
        elif name == "li":
            self._newline()
            self.parts.append("• ")
        elif name in self._allowed:
            if name == "a":
                href = next((str(value or "") for key, value in attrs if key.lower() == "href"), "")
                if re.match(r"https?://", href, re.I):
                    semantic_opened.append(name)
            else:
                semantic_opened.append(name)

        # ``span`` itself is not a Telegram tag, but its native-editor CSS
        # may contain the actual rich-text meaning.  Also inspect styles on
        # semantic elements for documents created by other clients.
        opened = self._deduplicated([*semantic_opened, *self._style_tags(attrs)])
        for semantic_tag in semantic_opened:
            if semantic_tag == "a":
                href = next((str(value or "") for key, value in attrs if key.lower() == "href"), "")
                self.parts.append(f'<a href="{escape(href, quote=True)}">')
            else:
                self.parts.append(f"<{semantic_tag}>")
        for style_tag in opened:
            if style_tag not in semantic_opened:
                self.parts.append(f"<{style_tag}>")
        self.stack.append((name, opened))

    def handle_startendtag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        name = self._tag_map.get(tag.lower(), tag.lower())
        self.handle_starttag(tag, attrs)
        if name not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = self._tag_map.get(tag.lower(), tag.lower())
        if self.ignored_depth:
            self.ignored_depth -= 1
            return
        if name in self._void:
            return
        if name in self._block or name == "li":
            self._newline()
        if not self.stack:
            return
        _, opened = self.stack.pop()
        for emitted in reversed(opened):
            self.parts.append(f"</{emitted}>")

    def handle_data(self, data: str) -> None:
        if data and not self.ignored_depth and (data.strip() or "\n" not in data and "\r" not in data):
            self.parts.append(escape(data))

    def value(self) -> str:
        while self.stack:
            _, opened = self.stack.pop()
            for opened in reversed(opened):
                self.parts.append(f"</{opened}>")
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()

# --------------- Telegram 连接复用 ---------------
_telegram_client: Optional[httpx.AsyncClient] = None
_telegram_client_loop: Optional[asyncio.AbstractEventLoop] = None
_telegram_client_managed = False


async def start_telegram_client() -> None:
    global _telegram_client, _telegram_client_loop, _telegram_client_managed
    loop = asyncio.get_running_loop()
    if _telegram_client is not None and not _telegram_client.is_closed and _telegram_client_loop is loop:
        _telegram_client_managed = True
        return
    await close_telegram_client()
    _telegram_client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(max_connections=6, max_keepalive_connections=6, keepalive_expiry=30.0),
    )
    _telegram_client_loop = loop
    _telegram_client_managed = True


async def close_telegram_client() -> None:
    global _telegram_client, _telegram_client_loop, _telegram_client_managed
    client = _telegram_client
    _telegram_client = None
    _telegram_client_loop = None
    _telegram_client_managed = False
    if client is not None and not client.is_closed:
        await client.aclose()


def _telegram_request_client() -> tuple[httpx.AsyncClient, bool]:
    loop = asyncio.get_running_loop()
    if (
        _telegram_client_managed
        and _telegram_client is not None
        and not _telegram_client.is_closed
        and _telegram_client_loop is loop
    ):
        return _telegram_client, False
    return (
        httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=6, keepalive_expiry=30.0),
        ),
        True,
    )


def normalize_database_note(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    # Persisted drafts already contain the sanitized Telegram HTML rather than
    # the original client payload.  Accept all three representations so a
    # preview and the later channel publish render the same rich note.
    html = str(value.get("noteContent") or value.get("html") or value.get("telegramHtml") or "").strip()
    plain = str(value.get("plainText") or "").strip()
    if not plain and html:
        parser = _TelegramNoteHTMLParser()
        parser.feed(html)
        plain = re.sub(r"<[^>]+>", "", unescape(parser.value())).strip()
    if not html and not plain:
        return None
    parser = _TelegramNoteHTMLParser()
    parser.feed(html or escape(plain))
    rich = parser.value()
    return {"html": html, "plainText": plain or re.sub(r"<[^>]+>", "", unescape(rich)).strip(), "telegramHtml": rich}


def rich_note_capability_key(bot_token: str) -> str:
    digest = hashlib.sha256(str(bot_token or "").encode("utf-8")).hexdigest()
    return RICH_NOTE_CAPABILITY_KEY_PREFIX + digest


def database_note_mode(store: Optional[SessionStore], bot_token: str) -> str:
    if not store or not bot_token:
        return "rich"
    value = store.read_value(rich_note_capability_key(bot_token))
    return "plain" if value == "plain" else "rich"


def save_database_note_mode(store: Optional[SessionStore], bot_token: str, mode: str) -> None:
    if store and bot_token:
        store.write_value(rich_note_capability_key(bot_token), "plain" if mode == "plain" else "rich")


def set_database_note_mode(draft: Dict[str, Any], mode: str) -> None:
    note = draft.get("databaseNote") if isinstance(draft.get("databaseNote"), dict) else None
    if note is not None:
        note["mode"] = "plain" if mode == "plain" else "rich"


def is_rich_note_parse_error(error: BaseException) -> bool:
    value = str(error or "").lower()
    return any(marker in value for marker in ("can't parse entities", "cannot parse entities", "unsupported start tag", "entity parsing", "parse_mode"))


def combined_submission_note(metadata_note: Any, database_note: Any) -> str:
    base = str(metadata_note or "").strip()
    if not isinstance(database_note, dict) or str(database_note.get("mode") or "rich") != "plain":
        return base
    plain = str(database_note.get("plainText") or "").strip()
    if not plain:
        return base
    addition = f"【数据库的备注信息】\n{plain}"
    return f"{base}\n{addition}" if base else addition


def database_note_rich_block(draft: Dict[str, Any]) -> str:
    note = draft.get("databaseNote") if isinstance(draft.get("databaseNote"), dict) else {}
    if str(note.get("mode") or "rich") == "plain":
        return ""
    rich = str(note.get("telegramHtml") or "").strip()
    return f"📝 数据库备注：\n{rich}" if rich else ""


async def submit_submission_links(
    store: SessionStore,
    links: List[Dict[str, Any]],
    source_label: str,
    target_user_id: Optional[int] = None,
    source_text: str = "",
    owner_chat_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    source_message_id: int = 0,
    max_links: int = 10,
    interaction_message_ids: Optional[List[int]] = None,
    submitter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = store.read_submission_config()
    if not links:
        raise ValueError("没有找到 123 分享链接或秒传链接")

    bot_token = str(config.get("botToken") or "").strip()
    allowed_ids = positive_ints(config.get("channelOwnerUserIds") or config.get("allowedUserIds") or [])
    target = int(target_user_id or (allowed_ids[0] if allowed_ids else 0))
    if target <= 0:
        raise ValueError("请先在投稿机器人配置里填写允许使用的 Telegram User ID")
    drafts: List[Dict[str, Any]] = []
    sent_count = 0
    first_error = ""
    owner_chat = int(owner_chat_id or target)
    owner_user = int(owner_user_id or target)
    for link in unique_submission_links(links)[: max(1, min(int(max_links or 10), 10))]:
        try:
            normalized = normalize_submission_link(link, source_text)
            if str(normalized.get("provider") or "") != "123fastlink":
                normalized["sourceText"] = str(source_text or normalized.get("sourceText") or normalized.get("cleanUrl") or "")
            inspection = normalized.get("inspection") if isinstance(normalized.get("inspection"), dict) else None
            if not inspection:
                inspection = await inspect_submission_link(normalized)
            cached = get_share_media_cache(store, str(normalized.get("cleanUrl") or normalized.get("url") or ""))
            cached_media = cached.get("media") if isinstance(cached.get("media"), dict) else None
            draft = await build_submission_draft(
                config,
                normalized,
                inspection,
                source_label,
                cached_media,
                owner_chat_id=owner_chat,
                owner_user_id=owner_user,
                source_message_id=source_message_id,
                interaction_message_ids=interaction_message_ids,
                store=store,
                submitter=submitter,
            )
            if isinstance(draft.get("databaseNote"), dict):
                set_database_note_mode(draft, database_note_mode(store, bot_token))
                refresh_submission_caption(draft, config, store)
            save_share_media_cache(store, str(draft.get("share", {}).get("cleanUrl") or ""), draft.get("media") if isinstance(draft.get("media"), dict) else {}, str(cached.get("source") or "auto"))
            saved = append_submission_draft(store, draft)
            if bot_token:
                try:
                    preview = await send_submission_preview_result(bot_token, target, saved, config, store=store)
                    sent_chunks = int(preview.get("sentCount") or 0)
                    if sent_chunks > 0:
                        sent_count += 1
                    saved = save_submission_draft(store, saved)
                    saved = mark_submission_draft_sent(store, str(saved.get("id") or ""), sent_chunks, int(preview.get("firstMessageId") or 0))
                    await cleanup_stale_submission_drafts(store, bot_token, saved)
                except Exception as error:
                    first_error = first_error or f"投稿机器人发送失败：{error}"
            drafts.append(saved)
        except Exception as error:
            first_error = first_error or str(error)

    if not drafts:
        raise ValueError(first_error or "投稿草稿生成失败")

    return {
        "sentCount": sent_count,
        "draftCount": len(drafts),
        "drafts": drafts,
        **({"error": first_error} if first_error else {}),
    }


async def build_submission_draft(
    config: Dict[str, Any],
    link: Dict[str, Any],
    inspection: Dict[str, Any],
    source_label: str,
    cached_media: Optional[Dict[str, Any]] = None,
    owner_chat_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    source_message_id: int = 0,
    interaction_message_ids: Optional[List[int]] = None,
    store: Optional[SessionStore] = None,
    submitter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_text = str(link.get("sourceText") or "").strip()
    recognition = build_media_recognition_input(source_text, inspection)
    metadata = recognize_submission_metadata(recognition["text"], recognition["inspection"], config)
    media = should_use_cached_media(cached_media, metadata) or await find_submission_media(config, metadata, recognition["inspection"])
    media = await enrich_submission_douban(media, metadata, config)
    metadata = fill_submission_metadata(metadata, media, recognition["inspection"])
    link_note = str(link.get("note") or "").strip()
    database_note = normalize_database_note(link.get("databaseNote"))
    if link_note:
        metadata["note"] = link_note
    elif not metadata.get("note"):
        file_names = [str(n or "") for n in recognition["inspection"].get("fileNames") or []]
        version_note = _detect_multi_version_note(file_names, config, media)
        if version_note:
            metadata["note"] = version_note
    documents = normalize_submission_documents(link.get("documents") or link.get("document"))
    draft = {
        "id": uuid.uuid4().hex,
        "ownerChatId": int(owner_chat_id or 0),
        "ownerUserId": int(owner_user_id or 0) if owner_user_id is not None else None,
        "submitter": normalize_submission_submitter(submitter),
        "routeOwnerUserId": 0,
        "sourceMessageId": int(source_message_id or 0),
        "previewMessageId": 0,
        "interactionMessageIds": [int(value) for value in interaction_message_ids or [] if safe_int(value) > 0],
        "status": "draft",
        "sourceLabel": source_label,
        "share": {
            "url": str(link.get("url") or link.get("cleanUrl") or ""),
            "cleanUrl": str(link.get("cleanUrl") or link.get("url") or ""),
            "password": str(link.get("password") or ""),
            "provider": str(link.get("provider") or "123pan"),
            "title": str(link.get("title") or ""),
            "sourceText": source_text,
        },
        "inspection": recognition["inspection"],
        "metadata": metadata,
        "media": media,
        **({"databaseNote": {**database_note, "mode": "rich"}} if database_note else {}),
        "appendFooter": True,
        "createdAt": utc_now_iso(),
        "updatedAt": utc_now_iso(),
    }
    if documents:
        draft["documents"] = documents
    channel = select_submission_channel(store, int(owner_user_id or 0), draft) if store else select_submission_channel(config, draft)
    if channel:
        draft["routeOwnerUserId"] = int(owner_user_id or 0)
        draft["routeChannelId"] = str(channel.get("id") or "")
        draft["routeChannelTitle"] = str(channel.get("title") or "")
        draft["routeChannelChatId"] = str(channel.get("chatId") or "")
        draft["channelTitle"] = str(channel.get("title") or "")
        draft["channelChatId"] = str(channel.get("chatId") or "")
    caption = render_submission_caption(draft, config, store)
    draft["caption"] = caption
    draft["text"] = caption
    draft["linkCount"] = 1
    return draft


async def find_submission_media(config: Dict[str, Any], metadata: Dict[str, Any], inspection: Dict[str, Any]) -> Dict[str, Any]:
    media_type = str(metadata.get("mediaType") or "unknown")
    normalized_type = media_type if media_type in {"movie", "tv"} else None
    title = str(metadata.get("title") or inspection.get("title") or first_media_filename(inspection.get("fileNames") or []) or "未识别媒体").strip()
    year = str(metadata.get("year") or "").strip()
    tmdb_id = int(metadata.get("tmdbId") or 0)
    token = str(config.get("tmdbToken") or "").strip()
    language = str(config.get("tmdbLanguage") or "zh-CN").strip() or "zh-CN"

    media: Dict[str, Any] = {}
    if token:
        try:
            from .tmdb import pick_best_tmdb_candidate, tmdb_find_by_id, tmdb_search_candidates

            candidates = await tmdb_find_by_id(token, language, tmdb_id, normalized_type) if tmdb_id else []
            if not candidates and title:
                candidates = await tmdb_search_candidates(token, language, title, year, normalized_type)
            if candidates:
                media = pick_best_tmdb_candidate(candidates, title, year, normalized_type)
        except Exception:
            pass

    if not media:
        media = {
            "tmdbId": tmdb_id or None,
            "mediaType": normalized_type or ("tv" if metadata.get("seasonEpisode") else "movie"),
            "title": title or "未识别媒体",
            "originalTitle": "",
            "aliases": [],
            "year": year,
            "overview": "",
            "posterUrl": "",
            "backdropUrl": "",
            "voteAverage": None,
            "genres": [],
            "status": "",
            "tmdbUrl": f"https://www.themoviedb.org/{normalized_type or 'movie'}/{tmdb_id}" if tmdb_id else "",
        }

    return media


async def enrich_submission_douban(
    media: Dict[str, Any],
    metadata: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    result = dict(media)
    source_rating = metadata.get("doubanRating")
    if source_rating not in (None, ""):
        result["doubanRating"] = source_rating
        return result
    if result.get("doubanRating") not in (None, ""):
        return result
    douban_title = str(result.get("title") or metadata.get("title") or "").strip()
    has_imdb_id = bool(str(result.get("imdbId") or "").strip())
    can_search_by_title = bool(str(config.get("tmdbToken") or "").strip() and result.get("tmdbId") and douban_title)
    if not has_imdb_id and not can_search_by_title:
        return result
    try:
        from .tmdb import fetch_douban_rating

        douban = await fetch_douban_rating(
            str(result.get("imdbId") or "").strip(),
            douban_title,
            str(result.get("year") or metadata.get("year") or "").strip(),
            str(result.get("mediaType") or metadata.get("mediaType") or "").strip(),
        )
        if douban:
            result.update(douban)
    except Exception as exc:
        logger.debug("豆瓣评分查询失败: %s", exc)
    return result


def build_media_recognition_input(text: str, inspection: Dict[str, Any]) -> Dict[str, Any]:
    file_names = deduplicate_file_names(inspection.get("fileNames") or [])
    raw_title = str(inspection.get("title") or "").strip()
    inferred_title = infer_title_from_file_names(file_names)
    title = inferred_title if is_weak_recognition_title(raw_title) else raw_title
    title = title or inferred_title or ""
    primary_text = "\n".join(item for item in [title, *file_names] if item)
    raw_text = join_unique_lines([primary_text, str(inspection.get("rawText") or ""), text])
    return {
        "text": primary_text or text,
        "inspection": {
            **inspection,
            "title": title,
            "fileNames": file_names,
            "rawText": strip_share_artifacts(raw_text),
        },
    }


def recognize_submission_metadata(text: str, inspection: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    file_names = deduplicate_file_names(inspection.get("fileNames") or [])
    source_text = "\n".join(
        item
        for item in [
            strip_share_artifacts(text),
            str(inspection.get("title") or ""),
            *file_names,
            strip_share_artifacts(str(inspection.get("rawText") or "")),
        ]
        if item
    )
    release_group_files = deduplicate_file_names([*file_names, *extract_likely_file_names(source_text)])
    title, year = extract_title_year(source_text)
    metadata: Dict[str, Any] = {
        "tmdbId": extract_tmdb_id(source_text),
        "doubanRating": extract_douban_rating(source_text),
        "mediaType": infer_submission_media_type(source_text),
        "title": title or infer_title_from_text(source_text) or infer_title_from_file_names(file_names),
        "year": year or extract_year(source_text),
        "quality": collect_quality(file_names) or extract_quality(source_text),
        "source": collect_source(file_names) or extract_source(source_text),
        "resourceType": collect_source(file_names) or extract_source(source_text),
        "size": str(inspection.get("size") or ""),
        "seasonEpisode": summarize_season_episodes(file_names) or first_season_episode(source_text),
        "effect": collect_effect(file_names) or extract_effect(source_text),
        "edition": "",
        "videoFormat": "",
        "resourceTerm": "",
        "releaseGroup": collect_release_group(release_group_files, config),
        "videoCodec": collect_video_codec(file_names) or extract_video_codec(source_text),
        "videoBit": "",
        "audioCodec": collect_audio_codec(file_names) or extract_audio_codec(source_text),
        "fps": collect_fps(file_names) or extract_fps(source_text),
        "bitDepth": collect_bit_depth(file_names) or extract_bit_depth(source_text),
        "webSource": collect_web_source(file_names, config) or extract_web_source(source_text, config),
        "itunes": collect_itunes_source(file_names) or extract_itunes_source(source_text),
        "highQuality": "HQ" if re.search(r"\bHQ\b", source_text, re.I) else "",
        "edr": "EDR" if re.search(r"\bEDR\b", source_text, re.I) else "",
        "part": "",
        "note": "",
        "tags": [],
    }
    apply_config_recognition_rules(metadata, source_text, config, release_group_files)
    metadata["tags"] = build_submission_tags(metadata)
    return {key: value for key, value in metadata.items() if value not in (None, "") or key == "tags"}


def extract_douban_rating(value: str) -> Optional[float]:
    match = re.search(
        r"(?:豆瓣(?:评分)?|douban(?:\s*rating)?)\s*(?:[:：]\s*)?(10(?:\.0)?|[0-9](?:\.\d)?)\s*(?:/\s*10)?",
        str(value or ""),
        re.I,
    )
    if not match:
        return None
    rating = float(match.group(1))
    return rating if 0 < rating <= 10 else None


def apply_config_recognition_rules(
    metadata: Dict[str, Any],
    text: str,
    config: Dict[str, Any],
    file_names: Optional[Iterable[Any]] = None,
) -> None:
    for rule in config.get("recognitionRules") or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False or not rule.get("pattern"):
            continue
        pattern = js_named_groups_to_python(str(rule.get("pattern") or ""))
        rule_text = "\n".join(submission_video_file_names(file_names or [])) if "(?P<releaseGroup>" in pattern else text
        if not rule_text:
            continue
        flags = re.I if "i" in str(rule.get("flags") or "") else 0
        try:
            match = re.search(pattern, rule_text, flags)
        except re.error:
            continue
        if not match:
            continue
        for key, value in match.groupdict().items():
            if not value or metadata.get(key):
                continue
            start, end = match.span(key)
            normalized = normalize_metadata_value(key, value, config)
            if valid_recognition_field(key, normalized, rule_text, start, end):
                metadata[key] = normalized


def fill_submission_metadata(metadata: Dict[str, Any], media: Dict[str, Any], inspection: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(metadata)
    result["title"] = result.get("title") or media.get("title") or inspection.get("title") or "未识别媒体"
    result["year"] = result.get("year") or media.get("year") or ""
    result["tmdbId"] = result.get("tmdbId") or media.get("tmdbId") or None
    result["mediaType"] = result.get("mediaType") if result.get("mediaType") in {"movie", "tv"} else media.get("mediaType") or "unknown"
    result["tags"] = [genre.replace(" ", "") for genre in media.get("genres") or [] if str(genre or "").strip()]
    if not result.get("size") and inspection.get("size"):
        result["size"] = inspection["size"]
    completed_season_label = tmdb_completed_season_label(media, inspection.get("fileNames") or [])
    if completed_season_label:
        result["seasonEpisode"] = completed_season_label
    return result


def normalize_submission_submitter(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the small, non-sensitive Telegram profile snapshot used in a post."""
    source = value if isinstance(value, dict) else {}
    user_id = safe_int(source.get("id") or source.get("userId"))
    username = str(source.get("username") or "").strip().lstrip("@")[:64]
    first_name = str(source.get("first_name") or source.get("firstName") or "").strip()[:128]
    last_name = str(source.get("last_name") or source.get("lastName") or "").strip()[:128]
    if not user_id and not username and not first_name and not last_name:
        return {}
    return {
        "userId": user_id,
        "username": username,
        "firstName": first_name,
        "lastName": last_name,
    }


def submission_submitter_name(draft: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Return the contributor's public Telegram label, or empty for Bot admins.

    The configured share name is the site's own attribution.  It should remain
    on submissions made by a Bot administrator, but must not be shown as if it
    belonged to a collaborator who supplied their own link.
    """
    submitter = normalize_submission_submitter(draft.get("submitter"))
    user_id = safe_int(submitter.get("userId"))
    if not submitter or not user_id or telegram_admin_allowed(config, user_id):
        return ""
    username = str(submitter.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    display_name = " ".join(
        value for value in (str(submitter.get("firstName") or "").strip(), str(submitter.get("lastName") or "").strip()) if value
    )
    return display_name or "投稿用户"


def submission_share_name(draft: Dict[str, Any], config: Dict[str, Any]) -> str:
    submitter_name = submission_submitter_name(draft, config)
    if submitter_name:
        return submitter_name
    templates = config.get("templates") if isinstance(config.get("templates"), dict) else {}
    return str(templates.get("shareName") or "123").strip() or "123"


def render_submission_caption(
    draft: Dict[str, Any],
    config: Dict[str, Any],
    store: Optional[SessionStore] = None,
    include_route: bool = True,
) -> str:
    templates = config.get("templates") if isinstance(config.get("templates"), dict) else {}
    template = str(templates.get("caption") or "{title}\n{shareUrl}")
    if not include_route:
        template = strip_route_line(template)
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    inspection = draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {}
    display_title = submission_display_title(media)
    year = str(media.get("year") or metadata.get("year") or "")
    media_title = display_title if not year or f"({year})" in display_title else f"{display_title} ({year})"
    source_text = str(metadata.get("resourceType") or metadata.get("source") or "")
    values = {
        "title": escape(media_title),
        "mediaType": media_type_label(str(media.get("mediaType") or metadata.get("mediaType") or "unknown")),
        "tmdbMarker": tmdb_post_marker(str(media.get("mediaType") or metadata.get("mediaType") or "unknown"), media.get("tmdbId") or metadata.get("tmdbId")),
        "tmdbRating": render_rating(media.get("voteAverage"), str(media.get("tmdbUrl") or "")),
        "doubanRating": render_rating(media.get("doubanRating"), str(media.get("doubanUrl") or "")),
        "quality": escape(submission_resolution_label(str(metadata.get("quality") or ""), source_text) or "未识别"),
        "source": escape(compact_source(source_text) or "未识别"),
        "size": escape(str(metadata.get("size") or "未识别")),
        "mpName": escape(build_submission_resource_name(metadata, inspection.get("fileNames") or [], config)),
        "releaseGroup": escape(f"[{metadata.get('releaseGroup')}]" if metadata.get("releaseGroup") else ""),
        "videoCodec": escape(str(metadata.get("videoCodec") or "")),
        "audioCodec": escape(str(metadata.get("audioCodec") or "")),
        "effect": escape(compact_effect(str(metadata.get("effect") or "")) or ""),
        "webSource": escape(compact_web_source(str(metadata.get("webSource") or "")) or ""),
        "fps": escape(normalize_fps(str(metadata.get("fps") or "")) or ""),
        "shareName": escape(submission_share_name(draft, config)),
        "shareLink": render_share_link(draft, config),
        "shareUrl": escape(render_share_url_value(draft)),
        "routeChannel": escape(route_channel_label(draft, store)),
        "seasonEpisodeBlock": resource_block(draft, config),
        "resourceBlock": resource_block(draft, config),
        "overviewBlock": render_overview_block(str(media.get("overview") or "")),
        "tags": escape(build_tags_text(display_title, media.get("genres") or [])),
    }
    caption = template
    for key, value in values.items():
        caption = caption.replace(f"{{{key}}}", value)
    caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    database_block = database_note_rich_block(draft)
    if database_block:
        caption = f"{caption}\n\n{database_block}" if caption else database_block
    return caption


def build_submission_display_preview(config: Dict[str, Any], sample: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Render the display editor sample without network or persisted state."""
    current_config = config if isinstance(config, dict) else {}
    current_sample = sample if isinstance(sample, dict) else {}
    source = str(current_sample.get("resourceType") or current_sample.get("source") or "").strip()
    metadata = {
        "title": str(current_sample.get("title") or "示例媒体").strip() or "示例媒体",
        "year": str(current_sample.get("year") or "").strip(),
        "mediaType": str(current_sample.get("mediaType") or "movie").strip() or "movie",
        "quality": str(current_sample.get("quality") or "").strip(),
        "resourceType": source,
        "source": source,
        "webSource": str(current_sample.get("webSource") or "").strip(),
        "effect": str(current_sample.get("effect") or "").strip(),
        "fps": str(current_sample.get("fps") or "").strip(),
        "videoCodec": str(current_sample.get("videoCodec") or "").strip(),
        "audioCodec": str(current_sample.get("audioCodec") or "").strip(),
        "size": str(current_sample.get("size") or "").strip(),
        "releaseGroup": str(current_sample.get("releaseGroup") or "").strip(),
        "seasonEpisode": str(current_sample.get("seasonEpisode") or "").strip(),
    }
    media = {
        "title": metadata["title"],
        "year": metadata["year"],
        "mediaType": metadata["mediaType"],
        "overview": str(current_sample.get("overview") or "").strip(),
        "tmdbId": current_sample.get("tmdbId"),
        "tmdbUrl": str(current_sample.get("tmdbUrl") or "").strip(),
        "voteAverage": current_sample.get("tmdbRating"),
        "doubanRating": current_sample.get("doubanRating"),
        "doubanUrl": str(current_sample.get("doubanUrl") or "").strip(),
    }
    file_names = [
        str(value or "").strip()
        for value in current_sample.get("fileNames") or []
        if str(value or "").strip()
    ]
    share_url = str(current_sample.get("shareUrl") or "").strip()
    draft = {
        "ownerUserId": 0,
        "share": {"url": share_url, "cleanUrl": share_url},
        "inspection": {"fileNames": file_names},
        "metadata": metadata,
        "media": media,
    }
    caption = render_submission_caption(draft, current_config)
    resource_name = build_submission_resource_name(metadata, file_names, current_config)
    resolution = submission_resolution_label(metadata["quality"], source)
    source_label = configured_source_label(source, resolution, current_config) or compact_source(source) or source
    plain_text = re.sub(r"\n{3,}", "\n\n", unescape(
        re.sub(r"<[^>]+>", "", re.sub(r"</(?:blockquote|p)>|<br\s*/?>", "\n", caption, flags=re.I))
    )).strip()
    return {
        "caption": caption,
        "text": plain_text,
        "resourceName": resource_name,
        "sourceLabel": source_label,
        "shareLink": render_share_link(draft, current_config),
        "routeChannel": route_channel_label(draft, None),
        "resourceBlock": resource_block(draft, current_config),
        "overviewBlock": render_overview_block(media["overview"]),
    }


def strip_route_line(template: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in str(template or "").splitlines() if "{routeChannel}" not in line)).strip()


def select_submission_channel(
    store_or_config: Any,
    owner_user_id_or_draft: Any,
    draft: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a route from a legacy config or one isolated user config."""
    if draft is None:
        user_config = store_or_config if isinstance(store_or_config, dict) else {}
        current_draft = owner_user_id_or_draft if isinstance(owner_user_id_or_draft, dict) else {}
    else:
        store = store_or_config if isinstance(store_or_config, SessionStore) else None
        owner_user_id = safe_int(owner_user_id_or_draft)
        current_draft = draft
        route_owner_user_id = safe_int(current_draft.get("routeOwnerUserId")) or owner_user_id
        if not store or not store.has_user_channel_config(route_owner_user_id):
            return None
        user_config = store.read_user_channel_config(route_owner_user_id)
    channels = [channel for channel in user_config.get("channels") or [] if isinstance(channel, dict) and channel.get("enabled") is not False]
    if not channels:
        return None
    if len(channels) == 1:
        return channels[0]
    manual_id = str(current_draft.get("channelId") or "").strip()
    if manual_id:
        manual = find_channel(channels, manual_id)
        if manual:
            return manual
    routing = user_config.get("routing") if isinstance(user_config.get("routing"), dict) else {}
    metadata = current_draft.get("metadata") if isinstance(current_draft.get("metadata"), dict) else {}
    release_group = str(metadata.get("releaseGroup") or "").strip()
    if release_group:
        public_groups = {str(item or "").strip().lower() for item in routing.get("publicReleaseGroups") or []}
        if release_group.lower() not in public_groups:
            return find_channel(channels, routing.get("releaseGroupChannelId")) or find_channel_by_role(channels, "private") or find_default_channel(channels)
    if is_completed_media(current_draft):
        return find_channel(channels, routing.get("noReleaseGroupCompletedChannelId")) or find_channel_by_role(channels, "public_completed") or find_default_channel(channels)
    return find_channel(channels, routing.get("noReleaseGroupUpdatingChannelId")) or find_channel_by_role(channels, "public_updating") or find_default_channel(channels)


def submission_channel_candidates(store: SessionStore, user_id: int) -> List[Dict[str, Any]]:
    """Only expose channels the submitting Telegram user is entitled to use."""
    result: List[Dict[str, Any]] = []
    seen = set()
    if store.has_user_channel_config(user_id):
        own = store.read_user_channel_config(user_id)
        for channel in enabled_submission_channels(own.get("channels") or []):
            channel_id = str(channel.get("id") or "").strip()
            if channel_id:
                seen.add((int(user_id), channel_id))
                result.append({"ownerUserId": int(user_id), "channel": channel})
    for item in store.granted_submission_channels(user_id):
        owner = safe_int(item.get("ownerUserId"))
        channel = item.get("channel") if isinstance(item.get("channel"), dict) else {}
        key = (owner, str(channel.get("id") or "").strip())
        if owner > 0 and key[1] and key not in seen:
            seen.add(key)
            result.append({"ownerUserId": owner, "channel": channel})
    return result


def draft_channel_allowed(store: SessionStore, draft: Dict[str, Any], user_id: int) -> bool:
    channel = select_submission_channel(store, safe_int(draft.get("ownerUserId")), draft)
    route_owner = safe_int(draft.get("routeOwnerUserId")) or safe_int(draft.get("ownerUserId"))
    return bool(channel and store.channel_user_allowed(route_owner, str(channel.get("id") or ""), user_id))


def extract_submission_links(text: str) -> List[Dict[str, Any]]:
    source = str(text or "")
    links: List[Dict[str, Any]] = []
    seen = set()
    for match in WEB_SHARE_LINK_RE.finditer(source):
        raw = trim_link(match.group(0))
        if raw in seen:
            continue
        clean_url = normalize_web_share_url(raw)
        seen.add(raw)
        seen.add(clean_url)
        password = extract_share_password(clean_url) or extract_share_password(raw) or extract_share_password(source)
        links.append(
            {
                "url": raw,
                "cleanUrl": clean_url,
                "password": password or "",
                "provider": "123pan",
                "sourceText": source,
            }
        )
    for match in FASTLINK_RE.finditer(source):
        raw = trim_link(match.group(0))
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        context = fastlink_context(source, match.start(), raw)
        links.append(
            {
                "url": raw,
                "cleanUrl": raw,
                "provider": "123fastlink",
                "title": context.get("title") or "",
                "sourceText": context.get("sourceText") or raw,
            }
        )
    return links


def normalize_submission_link(link: Dict[str, Any], source_text: str = "") -> Dict[str, Any]:
    provider = str(link.get("provider") or "").strip() or ("123fastlink" if FASTLINK_RE.search(str(link.get("cleanUrl") or link.get("url") or "")) else "123pan")
    clean_url = trim_link(str(link.get("cleanUrl") or link.get("url") or ""))
    if provider != "123fastlink":
        clean_url = normalize_web_share_url(clean_url)
    return {
        **link,
        "provider": provider,
        "url": str(link.get("url") or clean_url),
        "cleanUrl": clean_url,
        "password": str(link.get("password") or extract_share_password(clean_url) or extract_share_password(source_text) or ""),
        "sourceText": str(link.get("sourceText") or source_text or clean_url),
        "documents": normalize_submission_documents(link.get("documents") or link.get("document")),
    }


def normalize_submission_documents(value: Any) -> List[Dict[str, Any]]:
    raw_documents = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    documents: List[Dict[str, Any]] = []
    for item in raw_documents:
        if not isinstance(item, dict):
            continue
        raw_content = item.get("content")
        if isinstance(raw_content, (dict, list)):
            content = json.dumps(raw_content, ensure_ascii=False, indent=2)
        else:
            content = str(raw_content or "")
        filename = safe_submission_document_filename(
            item.get("fileName")
            or item.get("filename")
            or item.get("name")
            or ("123FastLink_Export.123fastlink.json" if str(item.get("type") or "") == "fastlink_json" else "attachment.bin")
        )
        if not content or not filename:
            continue
        documents.append(
            {
                "type": str(item.get("type") or ""),
                "fileName": filename,
                "mimeType": str(item.get("mimeType") or item.get("mime_type") or "application/octet-stream"),
                "content": content,
                "caption": str(item.get("caption") or ""),
            }
        )
    return documents[:5]


def safe_submission_document_filename(value: Any) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or "")).strip()
    return name[:180] or "attachment.bin"


def unique_submission_links(links: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        clean_url = trim_link(str(link.get("cleanUrl") or link.get("url") or ""))
        key = share_media_cache_key(clean_url) if clean_url else ""
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({**link, "cleanUrl": clean_url})
    return result


def share_media_cache_key(share_url: str) -> str:
    value = str(share_url or "").strip()
    if not re.match(r"^https?://", value, re.I):
        return value.lower()
    try:
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        pwd = str((query.get("pwd") or [""])[0])
        suffix = f"?pwd={pwd}" if pwd else ""
        return f"{parsed.hostname or ''}{parsed.path}{suffix}".lower()
    except Exception:
        return value.lower()


def get_share_media_cache(store: SessionStore, share_url: str) -> Dict[str, Any]:
    key = share_media_cache_key(share_url)
    current = store.read_value(SUBMISSION_MEDIA_CACHE_KEY)
    cache = current if isinstance(current, dict) else {}
    item = cache.get(key)
    return item if isinstance(item, dict) else {}


def save_share_media_cache(store: SessionStore, share_url: str, media: Dict[str, Any], source: str = "auto") -> None:
    if not share_url or not isinstance(media, dict) or not media.get("tmdbId"):
        return
    key = share_media_cache_key(share_url)
    current = store.read_value(SUBMISSION_MEDIA_CACHE_KEY)
    cache = current if isinstance(current, dict) else {}
    cache[key] = {
        "key": key,
        "shareUrl": share_url,
        "media": media,
        "source": "manual" if source == "manual" else "auto",
        "updatedAt": utc_now_iso(),
    }
    store.write_value(SUBMISSION_MEDIA_CACHE_KEY, cache)


def should_use_cached_media(cached_media: Optional[Dict[str, Any]], metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(cached_media, dict) or not cached_media:
        return None
    wanted = int(metadata.get("tmdbId") or 0)
    cached_id = int(cached_media.get("tmdbId") or 0)
    if wanted and cached_id != wanted:
        return None
    return cached_media


async def inspect_submission_link(link: Dict[str, Any]) -> Dict[str, Any]:
    if str(link.get("provider") or "") == "123fastlink":
        return inspect_fastlink(link)
    return await inspect_web_share(link)


def inspect_fastlink(link: Dict[str, Any]) -> Dict[str, Any]:
    source = str(link.get("sourceText") or "")
    parsed = parse_fastlink(str(link.get("cleanUrl") or ""))
    file_names = extract_fastlink_context_files(source)
    title = str(link.get("title") or "").strip() or strip_fastlink_seed_ext(str(parsed.get("fileName") or "秒传文件"))
    return {
        "title": title,
        "fileNames": file_names or [str(parsed.get("fileName") or title)],
        "size": extract_fastlink_context_size(source) or (format_size(int(parsed.get("size") or 0)) if parsed.get("size") else ""),
        "rawText": source or f"{title}\n{link.get('cleanUrl') or ''}",
    }


async def inspect_web_share(link: Dict[str, Any]) -> Dict[str, Any]:
    share_key = get_share_key(str(link.get("cleanUrl") or ""))
    if share_key:
        try:
            names: List[str] = []
            total_size = 0
            title = ""
            visited: set[int] = set()

            async def visit(parent_file_id: int, path_prefix: str = "") -> int:
                nonlocal title, total_size
                if parent_file_id in visited or len(visited) > 500:
                    return 0
                visited.add(parent_file_id)

                items = await fetch_share_items(link, share_key, parent_file_id)
                folder_size = 0
                folders: List[Dict[str, Any]] = []
                for item in items:
                    name = str(item.get("FileName") or item.get("filename") or item.get("name") or "").strip()
                    item_type = int(item.get("Type") or item.get("type") or 0)
                    if not title and name:
                        title = name
                    if name:
                        full_name = f"{path_prefix}{name}"
                        names.append(full_name)
                    if item_type == 1:
                        folders.append(item)
                        continue
                    size = int(item.get("Size") or item.get("BaseSize") or item.get("size") or 0)
                    total_size += size
                    folder_size += size

                for folder in folders:
                    folder_id = int(folder.get("FileId") or folder.get("fileId") or folder.get("id") or 0)
                    before = total_size
                    fallback_size = int(folder.get("Size") or folder.get("BaseSize") or folder.get("size") or 0)
                    try:
                        folder_size += await visit(folder_id, f"{path_prefix}{str(folder.get('FileName') or folder.get('filename') or folder.get('name') or '')}/")
                    except Exception:
                        total_size += fallback_size
                        folder_size += fallback_size
                        continue
                    if total_size == before and fallback_size > 0:
                        total_size += fallback_size
                        folder_size += fallback_size
                return folder_size

            await visit(0, "")
            if names:
                names = deduplicate_file_names(names)
                return {
                    "title": title,
                    "fileNames": names,
                    "size": format_size(total_size) if total_size > 0 else "",
                    "rawText": "\n".join(names),
                }
        except Exception:
            pass
    html_inspection = await inspect_share_html(link)
    if html_inspection.get("fileNames") or html_inspection.get("title"):
        return html_inspection
    source = str(link.get("sourceText") or "")
    return {
        "title": str(link.get("title") or infer_title_from_text(source) or "123 分享").strip(),
        "fileNames": extract_likely_file_names(source),
        "size": "",
        "rawText": source,
    }


async def inspect_share_html(link: Dict[str, Any]) -> Dict[str, Any]:
    clean_url = str(link.get("cleanUrl") or "")
    if not clean_url.startswith("http"):
        return {"title": "", "fileNames": [], "size": "", "rawText": ""}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36", "accept": "text/html,application/xhtml+xml"}) as client:
            response = await client.get(clean_url)
        if response.status_code >= 400:
            return {"title": "", "fileNames": [], "size": "", "rawText": ""}
        html = response.text
        title = clean_share_page_title(first_present([extract_meta(html, "og:title"), extract_meta(html, "twitter:title"), extract_html_title(html)]))
        raw_text = re.sub(r"\s+", " ", strip_tags(unescape(html))).strip()
        return {
            "title": title or "",
            "fileNames": extract_likely_file_names(html),
            "size": "",
            "rawText": raw_text,
        }
    except Exception:
        return {"title": "", "fileNames": [], "size": "", "rawText": ""}


async def fetch_share_items(link: Dict[str, Any], share_key: str, parent_file_id: int) -> List[Dict[str, Any]]:
    parsed = urlparse(str(link.get("cleanUrl") or ""))
    base = f"{parsed.scheme}://{parsed.netloc}"
    items: List[Dict[str, Any]] = []
    page = 1
    next_token = "0"
    async with httpx.AsyncClient(timeout=12.0, headers={"user-agent": "Mozilla/5.0", "accept": "application/json", "referer": str(link.get("cleanUrl") or "")}) as client:
        while True:
            params = {
                "limit": "100",
                "next": next_token,
                "orderBy": "file_name",
                "orderDirection": "asc",
                "shareKey": share_key,
                "ParentFileId": str(parent_file_id),
                "Page": str(page),
                "event": "homeListFile",
                "operateType": "1",
                "SharePwd": str(link.get("password") or ""),
            }
            response = await client.get(f"{base}/b/api/share/get", params=params)
            if response.status_code >= 400:
                raise ValueError(f"123 分享接口 {response.status_code}")
            data = response.json()
            if int(data.get("code") or 0) != 0:
                raise ValueError(str(data.get("message") or "123 分享接口失败"))
            payload_raw = data.get("data")
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            page_items = share_payload_items(payload_raw)
            items.extend(page_items)

            response_next = str(first_present([payload.get("Next"), payload.get("next"), payload.get("LastFileId"), payload.get("lastFileId")]) or "").strip()
            if response_next in {"0", ""}:
                response_next = ""
            last_file_id = str(first_present([(page_items[-1] if page_items else {}).get("FileId"), (page_items[-1] if page_items else {}).get("fileId"), (page_items[-1] if page_items else {}).get("id")]) or "")
            next_token = response_next or (last_file_id if len(page_items) >= 100 and last_file_id else "-1")
            if next_token == "-1" or len(page_items) < 100:
                break
            page += 1
    return items


def share_payload_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("InfoList", "infoList", "fileList", "FileList", "list", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def format_size(size: int) -> str:
    current = float(size or 0)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while current >= 1024 and index < len(units) - 1:
        current /= 1024
        index += 1
    if index == 0:
        return f"{int(current)} B"
    return f"{current:.2f}".rstrip("0").rstrip(".") + f" {units[index]}"


def extract_title_year(value: str) -> tuple[str, str]:
    patterns = [
        r"(?P<title>[\u4e00-\u9fffA-Za-z0-9][^\n\r{}【】\[\]()（）]{0,100}?)\s*[\(（](?P<year>19\d{2}|20\d{2})[\)）]",
        r"(?P<title>[\u4e00-\u9fffA-Za-z0-9][^\n\r{}【】\[\]()（）]{1,100}?)\s+(?P<year>19\d{2}|20\d{2})(?=\s+(?:S\d{1,3}|EP?\d{1,5}|Complete|8K|6K|5K|4K|2K|UHD|QHD|FHD|HD|4320p|2160p|1440p|1080p|720p|WEB[- .]?DL|WEBRip|Blu[- .]?Ray|REMUX|HDTV|HEVC|AVC|H[. ]?26[45])|\s*$)",
        r"(?P<title>[\u4e00-\u9fffA-Za-z0-9][^\n\r{}【】\[\]()（）]{1,100}?)\s*[\s._-]+(?P<year>19\d{2}|20\d{2})(?=$|[\s._-]+(?:S\d{1,3}|EP?\d{1,5}|Complete|8K|6K|5K|4K|2K|UHD|QHD|FHD|HD|4320p|2160p|1440p|1080p|720p|WEB[- .]?DL|WEBRip|Blu[- .]?Ray|REMUX|HDTV|HEVC|AVC|H[. ]?26[45]))",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return clean_title(match.group("title")), match.group("year")
    return "", ""


def infer_submission_media_type(value: str) -> str:
    text = str(value or "")
    if re.search(r"\bS\d{1,3}(?:E\d{1,5})?\b", text, re.I) or re.search(r"第\s*\d{1,5}\s*[季集]", text):
        return "tv"
    if re.search(r"(?:电视剧|剧集|连续剧|番剧|📺)", text):
        return "tv"
    if re.search(r"(?:电影|影片|🎬)", text):
        return "movie"
    return "movie" if extract_year(text) and VIDEO_EXT_RE.search(text) else "unknown"


def infer_title_from_text(text: str) -> str:
    for line in str(text or "").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("http") or "123FLCPV2$" in clean or "123pan" in clean:
            continue
        return clean_title(re.sub(r"\b(?:S\d{1,3}E\d{1,5}|2160p|1080p|720p|WEB[- .]?DL|WEBRip|Blu[- .]?Ray|REMUX|HDTV|HEVC|AVC|H[. ]?26[45])\b.*$", "", clean, flags=re.I))
    return ""


def infer_title_from_file_names(file_names: Iterable[Any]) -> str:
    for name in file_names:
        text = str(name or "")
        if not VIDEO_EXT_RE.search(text):
            continue
        stem = re.sub(r"\.[^.]+$", "", text)
        stem = TMDB_ID_RE.sub(" ", stem)
        stem = re.sub(r"[\[\]【】《》]", " ", stem).replace(".", " ").replace("_", " ")
        match = re.search(r"\b(?:19|20)\d{2}|S\d{1,3}(?:E\d{1,5})?|EP?\d{1,5}|2160p|1080p|720p|WEB[- ]?DL|WEBRip|Blu[- ]?Ray|REMUX|HDTV|HEVC|AVC|H[. ]?26[45]", stem, re.I)
        if match and match.start() > 0:
            stem = stem[: match.start()]
        title = clean_title(stem)
        if title and not is_weak_recognition_title(title):
            return title
    return ""


def is_weak_recognition_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", re.sub(r"[\[\]【】()（）]", " ", str(title or ""))).strip()
    return not normalized or re.fullmatch(r"\d{1,4}", normalized) is not None or re.fullmatch(r"(?:s(?:eason)?\s*)?\d{1,2}", normalized, re.I) is not None or normalized.lower() in {"season", "folder", "dir", "new folder", "文件夹", "新建文件夹"}


def strip_share_artifacts(text: str) -> str:
    return PWD_RE.sub(" ", WEB_SHARE_LINK_RE.sub(" ", str(text or "")))


def clean_title(title: str) -> str:
    text = re.sub(r"[🎬🎥📺]", "", str(title or ""))
    text = re.sub(r"[《》]", "", text)
    text = text.replace(".", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" ：:-")
    return text


def extract_tmdb_id(value: str) -> Optional[int]:
    match = TMDB_ID_RE.search(str(value or ""))
    return int(match.group("id")) if match else None


def extract_year(value: str) -> str:
    match = YEAR_RE.search(str(value or ""))
    return match.group(1) if match else ""


def first_season_episode(value: str) -> str:
    text = str(value or "")
    match = re.search(r"\bS(?P<season>\d{1,3})\s*E(?P<episode>\d{1,5})(?:\s*[-~]\s*E?(?P<last>\d{1,5}))?\b", text, re.I)
    if match:
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        last = int(match.group("last")) if match.group("last") else None
        return format_season_episode(season, episode, last)
    match = re.search(r"(?:第\s*)?(?P<episode>\d{1,5})\s*集", text)
    if match:
        return format_season_episode(1, int(match.group("episode")), None)
    match = re.search(r"\b(?:EP|E)(?P<episode>\d{1,5})\b", text, re.I)
    if match:
        return format_season_episode(1, int(match.group("episode")), None)
    return ""


def summarize_season_episodes(file_names: Iterable[Any]) -> str:
    episodes: List[tuple[int, int]] = []
    for name in file_names:
        match = re.search(r"\bS(?P<season>\d{1,3})\s*E(?P<episode>\d{1,5})\b", str(name or ""), re.I)
        if match:
            episodes.append((int(match.group("season")), int(match.group("episode"))))
    if not episodes:
        return ""
    seasons = sorted({season for season, _episode in episodes})
    if len(seasons) == 1:
        values = sorted({episode for season, episode in episodes if season == seasons[0]})
        if len(values) > 1:
            return format_season_episode(seasons[0], values[0], values[-1])
        return format_season_episode(seasons[0], values[0], None)
    return f"S{seasons[0]:02d}-S{seasons[-1]:02d}"


def format_season_episode(season: int, episode: int, last: Optional[int]) -> str:
    base = f"S{season:02d}E{episode:02d}"
    return f"{base}-E{last:02d}" if last and last != episode else base


def collect_quality(files: Iterable[Any]) -> str:
    return join_variants(extract_quality(str(file or "")) for file in files)


def extract_quality(value: str) -> str:
    for pattern, label in (
        (r"\b(?:4320p|8K)\b", "8K"),
        (r"\b(?:2160p|4K|UHD|Ultra[\s._-]?HD)\b", "2160p"),
        (r"\b(?:1440p|2K|QHD)\b", "2K"),
        (r"\b(?:1080p|1080i|FHD)\b", "1080P"),
        (r"\b720p\b", "720P"),
        (r"\b480p\b", "480P"),
    ):
        if re.search(pattern, value, re.I):
            return label
    return ""


def collect_source(files: Iterable[Any]) -> str:
    return normalize_source_variants(join_variants(extract_source(str(file or "")) for file in files))


def extract_source(value: str) -> str:
    for pattern, label in (
        (r"\bUHD[\s._-]*Blu[\s._-]*Ray[\s._-]*REMUX\b", "UHD BluRay Remux"),
        (r"\bBlu[\s._-]*Ray[\s._-]*REMUX\b", "BluRay Remux"),
        (r"\bREMUX\b", "Remux"),
        (r"\bWEB[\s._-]*DL\b", "WEB-DL"),
        (r"\bWEB[\s._-]*Rip\b", "WEBRip"),
        (r"\bUHD[\s._-]*Blu[\s._-]*Ray\b", "UHD BluRay"),
        (r"\bBlu[\s._-]*Ray\b", "BluRay"),
        (r"\bUHDTV\b", "UHDTV"),
        (r"\bHDTV\b", "HDTV"),
    ):
        if re.search(pattern, value, re.I):
            return label
    return ""


def collect_effect(files: Iterable[Any]) -> str:
    return join_variants(extract_effect(str(file or "")) for file in files)


def extract_effect(value: str) -> str:
    effects = []
    for pattern, label in (
        (r"\b(?:Dolby\s?Vision|DoVi|DV)\b", "DV"),
        (r"\bHDR10\+\b", "HDR10"),
        (r"\bHDR10\b", "HDR10"),
        (r"\bHDR[\s.]?Vivid\b", "HDR.Vivid"),
        (r"\bHLG\b", "HLG"),
        (r"\bSDR\b", "SDR"),
        (r"\bHDR\b", "HDR"),
    ):
        if re.search(pattern, value, re.I) and label not in effects:
            effects.append(label)
    return " ".join(effects)


def collect_video_codec(files: Iterable[Any]) -> str:
    return join_variants(extract_video_codec(str(file or "")) for file in files)


def extract_video_codec(value: str) -> str:
    for pattern, label in (
        (r"\b(?:H[ ._-]?265|HEVC|x265)\b", "HEVC"),
        (r"\b(?:H[ ._-]?264|AVC|x264)\b", "AVC"),
        (r"\bAV1\b", "AV1"),
    ):
        if re.search(pattern, value, re.I):
            return label
    return ""


def collect_audio_codec(files: Iterable[Any]) -> str:
    return join_variants(extract_audio_codec(str(file or "")) for file in files)


def extract_audio_codec(value: str) -> str:
    family = (
        r"TrueHD|DDP|DD\+?|DTS(?:[- .]?(?:HD(?:[- .]?(?:MA|HRA))?|X))?"
        r"|EAC3|AC3|AAC|FLAC|LPCM|PCM|Opus|Atmos"
    )
    match = re.search(
        rf"(?<![A-Za-z0-9])(?P<family>{family})"
        r"(?P<details>(?:[.\s_-]*(?:\d\.\d|(?:Dolby[.\s_-]*)?Atmos|JOC))*)"
        r"(?![A-Za-z0-9])",
        str(value or ""),
        re.I,
    )
    if not match:
        return ""
    key = re.sub(r"[^A-Z0-9+]", "", match.group("family").upper())
    labels = {
        "TRUEHD": "TrueHD", "DDP": "DDP", "DD": "DD", "DD+": "DD+",
        "DTS": "DTS", "DTSHD": "DTS-HD", "DTSHDMA": "DTS-HD.MA",
        "DTSHDHRA": "DTS-HD.HRA", "DTSX": "DTS-X", "EAC3": "EAC3",
        "AC3": "AC3", "AAC": "AAC", "FLAC": "FLAC", "LPCM": "LPCM",
        "PCM": "PCM", "OPUS": "Opus", "ATMOS": "Atmos",
    }
    codec = labels.get(key, match.group("family"))
    details = match.group("details") or ""
    channel = re.search(r"\d\.\d", details)
    if channel:
        separator = "." if codec.startswith("DTS-") or codec in {"EAC3", "AC3"} else ""
        codec += separator + channel.group(0)
    if re.search(r"Atmos", details, re.I):
        codec += " Atmos"
    elif re.search(r"JOC", details, re.I):
        codec += " JOC"
    return codec


def collect_fps(files: Iterable[Any]) -> str:
    return join_variants(extract_fps(str(file or "")) for file in files)


def extract_fps(value: str) -> str:
    text = re.sub(r"(?<![A-Za-z0-9])[HX][ ._-]?26[45](?![A-Za-z0-9])", " ", str(value or ""), flags=re.I)
    for match in re.finditer(r"(?<![A-Za-z0-9])(\d{2,3}(?:\.\d{1,3})?)\s*fps\b", text, re.I):
        rate = float(match.group(1))
        if 10 <= rate <= 240:
            return f"{match.group(1)}FPS"
    return ""


def collect_bit_depth(files: Iterable[Any]) -> str:
    return join_variants(extract_bit_depth(str(file or "")) for file in files)


def extract_bit_depth(value: str) -> str:
    match = re.search(r"\b(8|10|12)[\s.-]?bit\b", value, re.I)
    return f"{match.group(1)}bit" if match else ""


def collect_web_source(files: Iterable[Any], config: Optional[Dict[str, Any]] = None) -> str:
    return join_variants(extract_web_source(str(file or ""), config) for file in files)


def collect_itunes_source(files: Iterable[Any]) -> str:
    for file in files:
        value = extract_itunes_source(str(file or ""))
        if value:
            return value
    return ""


def _get_web_source_aliases(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    rule_config = (config or {}).get("ruleConfig") if isinstance((config or {}).get("ruleConfig"), dict) else {}
    web_sources = rule_config.get("webSource") if isinstance(rule_config.get("webSource"), list) else []
    result = []
    for item in web_sources:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        value = str(item.get("value") or "").strip()
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if value:
            result.append({"value": value, "aliases": [value, *[str(a or "").strip() for a in aliases if str(a or "").strip()]]})
    return result


def extract_web_source(value: str, config: Optional[Dict[str, Any]] = None) -> str:
    text = str(value or "")
    aliases = _get_web_source_aliases(config)
    if aliases:
        for item in sorted(aliases, key=lambda x: -len(x.get("value", ""))):
            for alias in item.get("aliases", []):
                if re.search(rf"(?:^|[\s._\-\[\]()]){re.escape(alias)}(?=$|[\s._\-\[\]()])", text, re.I):
                    return item["value"]
        return ""
    fallback_aliases = {"NETFLIX": "NF", "AMAZON": "AMZN", "PRIME": "AMZN", "APPLETV": "ATVP", "DISNEY": "DSNP", "BILIBILI": "Bilibili"}
    for token in ("AMZN", "NF", "ATVP", "DSNP", "Hulu", "HMAX", "MAX", "CR", "IQ", "Bilibili", "YOUKU", "MGTV"):
        if re.search(rf"(?:^|[\s._\-\[\]()]){re.escape(token)}(?=$|[\s._\-\[\]()])", text, re.I):
            return fallback_aliases.get(token.upper(), token)
    for raw, label in fallback_aliases.items():
        if re.search(rf"(?:^|[\s._\-\[\]()]){raw}(?=$|[\s._\-\[\]()])", text, re.I):
            return label
    return ""


def extract_itunes_source(value: str) -> str:
    """Extract iTunes (iT) as a separate source indicator."""
    if re.search(r"(?:^|[\s._\-\[\]()])iT(?=$|[\s._\-\[\]()])", value, re.I):
        return "iT"
    return ""


def collect_release_group(files: Iterable[Any], config: Optional[Dict[str, Any]] = None) -> str:
    for file_name in submission_video_file_names(files):
        value = extract_release_group(file_name, config)
        if value:
            return value
    return ""


def submission_video_file_names(files: Iterable[Any]) -> List[str]:
    names: List[str] = []
    for value in files:
        file_name = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if VIDEO_EXT_RE.search(file_name):
            names.append(file_name)
    return names


_KNOWN_NON_GROUP_TERMS = {
    # Video codecs
    "HEVC", "H265", "H264", "AVC", "AV1", "X264", "X265", "VP9", "MPEG", "MPEG2",
    # Audio codecs
    "AAC", "AC3", "DDP", "DDP5", "DD", "DTS", "FLAC", "TRUEHD", "ATMOS", "EAC3", "OPUS", "MP3", "LPCM",
    # HDR / video formats
    "DV", "HDR", "HDR10", "HDR10+", "HLG", "SDR", "DOVI",
    # Resolution / frame rate
    "P", "I", "FPS",
    # Source / quality
    "WEB", "DL", "WEBDL", "WEBRIP", "REMUX", "BLURAY", "HDTV", "BDRIP", "HDRIP",
    "DVDRIP", "DVDSCR", "DVD", "BDR", "UHD", "PQ",
    # Streaming services
    "NF", "AMZN", "DSNP", "ATVP", "HULU", "HMAX", "MAX", "CR", "IQ", "YOUKU", "MGTV", "BILIBILI",
    # Other
    "HQ", "PROPER", "REPACK", "INTERNAL", "LIMITED",
}


def _is_known_media_term(value: str) -> bool:
    """Check if value is a known technical media term (not a release group name)."""
    upper = str(value or "").strip().upper()
    if not upper:
        return True
    compact = re.sub(r"[\s._+-]+", "", upper)
    if upper in _KNOWN_NON_GROUP_TERMS or compact in _KNOWN_NON_GROUP_TERMS:
        return True
    # Match patterns like DDP5.1, AAC2.0, DD+5.1, etc.
    if re.match(r"^(?:AAC|DDP?|DD\+?|AC3|DTS|FLAC|TRUEHD)\d*\.?\d*$", upper):
        return True
    if re.fullmatch(r"S\d{1,3}(?:\s*[-~]\s*S\d{1,3})?", upper):
        return True
    if re.fullmatch(r"S\d{1,3}(?:E|EP)\d{1,5}(?:(?:E|EP)\d{1,5}|\s*[-~]\s*(?:S\d{1,3})?(?:E|EP)?\d{1,5})?", upper):
        return True
    # Match patterns like 2160p, 1080i, 50fps, etc.
    if re.match(r"^\d{3,4}[PI]$", upper):
        return True
    if re.match(r"^\d{2,3}FPS$", upper):
        return True
    return False


def extract_release_group(value: str, config: Optional[Dict[str, Any]] = None) -> str:
    stem = strip_known_extension(str(value or ""))
    bracket = re.search(r"\[(?P<group>[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9._@-]{1,40})\]\s*$", stem)
    if bracket and is_release_group_suffix(bracket.group("group")):
        return normalize_release_group(bracket.group("group"))
    configured_bracket = re.search(r"\[(?P<group>[^\]\r\n]{2,80})\]\s*$", stem)
    configured = normalize_configured_release_group(configured_bracket.group("group"), config) if configured_bracket else ""
    if configured:
        return configured

    dash_index = stem.rfind("-")
    if dash_index >= 0:
        suffix = stem[dash_index + 1 :].strip()
        if is_dash_release_group_suffix(suffix):
            return normalize_release_group(suffix)
        cfg_dash = normalize_configured_release_group(suffix, config)
        if cfg_dash:
            return cfg_dash

    # Fallback: last dot-separated segment (e.g. "...Atmos.HiveWeb" -> "HiveWeb")
    dot_index = stem.rfind(".")
    if dot_index >= 0:
        last_seg = stem[dot_index + 1:].strip()
        if last_seg and is_release_group_suffix(last_seg) and not _is_known_media_term(last_seg):
            return normalize_release_group(last_seg)

    # Fallback: last space-separated segment (e.g. "Season 1 ... HiveWeb" -> "HiveWeb")
    space_index = stem.rfind(" ")
    if space_index >= 0:
        last_seg = stem[space_index + 1:].strip()
        if last_seg and is_release_group_suffix(last_seg) and not _is_known_media_term(last_seg):
            return normalize_release_group(last_seg)

    return ""


def strip_known_extension(value: str) -> str:
    return re.sub(r"\.(?:mkv|mp4|avi|mov|wmv|flv|webm|m4v|mpeg|mpg|3gp|ts|m2ts|mts|ass|srt|ssa|zip|rar|7z)$", "", str(value or ""), flags=re.I)


def normalize_release_group(value: str) -> str:
    return strip_known_extension(str(value or "")).strip(". ")


def normalize_configured_release_group(value: str, config: Optional[Dict[str, Any]] = None) -> str:
    key = release_group_config_key(value)
    if not key or not isinstance(config, dict):
        return ""
    rule_config = config.get("ruleConfig") if isinstance(config.get("ruleConfig"), dict) else {}
    recognition = rule_config.get("recognition") if isinstance(rule_config.get("recognition"), dict) else {}
    for group in recognition.get("releaseGroups") or []:
        normalized = re.sub(r"\s+", " ", str(group or "")).strip()
        if normalized and release_group_config_key(normalized) == key:
            return normalized
    return ""


def release_group_config_key(value: Any) -> str:
    return re.sub(r"[\s._]+", " ", str(value or "")).strip().lower()


def is_release_group_suffix(value: str) -> bool:
    text = str(value or "")
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9._@-]{1,40}", text):
        return False
    return not _is_known_media_term(text) and not re.fullmatch(
        r"(?:DL|WEB|WEBRIP|REMUX|BLURAY|HDTV|BDRIP|HDRIP|NF|AMZN|DSNP|ATVP|VIU|HULU|MAX|HMAX|AAC\d?(?:\.\d)?|DDP\d?(?:\.\d)?|DD\d?(?:\.\d)?|AC3|FLAC|TRUEHD|DTS|AVC|HEVC|AV1|H264|H265|X264|X265|bit|bits?|\d+bit|\d+bits?|\d{3,4}p|\d{3,4}i|S\d{1,2}E\d{1,4}|19\d{2}|20\d{2})",
        text,
        re.I,
    )


def is_dash_release_group_suffix(value: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9@-]{1,40}", str(value or ""))) and is_release_group_suffix(value)


def join_variants(values: Iterable[str]) -> str:
    out: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.upper()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return "/".join(out)


def _detect_multi_version_note(
    file_names: List[str],
    config: Optional[Dict[str, Any]] = None,
    media: Optional[Dict[str, Any]] = None,
) -> str:
    """Detect multi-version TV season folders or multi-version movies from file names and generate a note.

    Returns empty string for single-version content (let resource_block handle it).
    """
    cfg = config or DEFAULT_SUBMISSION_CONFIG
    # --- Multi-version TV: detect Season N subfolders ---
    season_files: Dict[str, List[str]] = {}
    for path in file_names:
        parts = path.replace("\\", "/").split("/")
        for part in parts[:-1]:
            match = SEASON_FOLDER_NAME_RE.match(part.strip())
            if match:
                season_key = part.strip()
                season_files.setdefault(season_key, []).append(parts[-1])
                break
    if season_files:
        # Only consider season folders that have version info appended
        # (indicating they were created by the in-place organization feature).
        # Plain "Season N" folders from regular shares must not trigger multi-version notes.
        season_files = {
            name: files for name, files in season_files.items()
            if (SEASON_FOLDER_NAME_RE.match(name).group(2) or "").strip()
        }
        if season_files:
            version_groups_by_season: Dict[int, Dict[str, Dict[str, Any]]] = {}
            for season_name, files in season_files.items():
                season_match = SEASON_FOLDER_NAME_RE.match(season_name)
                if not season_match:
                    continue
                season_number = int(season_match.group(1))
                groups = version_groups_by_season.setdefault(season_number, {})
                for file_name in files:
                    fields = _submission_tv_version_fields(season_name, file_name, cfg)
                    key = version_semantic_key(fields, cfg)
                    group = groups.setdefault(key, {"fields": fields, "paths": []})
                    group["paths"].append(file_name)
            multi_version_seasons = {
                season_number for season_number, version_groups in version_groups_by_season.items() if len(version_groups) >= 2
            }
            if not multi_version_seasons:
                return ""
            notes: List[str] = []
            for season_number, version_groups in version_groups_by_season.items():
                if season_number not in multi_version_seasons:
                    continue
                season_label = f"S{season_number:02d}"
                for group in version_groups.values():
                    fields = normalize_version_fields(group["fields"], cfg)
                    group_paths = group["paths"]
                    is_completed = tmdb_season_files_complete(media or {}, season_number, group_paths)
                    group_episodes = submission_video_episodes(group_paths).get(season_number, set())
                    max_episode = max(group_episodes, default=0)
                    resolution = submission_resolution_label(fields["videoFormat"], fields["resourceType"])
                    if resolution:
                        resolution = re.sub(r'(\d)[Pp]$', r'\1p', resolution)
                    quality = re.sub(r'(\d)[Pp]$', r'\1p', fields["videoFormat"]) if fields["videoFormat"] else ""
                    source_label = configured_source_label(fields["resourceType"], resolution, cfg)
                    if source_label:
                        source_part = source_label
                    else:
                        parts = [resolution or quality, compact_web_source(fields["mediaSource"]), compact_source(fields["resourceType"])]
                        source_part = " ".join(p for p in parts if p)
                    version_parts = [source_part]
                    for field in ("highQuality", "dolbyVision"):
                        if fields[field]:
                            version_parts.append(fields[field])
                    ce = compact_effect(fields["effect"])
                    if ce:
                        version_parts.append(ce)
                    for field in ("dynamicRange", "frameRate", "colorDepth", "originalEdition", "videoCodec", "audioCodec"):
                        if fields[field]:
                            version_parts.append(fields[field])
                    if fields["releaseGroup"]:
                        version_parts.append(f"[{fields['releaseGroup']}]")
                    version_info = " ".join(version_parts)
                    status = ""
                    if is_completed:
                        status = "完结"
                    elif max_episode > 0 and group_episodes == set(range(1, max_episode + 1)):
                        status = f"更新至 E{max_episode:02d}"
                    elif group_episodes:
                        status = f"含 {format_episode_ranges(group_episodes)}"
                    prefix = f"{season_label} " if season_label else ""
                    if version_info and status:
                        notes.append(f"{prefix}{version_info} {status}")
                    elif version_info:
                        notes.append(f"{prefix}{version_info}")
                    elif status:
                        notes.append(f"{prefix}{status}".strip())
            if notes:
                return "\n".join(notes)

    # --- Flat-file TV (no season folders): disabled ---
    # Flat TV episodes without season folders cannot be from in-place organization,
    # so multi-version notes must not be generated for them.
    _tv_episode_re = re.compile(r"\bS(\d{1,3})\s*(?:E|EP)\d{1,5}", re.I)
    for _path in file_names:
        if _tv_episode_re.search(_path):
            return ""

    # --- Multi-version movie: group by the complete per-file signature ---
    movie_files = [path for path in deduplicate_file_names(file_names) if VIDEO_EXT_RE.search(path)]
    if len(movie_files) < 2:
        return ""

    version_groups: Dict[str, Dict[str, str]] = {}
    for path in movie_files:
        fields = _submission_movie_version_fields(path, cfg)
        key = "|".join(fields.values()).upper()
        version_groups.setdefault(key, fields)
    if len(version_groups) < 2:
        return ""
    notes = []
    for fields in version_groups.values():
        resolution = submission_resolution_label(fields["quality"], fields["source"])
        source_label = configured_source_label(fields["source"], resolution, cfg)
        if source_label:
            source_part = source_label
        else:
            parts = [resolution or fields["quality"], compact_web_source(fields["webSource"]), compact_source(fields["source"])]
            source_part = " ".join(p for p in parts if p)
        version_parts = [source_part]
        if fields["itunes"]:
            version_parts.append(fields["itunes"])
        if fields["highQuality"]:
            version_parts.append(fields["highQuality"])
        if fields["edr"]:
            version_parts.append(fields["edr"])
        ce = compact_effect(fields["effect"])
        if ce:
            version_parts.append(ce)
        fps_normalized = normalize_fps(fields["fps"])
        if fps_normalized:
            version_parts.append(fps_normalized)
        if fields["bitDepth"]:
            version_parts.append(fields["bitDepth"])
        if fields["videoCodec"]:
            version_parts.append(fields["videoCodec"])
        if fields["audioCodec"]:
            version_parts.append(fields["audioCodec"])
        if fields["releaseGroup"]:
            version_parts.append(f"[{fields['releaseGroup']}]")
        notes.append(" ".join(version_parts))
    return "\n".join(notes)


def _submission_movie_version_fields(path: str, config: Dict[str, Any]) -> Dict[str, str]:
    return {
        "quality": extract_quality(path),
        "source": extract_source(path),
        "webSource": extract_web_source(path, config),
        "itunes": extract_itunes_source(path),
        "highQuality": "HQ" if re.search(r"\bHQ\b", path, re.I) else "",
        "edr": "EDR" if re.search(r"\bEDR\b", path, re.I) else "",
        "effect": extract_effect(path),
        "fps": extract_fps(path),
        "bitDepth": extract_bit_depth(path),
        "videoCodec": extract_video_codec(path),
        "audioCodec": extract_audio_codec(path),
        "releaseGroup": extract_release_group(path, config),
    }


def _submission_tv_version_key(season_name: str, file_name: str, config: Dict[str, Any]) -> str:
    return version_semantic_key(_submission_tv_version_fields(season_name, file_name, config), config)


def _submission_tv_version_fields(season_name: str, file_name: str, config: Dict[str, Any]) -> Dict[str, str]:
    source = f"{season_name} {file_name}"
    effect = extract_effect(season_name) or extract_effect(file_name)
    return {
        "videoFormat": extract_quality(season_name) or extract_quality(file_name),
        "highQuality": "HQ" if re.search(r"\bHQ\b", source, re.I) else "",
        "dolbyVision": "DV" if re.search(r"\b(?:DV|DoVi|Dolby[ ._-]?Vision)\b", source, re.I) else "",
        "mediaSource": extract_web_source(season_name, config) or extract_web_source(file_name, config),
        "resourceType": extract_source(season_name) or extract_source(file_name),
        "effect": effect,
        "dynamicRange": "EDR" if re.search(r"\bEDR\b", source, re.I) else "",
        "frameRate": extract_fps(season_name) or extract_fps(file_name),
        "colorDepth": extract_bit_depth(season_name) or extract_bit_depth(file_name),
        "originalEdition": extract_version_alias(source, config, "edition"),
        "videoCodec": extract_video_codec(season_name) or extract_video_codec(file_name),
        "audioCodec": extract_audio_codec(season_name) or extract_audio_codec(file_name),
        "releaseGroup": extract_release_group(season_name, config) or extract_release_group(file_name, config),
    }


def build_submission_resource_name(metadata: Dict[str, Any], file_names: Iterable[Any], config: Dict[str, Any]) -> str:
    source_text = str(metadata.get("resourceType") or metadata.get("source") or "")
    resolution = submission_resolution_label(str(metadata.get("quality") or ""), source_text)
    configured_source = configured_source_label(source_text, resolution, config)
    details = [
        compact_season_episode(str(metadata.get("seasonEpisode") or "")),
        None if configured_source else resolution,
        metadata.get("edr"),
        configured_source or compact_source(source_text),
        compact_web_source(str(metadata.get("webSource") or "")),
        metadata.get("itunes"),
        metadata.get("highQuality"),
        compact_effect(str(metadata.get("effect") or "")),
        normalize_fps(str(metadata.get("fps") or "")),
        metadata.get("bitDepth"),
        metadata.get("resourceTerm"),
        metadata.get("edition"),
        metadata.get("videoCodec"),
        metadata.get("audioCodec"),
        metadata.get("part"),
    ]
    unique: List[str] = []
    seen = set()
    for item in details:
        clean = re.sub(r"\s+", " ", str(item or "")).strip()
        key = clean.upper()
        if clean and key not in seen:
            seen.add(key)
            unique.append(clean)
    if metadata.get("releaseGroup"):
        unique.append(f"[{metadata['releaseGroup']}]")
    return " ".join(unique).strip()


def configured_source_label(source: str, resolution: Optional[str], config: Dict[str, Any]) -> Optional[str]:
    rules = (((config.get("ruleConfig") or {}).get("display") or {}).get("sourceLabels") or [])
    normalized = compact_source(source) or source
    for rule in sorted([rule for rule in rules if isinstance(rule, dict) and rule.get("enabled") is not False], key=lambda item: int(item.get("order") or 0), reverse=True):
        wanted = source_label_key(str(rule.get("source") or ""))
        if wanted and wanted in {source_label_key(source), source_label_key(normalized)}:
            template = str(rule.get("template") or "")
            return (
                template.replace("{{resolution4k}}", format_disc_resolution(resolution, True) or resolution or "")
                .replace("{{resolution}}", resolution or "")
                .replace("{{source}}", normalized or source)
                .strip()
            )
    return None


def submission_resolution_label(value: str, source: str = "") -> Optional[str]:
    normalized = normalize_resolution(value)
    if not normalized:
        return None
    if normalized.upper() == "4K":
        return "2160p"
    # Use lowercase 'p' format (e.g. 1080p, 720p)
    return re.sub(r'(\d)[Pp]$', r'\1p', normalized)


def normalize_resolution(value: str) -> str:
    upper = str(value or "").strip().upper()
    if upper in {"4320P", "8K"}:
        return "8K"
    if upper in {"2160P", "4K", "UHD", "ULTRA HD"}:
        return "2160p"
    if upper in {"1440P", "QHD", "2K"}:
        return "2K"
    if upper == "FHD":
        return "1080P"
    if upper == "HD":
        return "720P"
    return str(value or "").strip()


def compact_source(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("_", " ").replace(".", " ")
    key = re.sub(r"[\s_-]+", " ", text).strip().upper()
    if "UHD" in key and "BLURAY" in key and "REMUX" in key:
        return "UHD BluRay Remux"
    if "BLURAY" in key and "REMUX" in key:
        return "BluRay Remux"
    if key in {"WEB DL", "WEBDL"}:
        return "WEB-DL"
    if key == "WEBRIP":
        return "WEBRip"
    if key == "BLURAY":
        return "BluRay"
    if key == "REMUX":
        return "Remux"
    return text


def normalize_source_variants(value: str) -> str:
    if not value:
        return ""
    variants = [compact_source(item) or item for item in str(value).split("/") if str(item or "").strip()]
    return "/".join(dict.fromkeys(variants))


def compact_effect(value: str) -> Optional[str]:
    if not value:
        return None
    parts = []
    for item in re.split(r"[/, ]+", value):
        clean = item.strip()
        if not clean:
            continue
        upper = clean.replace(".", "").upper()
        if upper in {"DOLBYVISION", "DOVI"}:
            clean = "DV"
        elif upper in {"HDR10+", "HDR10P"}:
            clean = "HDR10"
        if clean not in parts:
            parts.append(clean)
    return " ".join(parts) if parts else None


def compact_web_source(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    mapping = {"NETFLIX": "NF", "AMAZON": "AMZN", "PRIME": "AMZN", "APPLETV+": "ATVP", "DISNEY+": "DSNP"}
    return mapping.get(text.replace(" ", "").upper(), text)


def normalize_fps(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text.upper() if text.upper().endswith("FPS") else f"{text}FPS"


def compact_season_episode(value: str) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def format_disc_resolution(value: Optional[str], prefer_4k: bool = False) -> Optional[str]:
    if not value:
        return None
    if prefer_4k and str(value).upper() in {"2160P", "4K"}:
        return "4K"
    return value


def source_label_key(value: str) -> str:
    return re.sub(r"[\s._-]+", "", str(value or "")).upper()


def submission_display_title(media: Dict[str, Any]) -> str:
    candidates = [media.get("title"), *(media.get("aliases") or []), media.get("originalTitle")]
    for value in candidates:
        text = str(value or "").strip()
        if text and re.search(r"[\u4e00-\u9fff]", text):
            return text
    return str(media.get("title") or "未识别媒体")


def media_type_label(value: str) -> str:
    return {"movie": "电影", "tv": "剧集"}.get(value, "未识别")


def tmdb_post_marker(media_type: str, tmdb_id: Any) -> str:
    try:
        value = int(tmdb_id or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0 or media_type not in {"movie", "tv"}:
        return ""
    return f"{'📺' if media_type == 'tv' else '🎬'} TMDB: {value}"


def render_rating(value: Any, url: str = "") -> str:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return "暂无"
    text = f"{rating:.1f}/10"
    return f'<a href="{escape(url)}">{escape(text)}</a>' if url else escape(text)


def render_share_link(draft: Dict[str, Any], config: Dict[str, Any]) -> str:
    templates = config.get("templates") if isinstance(config.get("templates"), dict) else {}
    name = escape(submission_share_name(draft, config))
    # A collaborator is attributed to their Telegram profile, not to the
    # site-wide share URL configured by the Bot administrator.
    if submission_submitter_name(draft, config):
        return name
    url = str(templates.get("shareUrl") or "").strip()
    return f'<a href="{escape(url)}">{name}</a>' if url else name


def render_share_url_value(draft: Dict[str, Any]) -> str:
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    clean_url = str(share.get("cleanUrl") or share.get("url") or "").strip()
    if clean_url.startswith("123FLCPV2$") or re.match(r"^https?://", clean_url, re.I):
        return clean_url
    return ""


def route_channel_label(draft: Dict[str, Any], store: Optional["SessionStore"]) -> str:
    owner_user_id = safe_int(draft.get("ownerUserId")) or 0
    channel = select_submission_channel(store, owner_user_id, draft)
    if not channel:
        return "未匹配"
    mode = "手动" if str(draft.get("channelId") or "").strip() else "自动"
    return f"{channel.get('title') or channel.get('id') or '频道'}（{mode}）"


def resource_block(draft: Dict[str, Any], config: Dict[str, Any]) -> str:
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    inspection = draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {}
    note = combined_submission_note(metadata.get("note"), draft.get("databaseNote"))
    if note:
        return f"<blockquote>{escape(note)}</blockquote>"
    line = build_submission_resource_name(metadata, inspection.get("fileNames") or [], config)
    return f"<blockquote>{escape(line)}</blockquote>" if line else ""


def render_overview_block(overview: str) -> str:
    text = str(overview or "").strip() or "暂无简介"
    expandable = " expandable" if len(text) > 90 else ""
    return f"📖 简介：\n<blockquote{expandable}>{escape(text)}</blockquote>"


def build_tags_text(title: str, genres: Iterable[Any]) -> str:
    values = [title, *[str(genre or "") for genre in genres]]
    tags = []
    for value in values:
        clean = re.sub(r"[^\w\u4e00-\u9fff-]+", "", str(value or ""))
        if clean and f"#{clean}" not in tags:
            tags.append(f"#{clean}")
    return " ".join(tags[:12]) or "未识别"


def find_channel(channels: List[Dict[str, Any]], channel_id: Any) -> Optional[Dict[str, Any]]:
    wanted = str(channel_id or "")
    return next((channel for channel in channels if str(channel.get("id") or "") == wanted), None) if wanted else None


def find_channel_by_role(channels: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    return next((channel for channel in channels if str(channel.get("role") or "") == role), None) or next((channel for channel in channels if infer_channel_role(channel) == role), None)


def find_default_channel(channels: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((channel for channel in channels if channel.get("isDefault")), None) or (channels[0] if channels else None)


def is_completed_media(draft: Dict[str, Any]) -> bool:
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    media_type = str(media.get("mediaType") or metadata.get("mediaType") or "")
    if media_type == "movie":
        return True
    if media_type != "tv":
        return False
    inspection = draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {}
    return bool(tmdb_completed_season_label(media, inspection.get("fileNames") or []))


def infer_channel_role(channel: Dict[str, Any]) -> str:
    text = f"{channel.get('title') or ''} {channel.get('id') or ''}".lower()
    if "完结" in text or "completed" in text:
        return "public_completed"
    if "更新" in text or "updating" in text:
        return "public_updating"
    if "私有" in text or "private" in text:
        return "private"
    return ""


def tmdb_completed_season_label(media: Dict[str, Any], file_names: Iterable[Any]) -> str:
    expected_counts = tmdb_season_episode_counts(media)
    episode_groups = submission_video_episode_groups(file_names)
    if not expected_counts or not episode_groups:
        return ""
    completed_seasons = sorted({season for season, _version in episode_groups})
    for (season, _version), episodes in episode_groups.items():
        expected_count = expected_counts.get(season, 0)
        if expected_count <= 0 or not set(range(1, expected_count + 1)).issubset(episodes):
            return ""
    if len(completed_seasons) == 1:
        return f"S{completed_seasons[0]:02d}"
    if completed_seasons == list(range(completed_seasons[0], completed_seasons[-1] + 1)):
        return f"S{completed_seasons[0]:02d}-S{completed_seasons[-1]:02d}"
    return "/".join(f"S{season:02d}" for season in completed_seasons)


def tmdb_season_files_complete(media: Dict[str, Any], season: int, file_names: Iterable[Any]) -> bool:
    expected_count = tmdb_season_episode_counts(media).get(int(season), 0)
    episodes = submission_video_episodes(file_names).get(int(season), set())
    return expected_count > 0 and set(range(1, expected_count + 1)).issubset(episodes)


def tmdb_season_episode_counts(media: Dict[str, Any]) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for season in media.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        raw_number = season.get("seasonNumber") if season.get("seasonNumber") is not None else season.get("season_number")
        raw_count = season.get("episodeCount") if season.get("episodeCount") is not None else season.get("episode_count")
        if raw_number is None or raw_count is None:
            continue
        number = safe_int(raw_number)
        count = safe_int(raw_count)
        if number >= 0 and count > 0:
            counts[number] = count
    return counts


def submission_video_episodes(file_names: Iterable[Any]) -> Dict[int, set[int]]:
    episodes: Dict[int, set[int]] = {}
    for (season, _version), values in submission_video_episode_groups(file_names).items():
        episodes.setdefault(season, set()).update(values)
    return episodes


def submission_video_episode_groups(file_names: Iterable[Any]) -> Dict[tuple[int, str], set[int]]:
    groups: Dict[tuple[int, str], set[int]] = {}
    pattern = re.compile(
        r"\bS(?P<season>\d{1,3})\s*(?:E|EP)(?P<first>\d{1,5})"
        r"(?:(?:\s*[-~]\s*(?:E|EP)?|\s*(?:E|EP))(?P<last>\d{1,5}))?",
        re.I,
    )
    for value in file_names:
        name = str(value or "")
        if not VIDEO_EXT_RE.search(name):
            continue
        version = "|".join(
            [
                extract_quality(name),
                extract_source(name),
                extract_web_source(name),
                extract_itunes_source(name),
                extract_release_group(name),
            ]
        ).upper()
        for match in pattern.finditer(name):
            season = int(match.group("season"))
            first = int(match.group("first"))
            last = int(match.group("last")) if match.group("last") else first
            if first <= 0 or last < first or last - first > 1000:
                continue
            groups.setdefault((season, version), set()).update(range(first, last + 1))
    return groups


def trim_link(value: str) -> str:
    return str(value or "").strip().rstrip(")，。；;,])")


def normalize_web_share_url(value: str) -> str:
    text = trim_link(value)
    if not re.match(r"^https?://", text, re.I):
        return text
    try:
        parsed = urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            return text
        return urlunparse(parsed)
    except Exception:
        return text


def first_present(values: Iterable[Optional[str]]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def extract_meta(html: str, name: str) -> str:
    pattern = rf"<meta[^>]+(?:property|name)=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']+)[\"']"
    match = re.search(pattern, str(html or ""), re.I)
    return unescape(match.group(1)) if match else ""


def extract_html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", str(html or ""), re.I)
    return unescape(match.group(1)) if match else ""


def clean_share_page_title(title: str) -> str:
    text = re.sub(r" - 123云盘.*$", "", str(title or ""), flags=re.I)
    text = re.sub(r"_?123云盘.*$", "", text, flags=re.I)
    text = re.sub(r"^\s*分享\s*", "", text)
    return text.strip()


def strip_tags(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", str(html or ""), flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def extract_share_password(value: str) -> str:
    match = PWD_RE.search(str(value or ""))
    if match:
        return match.group(1)
    try:
        query = parse_qs(urlparse(str(value or "")).query)
        return str((query.get("pwd") or [""])[0])
    except Exception:
        return ""


def get_share_key(url_text: str) -> str:
    try:
        parsed = urlparse(url_text)
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if len(parts) > 2 and parts[0].lower() == "gsb" and parts[1].lower() == "s":
        return re.sub(r"\.html$", "", parts[2], flags=re.I)
    if parts[0] in {"s", "ps", "123pan"} and len(parts) > 1:
        return re.sub(r"\.html$", "", parts[1], flags=re.I)
    return re.sub(r"\.html$", "", parts[0], flags=re.I)


def fastlink_context(text: str, index: int, link: str) -> Dict[str, str]:
    source = str(text or "")
    before = max(source.rfind("\n\n", 0, index), source.rfind("\r\n\r\n", 0, index))
    after_candidates = [pos for pos in [source.find("\n\n", index + len(link)), source.find("\r\n\r\n", index + len(link))] if pos >= 0]
    after = min(after_candidates) if after_candidates else len(source)
    block = source[before + 2 if before >= 0 else 0 : after].strip()
    title = ""
    for line in block.splitlines():
        clean = line.strip()
        if not clean or "123FLCPV2$" in clean:
            continue
        title = re.sub(r"^🎬\s*[：:]?\s*", "", clean)
        title = re.sub(r"^标题\s*[：:]\s*", "", title).strip()
        if title:
            break
    return {"title": title, "sourceText": block or link}


def parse_fastlink(link: str) -> Dict[str, Any]:
    body = re.sub(r"^123FLCPV2\$%?", "", str(link or ""), flags=re.I)
    parts = body.split("#", 2)
    if len(parts) < 3:
        return {}
    try:
        size = int(parts[1])
    except (TypeError, ValueError):
        size = 0
    return {"size": size, "fileName": decode_fastlink_name(parts[2].strip())}


def decode_fastlink_name(value: str) -> str:
    try:
        from urllib.parse import unquote

        return unquote(value)
    except Exception:
        return value


def strip_fastlink_seed_ext(value: str) -> str:
    return re.sub(r"\.123fastlink\.json$", "", str(value or ""), flags=re.I).strip() or str(value or "")


def extract_fastlink_context_files(source_text: str) -> List[str]:
    files: List[str] = []
    seen = set()
    for line in str(source_text or "").splitlines():
        clean = re.sub(r"^📄\s*[：:]?\s*", "", line.strip()).strip()
        if not clean or "123FLCPV2$" in clean or re.match(r"还有\s+\d+\s+个文件", clean):
            continue
        if not re.search(r"\.(?:mkv|mp4|avi|mov|rmvb|wmv|flv|webm|m4v|mpeg|mpg|3gp|ts|m2ts|mts|ass|srt|ssa|sub|vtt|nfo|jpg|jpeg|png|webp)$", clean, re.I):
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            files.append(clean)
    return files


def extract_fastlink_context_size(source_text: str) -> str:
    for line in str(source_text or "").splitlines():
        if not re.search(r"(?:💾|大小|总大小|size)", line, re.I):
            continue
        match = re.search(r"(\d+(?:\.\d+)?\s*(?:PB|TB|GB|MB|KB|B))", line, re.I)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    return ""


def extract_likely_file_names(text: str) -> List[str]:
    names = []
    seen = set()
    decoded = unescape(str(text or ""))
    patterns = [
        r"\"FileName\"\s*:\s*\"([^\"]+)\"",
        r"\"filename\"\s*:\s*\"([^\"]+)\"",
        r"\"name\"\s*:\s*\"([^\"]+\.(?:mkv|mp4|avi|mov|ass|srt|zip|rar|7z|ts|m2ts|mts))\"",
        r"([^\s/\\]+\.(?:mkv|mp4|avi|mov|ass|srt|zip|rar|7z|ts|m2ts|mts))",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, decoded, re.I):
            name = decode_jsonish_text(match.group(1))
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names[:50]


def decode_jsonish_text(value: str) -> str:
    text = str(value or "")
    if "\\u" in text or "\\/" in text:
        try:
            text = text.encode("utf-8").decode("unicode_escape")
        except Exception:
            pass
        text = text.replace("\\/", "/")
    return text.strip()


def first_media_filename(file_names: Iterable[Any]) -> str:
    for name in file_names:
        text = str(name or "")
        if VIDEO_EXT_RE.search(text):
            return text
    for name in file_names:
        text = str(name or "")
        if text:
            return text
    return ""


def deduplicate_file_names(file_names: Iterable[Any]) -> List[str]:
    values: List[str] = []
    seen = set()
    for value in file_names:
        clean = str(value or "").strip().replace("\\", "/")
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            values.append(clean)
    bare_names = {value.casefold() for value in values if "/" not in value}
    nested_names = {value.rsplit("/", 1)[-1].casefold() for value in values if "/" in value}
    duplicated_bare_names = bare_names & nested_names
    return [value for value in values if "/" in value or value.casefold() not in duplicated_bare_names]


def join_unique_lines(values: Iterable[str]) -> str:
    lines = []
    seen = set()
    for value in values:
        for line in str(value or "").splitlines():
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                lines.append(clean)
    return "\n".join(lines)


def js_named_groups_to_python(pattern: str) -> str:
    return re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)


def normalize_metadata_value(key: str, value: str, config: Optional[Dict[str, Any]] = None) -> str:
    clean = str(value or "").strip()
    if key == "title":
        return clean_title(clean)
    if key == "quality":
        return normalize_resolution(clean)
    if key in {"source", "resourceType"}:
        return compact_source(clean) or clean
    if key == "effect":
        return compact_effect(clean) or clean
    if key == "seasonEpisode":
        return first_season_episode(clean) or clean
    if key == "videoCodec":
        return extract_video_codec(clean) or clean
    if key == "audioCodec":
        return extract_audio_codec(clean) or clean
    if key == "fps":
        return extract_fps(clean)
    if key == "webSource":
        normalized = extract_web_source(clean, config)
        return normalized or compact_web_source(clean) or clean
    return clean


def valid_recognition_field(key: str, value: str, text: str, start: int, end: int) -> bool:
    clean = str(value or "").strip()
    if not clean:
        return False
    if key == "seasonEpisode":
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if (before and before.isalnum()) or (after and after.isalnum()):
            return False
        return bool(re.fullmatch(
            r"(?:S\d{1,3}(?:(?:E|EP)\d{1,5}(?:(?:E|EP)\d{1,5}|\s*[-~]\s*(?:S\d{1,3})?(?:E|EP)?\d{1,5})?|\s*[-~]\s*S\d{1,3})?|(?:EP|Episode)\s*\d{1,5}(?:\s*[-~]\s*(?:EP)?\d{1,5})?|第\s*\d{1,5}\s*集)",
            clean,
            re.I,
        ))
    if key == "fps":
        return bool(extract_fps(clean))
    if key == "releaseGroup":
        return is_release_group_suffix(clean) and not _is_known_media_term(clean)
    return True


def build_submission_tags(metadata: Dict[str, Any]) -> List[str]:
    values = []
    for key in ("title", "quality", "source", "resourceType", "webSource"):
        value = str(metadata.get(key) or "").strip()
        if value:
            values.append(value)
    return list(dict.fromkeys(re.sub(r"\s+", "", value) for value in values if value))


async def send_submission_preview(bot_token: str, chat_id: int, draft: Dict[str, Any], config: Dict[str, Any]) -> int:
    result = await send_submission_preview_result(bot_token, chat_id, draft, config)
    return int(result.get("sentCount") or 0)


async def send_submission_preview_result(
    bot_token: str,
    chat_id: Any,
    draft: Dict[str, Any],
    config: Dict[str, Any],
    store: Optional[SessionStore] = None,
    allow_rich_retry: bool = True,
) -> Dict[str, Any]:
    text = str(draft.get("caption") or draft.get("text") or "").strip()
    chunks = split_telegram_text(text)
    if not chunks:
        return {"sentCount": 0, "firstMessageId": 0}
    parse_mode = "HTML" if draft.get("caption") else None
    reply_markup = build_submission_preview_markup(draft, config, store)
    photo = media_photo(draft)
    TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
    sent_count = 0
    first_message_id = 0
    try:
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == 0 else None
            sent: Any = None
            if index == 0 and photo:
                # Telegram Bot API 的图片 caption 上限为 1024；短文案必须先创建无
                # caption 的图片消息，再编辑 caption，避免移动端 blockquote 首行空白。
                if len(chunk) > TELEGRAM_PHOTO_CAPTION_LIMIT:
                    photo_message = await send_telegram_photo(bot_token, chat_id, photo, "", reply_markup=None)
                    try:
                        sent = await send_telegram_text(bot_token, chat_id, chunk, parse_mode=parse_mode, reply_markup=markup)
                    except Exception:
                        photo_message_id = telegram_message_id(photo_message)
                        if photo_message_id > 0:
                            await delete_telegram_messages(bot_token, chat_id, [photo_message_id])
                        raise
                else:
                    sent = await send_telegram_photo_then_edit_caption(bot_token, chat_id, photo, chunk, markup)
            else:
                try:
                    sent = await send_telegram_text(bot_token, chat_id, chunk, parse_mode=parse_mode, reply_markup=markup)
                except Exception:
                    if not markup:
                        raise
                    sent = await send_telegram_text(bot_token, chat_id, chunk, parse_mode=parse_mode)
            sent_count += 1
            if index == 0:
                first_message_id = telegram_message_id(sent)
    except Exception as error:
        note = draft.get("databaseNote") if isinstance(draft.get("databaseNote"), dict) else {}
        if (
            allow_rich_retry
            and sent_count == 0
            and str(note.get("mode") or "rich") == "rich"
            and is_rich_note_parse_error(error)
        ):
            set_database_note_mode(draft, "plain")
            save_database_note_mode(store, bot_token, "plain")
            refresh_submission_caption(draft, config, store)
            return await send_submission_preview_result(bot_token, chat_id, draft, config, store=store, allow_rich_retry=False)
        raise
    if sent_count > 0 and isinstance(draft.get("databaseNote"), dict) and str(draft["databaseNote"].get("mode") or "rich") == "rich":
        save_database_note_mode(store, bot_token, "rich")
    return {"sentCount": sent_count, "firstMessageId": first_message_id}


def build_submission_preview_markup(draft: Dict[str, Any], config: Dict[str, Any], store: Optional["SessionStore"] = None) -> Optional[Dict[str, Any]]:
    """投稿预览的操作键盘，样式与旧版 Telegram 机器人一致。"""
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    clean_url = str(share.get("cleanUrl") or share.get("url") or "").strip()
    draft_id = str(draft.get("id") or "").strip()
    if not draft_id:
        return None
    provider = str(share.get("provider") or "")
    rows: List[List[Dict[str, Any]]] = []
    if provider == "123fastlink" and clean_url.startswith("123FLCPV2$"):
        rows.append([{"text": "秒传链接", "copy_text": {"text": clean_url}}])
    elif re.match(r"^https?://", clean_url, re.I):
        share_name = submission_share_name(draft, config)
        rows.append([{"text": f"{share_name}网盘", "url": clean_url}])
    route_channel = route_channel_label(draft, store) if store else "未匹配"
    rows.extend(
        [
            [{"text": f"📍 路由：{route_channel}", "callback_data": f"sub:{draft_id}:noop"}],
            [
                {"text": "✍️ 修改大小", "callback_data": f"sub:{draft_id}:edit:size"},
                {"text": "🧾 修改备注", "callback_data": f"sub:{draft_id}:edit:note"},
            ],
            [
                {"text": "🔍 更改识别", "callback_data": f"sub:{draft_id}:edit:title"},
                {"text": "📍 指定频道", "callback_data": f"sub:{draft_id}:channel"},
            ],
            [{"text": "📣 发布到频道", "callback_data": f"sub:{draft_id}:publish"}],
        ]
    )
    return {"inline_keyboard": rows}


def build_share_markup_row(draft: Dict[str, Any], config: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    clean_url = str(share.get("cleanUrl") or share.get("url") or "").strip()
    provider = str(share.get("provider") or "")
    if provider == "123fastlink" and clean_url.startswith("123FLCPV2$"):
        share_row = [{"text": "秒传链接", "copy_text": {"text": clean_url}}]
    elif re.match(r"^https?://", clean_url, re.I):
        share_name = submission_share_name(draft, config)
        share_row = [{"text": f"{share_name}网盘", "url": clean_url}]
    else:
        return None
    return share_row


def media_photo(draft: Dict[str, Any]) -> Optional[str]:
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    for key in ("backdropUrl", "posterUrl"):
        value = str(media.get(key) or "").strip()
        if value:
            return value
    return None


async def send_telegram_photo(
    bot_token: str,
    chat_id: Any,
    photo: str,
    caption: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_post(bot_token, "sendPhoto", payload)


async def send_telegram_text(
    bot_token: str,
    chat_id: Any,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_post(bot_token, "sendMessage", payload)


async def send_telegram_document(
    bot_token: str,
    chat_id: Any,
    filename: str,
    content: str,
    mime_type: str = "application/octet-stream",
    caption: str = "",
    reply_to_message_id: int = 0,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    if reply_to_message_id > 0:
        data["reply_to_message_id"] = str(reply_to_message_id)
        data["allow_sending_without_reply"] = "true"
    files = {"document": (filename, str(content or "").encode("utf-8"), mime_type or "application/octet-stream")}
    client, owned = _telegram_request_client()
    try:
        response = await client.post(f"{TELEGRAM_API_BASE}/bot{bot_token}/sendDocument", data=data, files=files, timeout=60.0)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code >= 400 or not payload.get("ok", True):
            raise ValueError(str(payload.get("description") or f"Telegram {response.status_code}"))
        result = payload.get("result")
        return result if isinstance(result, dict) else {}
    finally:
        if owned:
            await client.aclose()


async def telegram_post(bot_token: str, method: str, payload: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    for attempt in range(3):
        client, owned = _telegram_request_client()
        try:
            response = await client.post(url, json=payload, timeout=timeout)
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if response.status_code == 429:
                retry_after = int((data.get("parameters") or {}).get("retry_after") or 2)
                logger.warning("Telegram 429 rate limited, retry_after=%s, method=%s", retry_after, method)
                if attempt < 2:
                    await asyncio.sleep(min(retry_after, 10))
                    continue
                raise ValueError(str(data.get("description") or f"Telegram {response.status_code}"))
            if response.status_code >= 400 or not data.get("ok", True):
                raise ValueError(str(data.get("description") or f"Telegram {response.status_code}"))
            result = data.get("result")
            return result if isinstance(result, dict) else {}
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            if attempt < 2:
                await asyncio.sleep(0.5)
            else:
                raise
        finally:
            if owned:
                await client.aclose()
    return {}


def telegram_message_id(message: Any) -> int:
    if isinstance(message, dict):
        return safe_int(message.get("message_id"))
    # Telethon Message object
    if hasattr(message, "id"):
        return safe_int(getattr(message, "id", 0))
    return 0


async def publish_submission_draft(store: SessionStore, bot_token: str, config: Dict[str, Any], draft: Dict[str, Any], callback_id: str = "", callback_message_id_value: int = 0, acting_user_id: int = 0) -> Dict[str, Any]:
    owner_user_id = safe_int(draft.get("ownerUserId")) or 0
    channel = select_submission_channel(store, owner_user_id, draft)
    if not channel or not str(channel.get("chatId") or "").strip():
        await answer_callback_query(bot_token, callback_id, "自动路由没有找到可用频道，请检查频道用途配置", True)
        return {"action": "publish", "ok": False, "reason": "channel_not_configured"}

    # Check both the draft owner and the selected channel grant before sending.
    effective_user_id = acting_user_id or owner_user_id
    if effective_user_id != owner_user_id or not draft_channel_allowed(store, draft, effective_user_id):
        await answer_callback_query(bot_token, callback_id, "你没有权限推送到此频道", True)
        return {"action": "publish", "ok": False, "reason": "channel_not_allowed"}

    chat_id = str(channel.get("chatId") or "").strip()
    access_error = await check_telegram_chat_access(bot_token, chat_id)
    if access_error:
        await answer_callback_query(bot_token, callback_id, channel_access_hint(chat_id), True)
        return {"action": "publish", "ok": False, "reason": access_error}

    draft["channelId"] = str(channel.get("id") or "")
    draft["channelTitle"] = str(channel.get("title") or "")
    draft["channelChatId"] = chat_id
    caption = render_submission_caption(draft, config, store, include_route=False)

    async def publish_once() -> Dict[str, Any]:
        sent = await send_telegram_rich_message(bot_token, chat_id, caption, media_photo(draft), build_publish_markup(draft, config), parse_mode="HTML", config=config)
        message_id = telegram_message_id(sent)
        if message_id <= 0:
            raise ValueError("Telegram 未返回频道消息 ID")

        seed_message_ids: List[int] = []
        try:
            seed_message_ids = await send_submission_seed_documents(bot_token, chat_id, draft, message_id)
        except Exception:
            await delete_telegram_messages(bot_token, chat_id, [message_id, *seed_message_ids])
            raise

        draft["status"] = "published"
        draft["publishedAt"] = utc_now_iso()
        draft["publishedMessageId"] = message_id
        draft["publishedSeedMessageIds"] = seed_message_ids
        delete_submission_draft(store, str(draft.get("id") or ""))
        await safe_answer_callback_query(bot_token, callback_id, "已发布", timeout=4.0)
        await cleanup_submission_draft_messages(bot_token, draft, callback_message_id_value)
        history_warning = await cleanup_previous_submission_publications(store, bot_token, config, draft, chat_id, message_id)
        if history_warning:
            logger.warning(
                "Local channel publication cleanup finished with warning",
                extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "message_id": message_id, "warning": history_warning},
            )
        record_submission_publication(store, config, draft, channel, chat_id, message_id, seed_message_ids)
        schedule_published_submission_history_cleanup(store, config, draft, chat_id, message_id)
        return {"action": "publish", "ok": True, "channelId": str(channel.get("id") or ""), "messageId": message_id, "seedMessageIds": seed_message_ids}

    try:
        return await with_channel_publish_queue(channel_publish_queue_key(str(channel.get("id") or ""), chat_id), publish_once)
    except Exception as error:
        message = str(error)
        await safe_answer_callback_query(bot_token, callback_id, publish_error_hint(message, chat_id), True)
        return {"action": "publish", "ok": False, "reason": message}


async def cleanup_previous_submission_publications(store: SessionStore, bot_token: str, config: Dict[str, Any], draft: Dict[str, Any], chat_id: Any, message_id: int) -> str:
    identity = submission_publication_identity(draft, config)
    identity_key = str(identity.get("identityKey") or "")
    if not identity_key:
        return ""
    route_owner_user_id = safe_int(draft.get("routeOwnerUserId")) or safe_int(draft.get("ownerUserId"))
    try:
        previous = store.find_submission_publications(str(chat_id), identity_key, message_id, 20, route_owner_user_id)
    except Exception:
        logger.warning(
            "Loading local submission publication history failed",
            extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "identity_key": identity_key},
            exc_info=True,
        )
        return "读取本地发布历史失败"
    # 多版本场景下 identityKey 因 resource_key 不同而失配，按 TMDB ID + 分享链接兜底匹配
    if not previous:
        tmdb_id = safe_int(identity.get("tmdbId"))
        share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
        current_share_key = share_media_cache_key(str(share.get("cleanUrl") or share.get("url") or ""))
        if tmdb_id > 0 and current_share_key:
            try:
                candidates = store.find_submission_publications_by_tmdb_share(str(chat_id), tmdb_id, message_id, 20, route_owner_user_id)
                previous = [
                    item for item in candidates
                    if share_media_cache_key(str(item.get("shareUrl") or "")) == current_share_key
                ]
            except Exception:
                logger.warning(
                    "Fallback publication lookup by tmdb+share failed",
                    extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "tmdb_id": tmdb_id},
                    exc_info=True,
                )
    if not previous:
        return ""

    by_message_id: Dict[int, List[str]] = {}
    by_publication_id: Dict[str, set[int]] = {}
    for item in previous:
        publication_id = str(item.get("id") or "")
        message_ids = submission_publication_message_ids(item)
        if not publication_id or not message_ids:
            continue
        old_message_id = safe_int(item.get("messageId"))
        if old_message_id <= 0 or old_message_id == safe_int(message_id):
            continue
        for message_id_value in message_ids:
            by_message_id.setdefault(message_id_value, []).append(publication_id)
            by_publication_id.setdefault(publication_id, set()).add(message_id_value)
    if not by_message_id:
        return ""

    result = await delete_telegram_messages_with_result(bot_token, chat_id, by_message_id.keys())
    deleted_message_ids = {safe_int(value) for value in result.get("deletedMessageIds", []) if safe_int(value) > 0}
    deleted_ids = [
        publication_id
        for publication_id, wanted_message_ids in by_publication_id.items()
        if wanted_message_ids and wanted_message_ids.issubset(deleted_message_ids)
    ]
    try:
        store.mark_submission_publications_deleted(deleted_ids)
    except Exception:
        logger.warning(
            "Marking local submission publications deleted failed",
            extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "message_ids": list(by_message_id)},
            exc_info=True,
        )
    failed = result.get("failedMessageIds", [])
    if failed:
        return f"本地历史有 {len(failed)} 条旧帖删除失败"
    return ""


def record_submission_publication(store: SessionStore, config: Dict[str, Any], draft: Dict[str, Any], channel: Dict[str, Any], chat_id: Any, message_id: int, seed_message_ids: Optional[List[int]] = None) -> None:
    identity = submission_publication_identity(draft, config)
    identity_key = str(identity.get("identityKey") or "")
    if not identity_key:
        return
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    try:
        store.record_submission_publication(
            {
                **identity,
                "channelChatId": str(chat_id),
                "channelId": str(channel.get("id") or ""),
                "channelTitle": str(channel.get("title") or ""),
                "routeOwnerUserId": safe_int(draft.get("routeOwnerUserId")) or safe_int(draft.get("ownerUserId")),
                "messageId": int(message_id or 0),
                "seedMessageIds": [safe_int(value) for value in (seed_message_ids or draft.get("publishedSeedMessageIds") or []) if safe_int(value) > 0],
                "shareUrl": str(share.get("cleanUrl") or share.get("url") or ""),
                "fastLink": str(share.get("provider") or "") == "123fastlink",
                "draftId": str(draft.get("id") or ""),
                "publishedAt": str(draft.get("publishedAt") or utc_now_iso()),
            }
        )
    except Exception:
        logger.warning(
            "Recording local submission publication history failed",
            extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "message_id": message_id},
            exc_info=True,
        )


def submission_publication_message_ids(publication: Dict[str, Any]) -> List[int]:
    ids: List[int] = []
    for value in publication.get("seedMessageIds") or []:
        message_id = safe_int(value)
        if message_id > 0 and message_id not in ids:
            ids.append(message_id)
    main_message_id = safe_int(publication.get("messageId"))
    if main_message_id > 0 and main_message_id not in ids:
        ids.append(main_message_id)
    return ids


def submission_publication_identity(draft: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    inspection = draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {}
    share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
    media_type = str(media.get("mediaType") or metadata.get("mediaType") or "unknown")
    tmdb_id = safe_int(media.get("tmdbId") or metadata.get("tmdbId"))
    title = first_present(
        [
            submission_display_title(media) if media.get("title") else "",
            metadata.get("title"),
            inspection.get("title"),
            share.get("title"),
        ]
    )
    year = str(media.get("year") or metadata.get("year") or "")
    if year and title and f"({year})" not in title:
        title = f"{title} ({year})"
    resource_name = build_submission_resource_name(metadata, inspection.get("fileNames") or [], config)
    title_key = normalize_publication_identity_text(title)
    resource_key = normalize_publication_identity_text(resource_name)
    share_key = share_media_cache_key(str(share.get("cleanUrl") or share.get("url") or ""))
    if tmdb_id > 0:
        identity_key = f"tmdb:{media_type}:{tmdb_id}:{resource_key or title_key}"
    else:
        identity_key = f"title:{title_key or resource_key or share_key}"
    return {
        "identityKey": identity_key.strip(":"),
        "mediaType": media_type,
        "tmdbId": tmdb_id if tmdb_id > 0 else None,
        "titleKey": title_key,
        "title": title,
        "resourceName": resource_name,
    }


def normalize_publication_identity_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{?\b(?:tmdbid|tmdb)[=\-_: ]?\d{2,10}\}?", " ", text, flags=re.I)
    text = re.sub(r"[\s._\-:/\\|()[\]{}<>《》【】（）]+", " ", text)
    return text.strip().casefold()


async def cleanup_published_submission_history(config: Dict[str, Any], draft: Dict[str, Any], chat_id: Any, message_id: int) -> str:
    telegram_api = config.get("telegramApi") if isinstance(config.get("telegramApi"), dict) else {}
    if not str(telegram_api.get("apiId") or "").strip() or not str(telegram_api.get("apiHash") or "").strip() or not str(telegram_api.get("session") or "").strip():
        return ""
    try:
        from .telegram_history import TelegramHistoryCleaner

        media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
        identity = submission_publication_identity(draft, config)
        result = await TelegramHistoryCleaner().cleanup(
            telegram_api,
            {
                "chatId": str(chat_id),
                "messageId": message_id,
                "mediaType": str(media.get("mediaType") or metadata.get("mediaType") or "unknown"),
                "tmdbId": media.get("tmdbId") or metadata.get("tmdbId"),
                "shareUrl": str(share.get("cleanUrl") or ""),
                "fastLink": str(share.get("provider") or "") == "123fastlink",
                "identityKey": str(identity.get("identityKey") or ""),
                "titleKey": str(identity.get("titleKey") or ""),
                "resourceName": str(identity.get("resourceName") or ""),
            },
        )
        deleted = result.get("deletedMessageIds") if isinstance(result, dict) else []
        failed = result.get("failedMessageIds") if isinstance(result, dict) else []
        logger.info(
            "TG API old submission cleanup completed",
            extra={
                "draft_id": str(draft.get("id") or ""),
                "channel_chat_id": str(chat_id),
                "message_id": message_id,
                "deleted_old_message_count": len(deleted) if isinstance(deleted, list) else 0,
                "failed_old_message_count": len(failed) if isinstance(failed, list) else 0,
            },
        )
        if isinstance(failed, list) and failed:
            return f"有 {len(failed)} 条旧帖删除失败"
        return ""
    except Exception as error:
        logger.warning(
            "Submission was published, but old channel cleanup failed",
            extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "message_id": message_id},
            exc_info=True,
        )
        return str(error)


def refresh_submission_caption(draft: Dict[str, Any], config: Dict[str, Any], store: "SessionStore") -> None:
    owner_user_id = safe_int(draft.get("ownerUserId")) or 0
    channel = select_submission_channel(store, owner_user_id, draft)
    if channel:
        draft["routeChannelId"] = str(channel.get("id") or "")
        draft["routeChannelTitle"] = str(channel.get("title") or "")
        draft["routeChannelChatId"] = str(channel.get("chatId") or "")
        if str(draft.get("channelId") or ""):
            draft["channelTitle"] = str(channel.get("title") or "")
            draft["channelChatId"] = str(channel.get("chatId") or "")
        elif not str(draft.get("channelTitle") or ""):
            draft["channelTitle"] = str(channel.get("title") or "")
            draft["channelChatId"] = str(channel.get("chatId") or "")
    else:
        draft["routeChannelId"] = ""
        draft["routeChannelTitle"] = ""
        draft["routeChannelChatId"] = ""
    caption = render_submission_caption(draft, config, store)
    draft["caption"] = caption
    draft["text"] = caption


async def find_submission_recognition_candidates(config: Dict[str, Any], query: str, limit: int = 6) -> List[Dict[str, Any]]:
    token = str(config.get("tmdbToken") or "").strip()
    if not token:
        return []
    language = str(config.get("tmdbLanguage") or "zh-CN").strip() or "zh-CN"
    try:
        from .tmdb import normalize_tmdb_media_type, parse_tmdb_lookup_query, tmdb_find_by_id, tmdb_search_candidates

        parsed = parse_tmdb_lookup_query(query)
        media_type = normalize_tmdb_media_type(parsed.get("mediaType"))
        tmdb_id = safe_int(parsed.get("tmdbId"))
        lookup_query = str(parsed.get("query") or query or "").strip()
        if tmdb_id > 0:
            return (await tmdb_find_by_id(token, language, tmdb_id, media_type))[:limit]
        return (await tmdb_search_candidates(token, language, lookup_query, "", media_type, limit))[:limit]
    except Exception:
        return []


def telegram_admin_allowed(config: Dict[str, Any], user_id: int) -> bool:
    return user_id > 0 and user_id in set(positive_ints(config.get("telegramAdminUserIds") or config.get("allowedUserIds") or []))


def telegram_channel_owner_allowed(config: Dict[str, Any], user_id: int, store: Optional["SessionStore"] = None) -> bool:
    if user_id <= 0:
        return False
    if user_id in set(positive_ints(config.get("channelOwnerUserIds") or config.get("allowedUserIds") or [])):
        return True
    return bool(store and store.has_user_channel_config(user_id))


def telegram_submission_allowed(config: Dict[str, Any], user_id: int, store: Optional["SessionStore"] = None) -> bool:
    if telegram_admin_allowed(config, user_id) or telegram_channel_owner_allowed(config, user_id, store):
        return True
    return bool(store and store.granted_submission_channels(user_id))


def channel_user_allowed(channel: Dict[str, Any], user_id: int, owner_user_id: int) -> bool:
    """检查 user_id 是否有权推送到此频道。空 allowedUserIds 表示仅所有者可用。"""
    allowed = set(positive_ints(channel.get("allowedUserIds") or []))
    if not allowed:
        return user_id == owner_user_id
    return user_id == owner_user_id or user_id in allowed


def enabled_submission_channels(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [channel for channel in channels if isinstance(channel, dict) and channel.get("enabled") is not False]


def build_publish_markup(draft: Dict[str, Any], config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    share_row = build_share_markup_row(draft, config)
    return {"inline_keyboard": [share_row]} if share_row else None


async def send_telegram_photo_via_client(config: Dict[str, Any], chat_id: str, photo: str, caption: str, parse_mode: str = "HTML") -> Any:
    """通过 Telegram Client API (Telethon) 发送带长文案的海报，caption 上限 4096 字符。"""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as error:
        raise RuntimeError("请先安装后端依赖 telethon") from error

    telegram_api = config.get("telegramApi") if isinstance(config.get("telegramApi"), dict) else {}
    api_id = safe_int(telegram_api.get("apiId"))
    api_hash = str(telegram_api.get("apiHash") or "").strip()
    session = str(telegram_api.get("session") or "").strip()
    if api_id <= 0 or not api_hash or not session:
        raise ValueError("TG API 未配置")

    client = TelegramClient(StringSession(session), api_id, api_hash, connection_retries=3, retry_delay=1)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ValueError("TG API Session 未授权或已失效")
        try:
            peer = await client.get_input_entity(int(chat_id))
        except Exception:
            dialogs = await client.get_dialogs(limit=None)
            wanted = int(chat_id)
            for dialog in dialogs:
                if safe_int(getattr(dialog, "id", 0)) == wanted:
                    peer = getattr(dialog, "input_entity")
                    break
            else:
                raise ValueError(f"TG API 用户无法访问目标频道：{chat_id}")
        return await client.send_file(peer, photo, caption=caption, parse_mode=parse_mode)
    finally:
        await client.disconnect()


async def send_telegram_photo_then_edit_caption(
    bot_token: str,
    chat_id: Any,
    photo: str,
    caption: str,
    reply_markup: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Atomically publish a short HTML caption without sendPhoto rendering it."""
    sent = await send_telegram_photo(bot_token, chat_id, photo, "", reply_markup=None)
    message_id = telegram_message_id(sent)
    if message_id <= 0:
        raise ValueError("Telegram 未返回图片消息 ID")

    try:
        await edit_telegram_message_caption(bot_token, chat_id, message_id, caption, reply_markup)
    except Exception:
        logger.warning(
            "Telegram caption edit failed; removing blank photo",
            extra={"chat_id": str(chat_id), "message_id": message_id},
            exc_info=True,
        )
        try:
            await delete_telegram_messages(bot_token, chat_id, [message_id])
        except Exception:
            logger.warning(
                "Removing blank Telegram photo after caption edit failure failed",
                extra={"chat_id": str(chat_id), "message_id": message_id},
                exc_info=True,
            )
        raise
    return sent


async def send_telegram_rich_message(bot_token: str, chat_id: Any, caption: str, photo: Optional[str], reply_markup: Optional[Dict[str, Any]], parse_mode: Optional[str] = "HTML", config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
    if photo:
        # 优先使用 Client API 发送，支持 4096 字符 caption，海报与长文案合并显示
        if config and len(caption) > TELEGRAM_PHOTO_CAPTION_LIMIT:
            try:
                result = await send_telegram_photo_via_client(config, str(chat_id), photo, caption, parse_mode=parse_mode or "HTML")
                return {"message_id": telegram_message_id(result)} if result else {}
            except Exception as error:
                logger.warning("TG Client API 发送海报失败，回退 Bot API: %s", error)
        # Bot API: caption 超长时先单独发海报，再发文字，避免海报丢失
        if len(caption) > TELEGRAM_PHOTO_CAPTION_LIMIT:
            try:
                await send_telegram_photo(bot_token, chat_id, photo, "", reply_markup=None)
            except Exception:
                pass
            return await send_telegram_text(bot_token, chat_id, caption, parse_mode=parse_mode, reply_markup=reply_markup)
        return await send_telegram_photo_then_edit_caption(bot_token, chat_id, photo, caption, reply_markup)
    return await send_telegram_text(bot_token, chat_id, caption, parse_mode=parse_mode, reply_markup=reply_markup)


async def send_submission_seed_documents(bot_token: str, chat_id: Any, draft: Dict[str, Any], reply_to_message_id: int) -> List[int]:
    message_ids: List[int] = []
    try:
        for document in normalize_submission_documents(draft.get("documents") or []):
            sent = await send_telegram_document(
                bot_token,
                chat_id,
                str(document.get("fileName") or "123fastlink.json"),
                str(document.get("content") or ""),
                str(document.get("mimeType") or "application/json"),
                caption=str(document.get("caption") or ""),
                reply_to_message_id=reply_to_message_id,
            )
            message_id = telegram_message_id(sent)
            if message_id > 0:
                message_ids.append(message_id)
    except Exception:
        await delete_telegram_messages(bot_token, chat_id, message_ids)
        raise
    return message_ids


async def answer_callback_query(bot_token: str, callback_id: str, text: str = "", show_alert: bool = False, timeout: float = 20.0) -> Dict[str, Any]:
    if not callback_id:
        return {}
    payload: Dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:190]
        payload["show_alert"] = show_alert
    return await telegram_post(bot_token, "answerCallbackQuery", payload, timeout=timeout)


async def safe_answer_callback_query(bot_token: str, callback_id: str, text: str = "", show_alert: bool = False, timeout: float = 4.0) -> Dict[str, Any]:
    try:
        return await answer_callback_query(bot_token, callback_id, text, show_alert, timeout=timeout)
    except Exception as error:
        logger.warning("Answering Telegram callback failed", extra={"callback_id": callback_id, "error": str(error)})
        return {}


async def with_channel_publish_queue(key: str, task: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
    queue_key = str(key or "").strip().lower()
    if not queue_key:
        return await task()
    loop = asyncio.get_running_loop()
    previous = CHANNEL_PUBLISH_QUEUES.get(queue_key)
    gate: asyncio.Future[None] = loop.create_future()
    CHANNEL_PUBLISH_QUEUES[queue_key] = gate
    try:
        if previous:
            try:
                await previous
            except asyncio.CancelledError:
                if not previous.cancelled():
                    raise
            except Exception:
                pass
        return await task()
    finally:
        if not gate.done():
            gate.set_result(None)
        if CHANNEL_PUBLISH_QUEUES.get(queue_key) is gate:
            CHANNEL_PUBLISH_QUEUES.pop(queue_key, None)


def channel_publish_queue_key(channel_id: str, chat_id: str) -> str:
    return f"{str(channel_id or '').strip()}:{str(chat_id or '').strip()}".lower()


def schedule_published_submission_history_cleanup(store: SessionStore, config: Dict[str, Any], draft: Dict[str, Any], chat_id: Any, message_id: int) -> None:
    telegram_api = config.get("telegramApi") if isinstance(config.get("telegramApi"), dict) else {}
    if not str(telegram_api.get("apiId") or "").strip() or not str(telegram_api.get("apiHash") or "").strip() or not str(telegram_api.get("session") or "").strip():
        return
    if store.channel_owner_count(str(chat_id)) != 1:
        logger.info("Skipping channel history scan because the Telegram channel is shared by multiple owners", extra={"channel_chat_id": str(chat_id)})
        return

    async def run_cleanup() -> None:
        warning = await cleanup_published_submission_history(config, draft, chat_id, message_id)
        if warning:
            logger.warning(
                "Old channel submission cleanup finished with warning",
                extra={"draft_id": str(draft.get("id") or ""), "channel_chat_id": str(chat_id), "message_id": message_id, "warning": warning},
            )

    task = asyncio.create_task(run_cleanup())
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)


def publish_error_hint(message: str, chat_id: Any) -> str:
    lower = str(message or "").lower()
    if "chat not found" in lower or "not enough rights" in lower or "forbidden" in lower:
        return channel_access_hint(chat_id)
    return f"发布失败：{message}"


async def edit_telegram_message_caption(bot_token: str, chat_id: Any, message_id: int, caption: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "caption": caption, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_post(bot_token, "editMessageCaption", payload)


async def delete_telegram_messages(bot_token: str, chat_id: Any, ids: Iterable[Any]) -> None:
    await delete_telegram_messages_with_result(bot_token, chat_id, ids)


async def delete_telegram_messages_with_result(bot_token: str, chat_id: Any, ids: Iterable[Any]) -> Dict[str, List[int]]:
    seen = set()
    deleted: List[int] = []
    failed: List[int] = []
    batch_count = 0
    for value in ids or []:
        message_id = safe_int(value)
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        # 每 10 条加间隔，避免触发 Telegram 限流
        batch_count += 1
        if batch_count > 1 and batch_count % 10 == 1:
            await asyncio.sleep(0.5)
        try:
            await telegram_post(bot_token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=8.0)
            deleted.append(message_id)
        except Exception as error:
            error_text = str(error).lower()
            if "not found" in error_text:
                deleted.append(message_id)
            else:
                failed.append(message_id)
                logger.warning("Deleting Telegram message failed", extra={"chat_id": chat_id, "message_id": message_id}, exc_info=True)
    return {"deletedMessageIds": deleted, "failedMessageIds": failed}


async def cleanup_submission_draft_messages(bot_token: str, draft: Dict[str, Any], callback_message_id_value: int = 0) -> None:
    chat_id = safe_int(draft.get("ownerChatId"))
    ids = [
        draft.get("sourceMessageId"),
        draft.get("previewMessageId"),
        callback_message_id_value,
        *(draft.get("interactionMessageIds") or []),
    ]
    await delete_telegram_messages(bot_token, chat_id, ids)


async def cleanup_stale_submission_drafts(store: SessionStore, bot_token: str, current: Dict[str, Any]) -> None:
    current_id = str(current.get("id") or "")
    owner_chat_id = safe_int(current.get("ownerChatId"))
    share = current.get("share") if isinstance(current.get("share"), dict) else {}
    share_key = share_media_cache_key(str(share.get("cleanUrl") or share.get("url") or ""))
    if not bot_token or not current_id or not owner_chat_id or not share_key:
        return
    stale = []
    for draft in list_submission_drafts(store, 200):
        draft_share = draft.get("share") if isinstance(draft.get("share"), dict) else {}
        if (
            str(draft.get("id") or "") != current_id
            and str(draft.get("status") or "draft") == "draft"
            and safe_int(draft.get("ownerChatId")) == owner_chat_id
            and share_media_cache_key(str(draft_share.get("cleanUrl") or draft_share.get("url") or "")) == share_key
        ):
            stale.append(draft)
    for draft in stale:
        await cleanup_submission_draft_messages(bot_token, draft)
        delete_submission_draft(store, str(draft.get("id") or ""))
        logger.info(
            "Stale submission draft cleaned up",
            extra={"draft_id": str(draft.get("id") or ""), "replacement_draft_id": current_id, "owner_chat_id": owner_chat_id},
        )


async def check_telegram_chat_access(bot_token: str, chat_id: Any) -> str:
    try:
        await telegram_post(bot_token, "getChat", {"chat_id": chat_id})
        return ""
    except Exception as error:
        return str(error)


def channel_access_hint(chat_id: Any) -> str:
    return f"频道不可访问：{chat_id}。请把本 Bot 加为该频道管理员；私有频道不能直接复用别人后台的 ID。"


def extract_123_links(text: str) -> List[str]:
    return SHARE_LINK_RE.findall(text or "")


def unique_links(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        link = str(value or "").strip()
        if not link or link in seen:
            continue
        seen.add(link)
        result.append(link)
    return result


def positive_ints(values: Iterable[Any]) -> List[int]:
    result = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.append(number)
    return result


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def bool_config(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def compact_text(value: Any, max_length: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_length:
        return text
    return text[: max(1, max_length - 1)] + "…"


def status_label(value: str) -> str:
    return {"queued": "排队中", "running": "执行中", "success": "成功", "partial": "部分成功", "failed": "失败"}.get(value, value or "--")


def split_telegram_text(text: str, limit: int = 3900) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]
    parts = raw.split("\n\n")
    chunks: List[str] = []
    current = ""
    for part in parts:
        candidate = part if not current else current + "\n\n" + part
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(part) <= limit:
            current = part
        else:
            for index in range(0, len(part), limit):
                chunks.append(part[index : index + limit])
            current = ""
    if current:
        chunks.append(current)
    return chunks


def append_submission_draft(store: SessionStore, draft: Dict[str, Any]) -> Dict[str, Any]:
    now = utc_now_iso()
    next_draft = {
        **draft,
        "id": str(draft.get("id") or uuid.uuid4().hex),
        "text": str(draft.get("text") or draft.get("caption") or ""),
        "caption": str(draft.get("caption") or draft.get("text") or ""),
        "linkCount": int(draft.get("linkCount") or 1),
        "sourceLabel": str(draft.get("sourceLabel") or "投稿"),
        "sent": bool(draft.get("sent")),
        "createdAt": str(draft.get("createdAt") or now),
        "updatedAt": now,
    }
    current = store.read_value(SUBMISSION_DRAFTS_KEY)
    drafts = current if isinstance(current, list) else []
    store.write_value(SUBMISSION_DRAFTS_KEY, [next_draft, *drafts][:200])
    return normalize_submission_draft(next_draft)


def save_submission_draft(store: SessionStore, draft: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(draft.get("id") or "").strip()
    if not draft_id:
        draft_id = uuid.uuid4().hex
        draft["id"] = draft_id
    now = utc_now_iso()
    next_draft = {**draft, "updatedAt": now}
    drafts = list_submission_drafts(store, 200)
    replaced = False
    next_drafts: List[Dict[str, Any]] = []
    for item in drafts:
        if str(item.get("id") or "") == draft_id:
            next_drafts.append(next_draft)
            replaced = True
        else:
            next_drafts.append(item)
    if not replaced:
        next_drafts.insert(0, next_draft)
    store.write_value(SUBMISSION_DRAFTS_KEY, next_drafts[:200])
    return normalize_submission_draft(next_draft)


def get_submission_draft(store: SessionStore, draft_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(draft_id or "").strip()
    if not wanted:
        return None
    return next((draft for draft in list_submission_drafts(store, 200) if str(draft.get("id") or "") == wanted), None)


def mark_submission_draft_sent(store: SessionStore, draft_id: str, sent_count: int, preview_message_id: int = 0) -> Dict[str, Any]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return {}
    now = utc_now_iso()
    drafts = list_submission_drafts(store, 200)
    saved: Optional[Dict[str, Any]] = None
    next_drafts: List[Dict[str, Any]] = []
    for item in drafts:
        if str(item.get("id") or "") == draft_id:
            item = {
                **item,
                "sent": True,
                "sentAt": now,
                "sentCount": int(sent_count or 0),
                "previewMessageId": int(preview_message_id or item.get("previewMessageId") or 0),
                "updatedAt": now,
            }
            saved = item
        next_drafts.append(item)
    store.write_value(SUBMISSION_DRAFTS_KEY, next_drafts)
    return normalize_submission_draft(saved) if saved else {}


def list_submission_drafts(store: SessionStore, limit: int = 100) -> List[Dict[str, Any]]:
    current = store.read_value(SUBMISSION_DRAFTS_KEY)
    drafts = current if isinstance(current, list) else []
    return [normalize_submission_draft(item) for item in drafts[: max(1, min(int(limit or 100), 200))] if isinstance(item, dict)]


def delete_submission_draft(store: SessionStore, draft_id: str) -> None:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return
    drafts = [draft for draft in list_submission_drafts(store, 200) if str(draft.get("id") or "") != draft_id]
    store.write_value(SUBMISSION_DRAFTS_KEY, drafts)


def clear_submission_drafts(store: SessionStore) -> None:
    store.write_value(SUBMISSION_DRAFTS_KEY, [])


async def submit_existing_draft(store: SessionStore, draft_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
    draft_id = str(draft_id or "").strip()
    drafts = list_submission_drafts(store, 200)
    draft = next((item for item in drafts if str(item.get("id") or "") == draft_id), None)
    if not draft:
        raise ValueError("投稿草稿不存在")

    config = store.read_submission_config()
    allowed_ids = positive_ints(config.get("telegramAdminUserIds") or config.get("channelOwnerUserIds") or config.get("allowedUserIds") or [])
    target = int(target_user_id or (allowed_ids[0] if allowed_ids else 0))
    if target <= 0:
        raise ValueError("请先在投稿机器人配置里填写允许使用的 Telegram User ID")
    bot_token = str(config.get("botToken") or "").strip()
    if not bot_token:
        raise ValueError("请先配置 Bot Token 后再重新投稿")

    preview = await send_submission_preview_result(bot_token, target, draft, config, store=store)
    draft = save_submission_draft(store, draft)
    sent_count = int(preview.get("sentCount") or 0)
    saved = mark_submission_draft_sent(store, draft_id, sent_count, int(preview.get("firstMessageId") or 0))
    return {"sentCount": sent_count, "draft": saved}


def normalize_submission_draft(value: Dict[str, Any]) -> Dict[str, Any]:
    pending_results = value.get("pendingRecognitionResults")
    interaction_ids = value.get("interactionMessageIds")
    database_note = normalize_database_note(value.get("databaseNote"))
    if database_note:
        raw_note = value.get("databaseNote") if isinstance(value.get("databaseNote"), dict) else {}
        database_note["mode"] = "plain" if str(raw_note.get("mode") or "rich") == "plain" else "rich"
    return {
        "id": str(value.get("id") or ""),
        "status": str(value.get("status") or "draft"),
        "text": str(value.get("text") or ""),
        "caption": str(value.get("caption") or value.get("text") or ""),
        "linkCount": int(value.get("linkCount") or 0),
        "sourceLabel": str(value.get("sourceLabel") or "投稿"),
        "ownerChatId": safe_int(value.get("ownerChatId")),
        "ownerUserId": safe_int(value.get("ownerUserId")),
        "routeOwnerUserId": safe_int(value.get("routeOwnerUserId")),
        "sourceMessageId": safe_int(value.get("sourceMessageId")),
        "previewMessageId": safe_int(value.get("previewMessageId")),
        "interactionMessageIds": [safe_int(item) for item in interaction_ids if safe_int(item) > 0] if isinstance(interaction_ids, list) else [],
        "pendingEdit": str(value.get("pendingEdit") or ""),
        "pendingRecognitionResults": pending_results if isinstance(pending_results, list) else [],
        "sent": bool(value.get("sent")),
        "createdAt": str(value.get("createdAt") or ""),
        "updatedAt": str(value.get("updatedAt") or ""),
        "sentAt": str(value.get("sentAt") or ""),
        "sentCount": int(value.get("sentCount") or 0),
        "publishedAt": str(value.get("publishedAt") or ""),
        "publishedMessageId": safe_int(value.get("publishedMessageId")),
        "publishedSeedMessageIds": [safe_int(item) for item in value.get("publishedSeedMessageIds") or [] if safe_int(item) > 0] if isinstance(value.get("publishedSeedMessageIds"), list) else [],
        "channelId": str(value.get("channelId") or ""),
        "channelTitle": str(value.get("channelTitle") or ""),
        "channelChatId": str(value.get("channelChatId") or ""),
        "routeChannelId": str(value.get("routeChannelId") or ""),
        "routeChannelTitle": str(value.get("routeChannelTitle") or ""),
        "routeChannelChatId": str(value.get("routeChannelChatId") or ""),
        "media": value.get("media") if isinstance(value.get("media"), dict) else {},
        "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
        "share": value.get("share") if isinstance(value.get("share"), dict) else {},
        "inspection": value.get("inspection") if isinstance(value.get("inspection"), dict) else {},
        "documents": normalize_submission_documents(value.get("documents") or []),
        **({"databaseNote": database_note} if database_note else {}),
    }


# ---------------------------------------------------------------------------
# Telegram 草稿按钮回调与后续输入（自旧版原样恢复；入口见 main.py 的 telegram_callback_polling_loop）
# ---------------------------------------------------------------------------
async def handle_submission_callback(store: SessionStore, bot_token: str, config: Dict[str, Any], callback: Dict[str, Any]) -> Dict[str, Any]:
    data = str(callback.get("data") or "")
    callback_id = str(callback.get("id") or "")
    if not data.startswith("sub:"):
        return {"handled": False, "reason": "unsupported_callback"}
    user = callback.get("from") if isinstance(callback.get("from"), dict) else {}
    user_id = safe_int(user.get("id"))
    if not telegram_user_allowed(config, user_id, store):
        await answer_callback_query(bot_token, callback_id, "你没有权限使用这个投稿 Bot。", True)
        return {"handled": True, "reason": "forbidden"}

    _prefix, draft_id, action, raw_value = (data.split(":", 3) + ["", "", "", ""])[:4]
    draft = get_submission_draft(store, draft_id)
    if not draft:
        await answer_callback_query(bot_token, callback_id, "投稿草稿不存在或已过期，请重新投稿", True)
        return {"handled": True, "reason": "draft_not_found"}
    if safe_int(draft.get("ownerUserId")) != user_id:
        await answer_callback_query(bot_token, callback_id, "这不是你的投稿草稿。", True)
        return {"handled": True, "reason": "draft_owner_mismatch"}

    message_id = callback_message_id(callback)
    if action == "noop":
        await answer_callback_query(bot_token, callback_id)
        return {"handled": True, "action": action}

    if action == "edit":
        field = "recognition" if raw_value == "title" else raw_value
        if field not in {"size", "note", "recognition"}:
            await answer_callback_query(bot_token, callback_id, "未知编辑项", True)
            return {"handled": True, "reason": "unknown_edit"}
        draft["pendingEdit"] = field
        draft["pendingRecognitionResults"] = []
        save_submission_draft(store, draft)
        await answer_callback_query(bot_token, callback_id)
        if field == "recognition":
            await delete_telegram_messages(bot_token, safe_int(draft.get("ownerChatId")), draft.get("interactionMessageIds") or [])
            draft["interactionMessageIds"] = []
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), recognition_prompt(draft), parse_mode="HTML", reply_markup=cancel_keyboard(draft))
        elif field == "note":
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), edit_prompt(field, draft, config), parse_mode="HTML", reply_markup={"force_reply": True, "selective": True, "input_field_placeholder": "粘贴并修改当前备注"})
        else:
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), edit_prompt(field, draft, config), reply_markup=cancel_keyboard(draft))
        track_interaction(draft, telegram_message_id(sent))
        save_submission_draft(store, draft)
        return {"handled": True, "action": action, "field": field}

    if action == "picktmdb":
        candidates = draft.get("pendingRecognitionResults") if isinstance(draft.get("pendingRecognitionResults"), list) else []
        index = safe_int(raw_value)
        candidate = candidates[index] if 0 <= index < len(candidates) and isinstance(candidates[index], dict) else None
        if not candidate:
            await answer_callback_query(bot_token, callback_id, "识别结果已过期，请重新更改识别", True)
            return {"handled": True, "reason": "candidate_not_found"}
        apply_media_to_submission_draft(draft, candidate, config, store)
        draft["pendingEdit"] = ""
        draft["pendingRecognitionResults"] = []
        save_share_media_cache(store, str(draft.get("share", {}).get("cleanUrl") or ""), draft.get("media") if isinstance(draft.get("media"), dict) else {}, "manual")
        await delete_telegram_messages(bot_token, safe_int(draft.get("ownerChatId")), draft.get("interactionMessageIds") or [])
        draft["interactionMessageIds"] = []
        save_submission_draft(store, draft)
        await refresh_submission_preview_message(store, bot_token, draft, config, message_id)
        await answer_callback_query(bot_token, callback_id, "已更新识别")
        return {"handled": True, "action": action}

    if action == "cancel":
        draft["pendingEdit"] = ""
        draft["pendingRecognitionResults"] = []
        await delete_telegram_messages(bot_token, safe_int(draft.get("ownerChatId")), draft.get("interactionMessageIds") or [])
        draft["interactionMessageIds"] = []
        save_submission_draft(store, draft)
        await answer_callback_query(bot_token, callback_id, "已取消")
        return {"handled": True, "action": action}

    if action == "channel":
        await answer_callback_query(bot_token, callback_id)
        try:
            await edit_telegram_reply_markup(bot_token, safe_int(draft.get("ownerChatId")), message_id or safe_int(draft.get("previewMessageId")), channel_keyboard(draft, store))
        except Exception:
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), "请选择发布频道：", reply_markup=channel_keyboard(draft, store))
            track_interaction(draft, telegram_message_id(sent))
            save_submission_draft(store, draft)
        return {"handled": True, "action": action}

    if action == "setch":
        channels = submission_channel_candidates(store, user_id)
        index = safe_int(raw_value)
        candidate = channels[index] if 0 <= index < len(channels) else None
        channel = candidate.get("channel") if isinstance(candidate, dict) and isinstance(candidate.get("channel"), dict) else None
        if not channel or not candidate:
            await answer_callback_query(bot_token, callback_id, "频道配置已变化，请重新打开频道选择", True)
            return {"handled": True, "reason": "channel_not_found"}
        route_owner_user_id = safe_int(candidate.get("ownerUserId"))
        if not store.channel_user_allowed(route_owner_user_id, str(channel.get("id") or ""), user_id):
            await answer_callback_query(bot_token, callback_id, "你没有权限使用此频道", True)
            return {"handled": True, "reason": "channel_not_allowed"}
        draft["routeOwnerUserId"] = route_owner_user_id
        draft["channelId"] = str(channel.get("id") or "")
        draft["channelTitle"] = str(channel.get("title") or "")
        draft["channelChatId"] = str(channel.get("chatId") or "")
        refresh_submission_caption(draft, config, store)
        save_submission_draft(store, draft)
        await refresh_submission_preview_message(store, bot_token, draft, config, message_id)
        await answer_callback_query(bot_token, callback_id)
        return {"handled": True, "action": action}

    if action == "back":
        await refresh_submission_preview_message(store, bot_token, draft, config, message_id)
        await answer_callback_query(bot_token, callback_id)
        return {"handled": True, "action": action}

    if action == "publish":
        result = await publish_submission_draft(store, bot_token, config, draft, callback_id, message_id, acting_user_id=user_id)
        return {"handled": True, **result}

    await answer_callback_query(bot_token, callback_id, "未知操作", True)
    return {"handled": True, "reason": "unknown_action"}


async def handle_pending_submission_input(store: SessionStore, bot_token: str, config: Dict[str, Any], draft: Dict[str, Any], text: str, source_message_id: int) -> Dict[str, Any]:
    field = str(draft.get("pendingEdit") or "")
    if field == "recognition":
        candidates = await find_submission_recognition_candidates(config, text, 6)
        draft["pendingRecognitionResults"] = candidates
        save_submission_draft(store, draft)
        if candidates:
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), recognition_results_text(text), parse_mode="HTML", reply_markup=recognition_results_keyboard(draft))
        else:
            sent = await send_telegram_text(bot_token, safe_int(draft.get("ownerChatId")), f"没有找到 “{text.strip()}” 的 TMDB 结果，请换关键词或发送 movie:ID / tv:ID。", reply_markup=cancel_keyboard(draft))
        track_interaction(draft, source_message_id, telegram_message_id(sent))
        save_submission_draft(store, draft)
        return {"handled": True, "action": "recognition_search", "candidateCount": len(candidates)}

    if field == "size":
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        metadata["size"] = text.strip()
        draft["metadata"] = metadata
    elif field == "note":
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        metadata["note"] = text.strip()
        draft["metadata"] = metadata
    else:
        return {"handled": False, "reason": "no_pending_edit"}

    draft["pendingEdit"] = ""
    refresh_submission_caption(draft, config, store)
    saved = save_submission_draft(store, draft)
    reply = await send_telegram_text(bot_token, safe_int(saved.get("ownerChatId")), "已更新投稿参数。")
    track_interaction(saved, source_message_id, telegram_message_id(reply))
    save_submission_draft(store, saved)
    await refresh_submission_preview_message(store, bot_token, saved, config)
    return {"handled": True, "action": "edit", "field": field}


async def refresh_submission_preview_message(store: SessionStore, bot_token: str, draft: Dict[str, Any], config: Dict[str, Any], callback_message_id_value: int = 0) -> Dict[str, Any]:
    refresh_submission_caption(draft, config, store)
    chat_id = safe_int(draft.get("ownerChatId"))
    message_id = callback_message_id_value or safe_int(draft.get("previewMessageId"))
    if message_id:
        try:
            photo = media_photo(draft)
            if photo:
                await edit_telegram_message_media(bot_token, chat_id, message_id, photo, str(draft.get("caption") or ""), build_submission_preview_markup(draft, config, store))
            else:
                await edit_telegram_message_text(bot_token, chat_id, message_id, str(draft.get("caption") or ""), parse_mode="HTML", reply_markup=build_submission_preview_markup(draft, config, store))
            draft["previewMessageId"] = message_id
            return save_submission_draft(store, draft)
        except Exception:
            try:
                await edit_telegram_message_caption(bot_token, chat_id, message_id, str(draft.get("caption") or ""), build_submission_preview_markup(draft, config, store))
                draft["previewMessageId"] = message_id
                return save_submission_draft(store, draft)
            except Exception:
                await delete_telegram_messages(bot_token, chat_id, [message_id])

    preview = await send_submission_preview_result(bot_token, chat_id, draft, config)
    draft["previewMessageId"] = int(preview.get("firstMessageId") or 0)
    if int(preview.get("sentCount") or 0) > 0:
        draft["sent"] = True
        draft["sentAt"] = utc_now_iso()
        draft["sentCount"] = int(preview.get("sentCount") or 0)
    return save_submission_draft(store, draft)


def apply_media_to_submission_draft(draft: Dict[str, Any], media: Dict[str, Any], config: Dict[str, Any], store: "SessionStore") -> None:
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    draft["media"] = media
    metadata["tmdbId"] = media.get("tmdbId")
    metadata["title"] = media.get("title") or metadata.get("title")
    metadata["year"] = media.get("year") or metadata.get("year")
    metadata["mediaType"] = media.get("mediaType") or metadata.get("mediaType")
    draft["metadata"] = fill_submission_metadata(metadata, media, draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {})
    refresh_submission_caption(draft, config, store)


def track_interaction(draft: Dict[str, Any], *ids: int) -> None:
    existing = {safe_int(value) for value in draft.get("interactionMessageIds") or [] if safe_int(value) > 0}
    source_id = safe_int(draft.get("sourceMessageId"))
    for value in ids:
        message_id = safe_int(value)
        if message_id and message_id != source_id:
            existing.add(message_id)
    draft["interactionMessageIds"] = sorted(existing)


def telegram_user_allowed(config: Dict[str, Any], user_id: int, store: Optional["SessionStore"] = None) -> bool:
    return telegram_submission_allowed(config, user_id, store)


def callback_message_id(callback: Dict[str, Any]) -> int:
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    return safe_int(message.get("message_id"))


def channel_keyboard(draft: Dict[str, Any], store: "SessionStore") -> Dict[str, Any]:
    user_id = safe_int(draft.get("ownerUserId"))
    channels = submission_channel_candidates(store, user_id)
    rows = []
    for index, candidate in enumerate(channels):
        channel = candidate.get("channel") if isinstance(candidate, dict) else {}
        selected = str(draft.get("channelId") or "") == str(channel.get("id") or "") and safe_int(draft.get("routeOwnerUserId")) == safe_int(candidate.get("ownerUserId"))
        prefix = "✅ " if selected else ""
        rows.append([{"text": f"{prefix}{channel.get('title') or channel.get('id') or '频道'}", "callback_data": f"sub:{draft.get('id')}:setch:{index}"}])
    rows.append([{"text": "返回", "callback_data": f"sub:{draft.get('id')}:back"}])
    return {"inline_keyboard": rows}


def cancel_keyboard(draft: Dict[str, Any]) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": "取消", "callback_data": f"sub:{draft.get('id')}:cancel"}]]}


def recognition_prompt(draft: Dict[str, Any]) -> str:
    media = draft.get("media") if isinstance(draft.get("media"), dict) else {}
    title = str(media.get("title") or "未识别媒体")
    year = str(media.get("year") or "")
    display = title if not year or f"({year})" in title else f"{title} ({year})"
    tmdb_id = media.get("tmdbId")
    return "\n".join(
        [
            "🔍 <b>更改识别</b>",
            "",
            f"<blockquote>{escape(display)}{f' {{tmdb-{tmdb_id}}}' if tmdb_id else ''}</blockquote>",
            "",
            "请直接发送下面任一格式：",
            "• <code>movie:363093</code> 指定电影 ID",
            "• <code>tv:12345</code> 指定剧集 ID",
            "• <code>363093</code> 纯 TMDB ID",
            "• <code>达顿牧场 2026</code> 搜索关键词",
        ]
    )


def recognition_results_text(query: str) -> str:
    return f"🔍 <b>选择识别结果</b>\n\n搜索：<code>{escape(str(query or '').strip())}</code>"


def recognition_results_keyboard(draft: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for index, item in enumerate((draft.get("pendingRecognitionResults") or [])[:6]):
        if not isinstance(item, dict):
            continue
        icon = "📺剧集" if item.get("mediaType") == "tv" else "🎬电影"
        year = f" ({item.get('year')})" if item.get("year") else ""
        rows.append([{"text": f"{index + 1}. [{icon}] {item.get('title') or '未命名'}{year}", "callback_data": f"sub:{draft.get('id')}:picktmdb:{index}"}])
    rows.append([{"text": "取消", "callback_data": f"sub:{draft.get('id')}:cancel"}])
    return {"inline_keyboard": rows}


def edit_prompt(field: str, draft: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> str:
    if field == "size":
        return "请直接回复新的资源大小，例如：192.10GB"
    note = current_draft_note(draft, config)
    parts = ["请直接回复新的备注。"]
    if note:
        parts.extend(["", "当前备注：", f"<code>{escape(note)}</code>"])
    return "\n".join(parts)


def current_draft_note(draft: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> str:
    if not draft:
        return ""
    metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
    note = combined_submission_note(metadata.get("note"), draft.get("databaseNote"))
    if note:
        return note
    inspection = draft.get("inspection") if isinstance(draft.get("inspection"), dict) else {}
    return build_submission_resource_name(metadata, inspection.get("fileNames") or [], config or {})


async def edit_telegram_reply_markup(bot_token: str, chat_id: Any, message_id: int, reply_markup: Dict[str, Any]) -> Dict[str, Any]:
    return await telegram_post(bot_token, "editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})


async def edit_telegram_message_text(bot_token: str, chat_id: Any, message_id: int, text: str, parse_mode: Optional[str] = "HTML", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": False}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_post(bot_token, "editMessageText", payload)


async def edit_telegram_message_media(bot_token: str, chat_id: Any, message_id: int, photo: str, caption: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "media": {"type": "photo", "media": photo, "caption": caption, "parse_mode": "HTML"},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await telegram_post(bot_token, "editMessageMedia", payload)

def find_pending_submission_draft(store: SessionStore, chat_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    for draft in list_submission_drafts(store, 20):
        if str(draft.get("status") or "draft") != "draft":
            continue
        if safe_int(draft.get("ownerChatId")) == chat_id and safe_int(draft.get("ownerUserId")) == user_id and str(draft.get("pendingEdit") or ""):
            return draft
    return None


def telegram_entity_urls(message: Dict[str, Any], text: str) -> List[str]:
    entities: List[Dict[str, Any]] = []
    if isinstance(message.get("entities"), list):
        entities.extend(item for item in message.get("entities") or [] if isinstance(item, dict))
    if isinstance(message.get("caption_entities"), list):
        entities.extend(item for item in message.get("caption_entities") or [] if isinstance(item, dict))
    urls: List[str] = []
    for entity in entities:
        if entity.get("type") == "text_link" and entity.get("url"):
            urls.append(str(entity.get("url") or ""))
            continue
        if entity.get("type") == "url":
            offset = safe_int(entity.get("offset"))
            length = safe_int(entity.get("length"))
            if length > 0:
                urls.append(text[offset : offset + length])
    return urls


def telegram_message_text(message: Dict[str, Any]) -> str:
    text = str(message.get("text") or message.get("caption") or "")
    parts = [text, *telegram_entity_urls(message, text)]
    return "\n".join(dict.fromkeys(part.strip() for part in parts if str(part or "").strip()))
