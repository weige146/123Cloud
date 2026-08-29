from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .defaults import DEFAULT_SUBMISSION_CONFIG, DEFAULT_USER_CHANNELS, DEFAULT_USER_ROUTING


PBKDF2_ITERATIONS = 180_000
ADMIN_CONFIG_KEY = "admin_config"
PAN123_SESSION_KEY = "pan123_session"
SUBMISSION_CONFIG_KEY = "submission_config"

DEFAULT_ADMIN_CONFIG = {
    "gatewayName": "123 Cloud Gateway",
    "pan123ClientMode": "web",
    "pan123OpenApiClientId": "",
    "pan123OpenApiClientSecret": "",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_file = data_dir / "cloud123.db"
        self.session_file = data_dir / "pan123-session.json"
        self.config_file = data_dir / "config.json"
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_initialized()

    def read_session(self) -> Optional[Dict[str, Any]]:
        raw = self.read_value(PAN123_SESSION_KEY)
        return raw if isinstance(raw, dict) else None

    def write_session(self, session: Dict[str, Any]) -> None:
        self.write_value(PAN123_SESSION_KEY, session)

    def clear_session(self) -> None:
        self.delete_value(PAN123_SESSION_KEY)

    def read_config(self) -> Dict[str, Any]:
        raw = self.read_value(ADMIN_CONFIG_KEY)
        return merge_dicts(DEFAULT_ADMIN_CONFIG, raw if isinstance(raw, dict) else {})

    def write_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        next_config = self.read_config()
        next_config.update(config)
        next_config["gatewayName"] = str(next_config.get("gatewayName") or "123 Cloud Gateway").strip() or "123 Cloud Gateway"
        next_config["updatedAt"] = utc_now_iso()
        self.write_value(ADMIN_CONFIG_KEY, next_config)
        return next_config

    def read_submission_config(self) -> Dict[str, Any]:
        raw = self.read_value(SUBMISSION_CONFIG_KEY)
        raw_config = raw if isinstance(raw, dict) else {}
        has_new_authorization = "telegramAdminUserIds" in raw_config or "channelOwnerUserIds" in raw_config
        return sanitize_submission_config(merge_dicts(DEFAULT_SUBMISSION_CONFIG, raw_config), migrate_legacy_authorization=not has_new_authorization)

    def write_submission_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        input_config = config if isinstance(config, dict) else {}
        # Preserve settings added after an older client was released.  Merging
        # only with defaults made a partial/stale save reset them to empty.
        existing = self.read_value(SUBMISSION_CONFIG_KEY)
        existing_config = existing if isinstance(existing, dict) else {}
        has_new_authorization = (
            "telegramAdminUserIds" in input_config
            or "channelOwnerUserIds" in input_config
            or "telegramAdminUserIds" in existing_config
            or "channelOwnerUserIds" in existing_config
        )
        base_config = merge_dicts(DEFAULT_SUBMISSION_CONFIG, existing_config)
        next_config = sanitize_submission_config(merge_dicts(base_config, input_config), migrate_legacy_authorization=not has_new_authorization)
        next_config["updatedAt"] = utc_now_iso()
        self.write_value(SUBMISSION_CONFIG_KEY, next_config)
        self._migrate_user_channel_configs()
        self._backfill_submission_publication_owners()
        return self.read_submission_config()

    # ── 用户级频道配置 ──

    def has_user_channel_config(self, user_id: int) -> bool:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM user_channel_configs WHERE owner_user_id = ?", (int(user_id or 0),)
            ).fetchone()
        return bool(row)

    def read_user_channel_config(self, user_id: int) -> Dict[str, Any]:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT channels_json, routing_json, updated_at, created_at FROM user_channel_configs WHERE owner_user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {
                "ownerUserId": user_id,
                "channels": copy.deepcopy(DEFAULT_USER_CHANNELS),
                "routing": copy.deepcopy(DEFAULT_USER_ROUTING),
                "updatedAt": "",
                "createdAt": "",
            }
        try:
            channels = json.loads(str(row["channels_json"]))
        except json.JSONDecodeError:
            channels = copy.deepcopy(DEFAULT_USER_CHANNELS)
        channels = channels if isinstance(channels, list) else copy.deepcopy(DEFAULT_USER_CHANNELS)
        channels = self._channels_with_grants(int(user_id), channels)
        try:
            routing = json.loads(str(row["routing_json"]))
        except json.JSONDecodeError:
            routing = copy.deepcopy(DEFAULT_USER_ROUTING)
        return {
            "ownerUserId": user_id,
            "channels": channels,
            "routing": routing if isinstance(routing, dict) else copy.deepcopy(DEFAULT_USER_ROUTING),
            "updatedAt": str(row["updated_at"] or ""),
            "createdAt": str(row["created_at"] or ""),
        }

    def write_user_channel_config(self, user_id: int, config: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_initialized()
        now = utc_now_iso()
        channels = config.get("channels") if isinstance(config.get("channels"), list) else copy.deepcopy(DEFAULT_USER_CHANNELS)
        routing = config.get("routing") if isinstance(config.get("routing"), dict) else copy.deepcopy(DEFAULT_USER_ROUTING)
        normalized_channels, grants = self._normalize_channels_and_grants(channels)
        channels_json = json.dumps(normalized_channels, ensure_ascii=False)
        routing_json = json.dumps(routing, ensure_ascii=False)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM user_channel_configs WHERE owner_user_id = ?",
                (user_id,),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE user_channel_configs SET channels_json = ?, routing_json = ?, updated_at = ? WHERE owner_user_id = ?",
                    (channels_json, routing_json, now, user_id),
                )
                created_at = str(existing["created_at"] or "")
            else:
                connection.execute(
                    "INSERT INTO user_channel_configs (owner_user_id, channels_json, routing_json, updated_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, channels_json, routing_json, now, now),
                )
                created_at = now
            connection.execute("DELETE FROM channel_access_grants WHERE owner_user_id = ?", (int(user_id),))
            for channel_id, member_user_id in grants:
                connection.execute(
                    "INSERT OR IGNORE INTO channel_access_grants(owner_user_id, channel_id, member_user_id, created_at) VALUES (?, ?, ?, ?)",
                    (int(user_id), channel_id, member_user_id, now),
                )
        return {
            "ownerUserId": user_id,
            "channels": self._channels_with_grants(int(user_id), normalized_channels),
            "routing": routing,
            "updatedAt": now,
            "createdAt": created_at,
        }

    def list_users_with_channel_configs(self) -> List[Dict[str, Any]]:
        self._ensure_initialized()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_user_id, channels_json, routing_json, updated_at, created_at FROM user_channel_configs ORDER BY owner_user_id"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            user_id = int(row["owner_user_id"])
            try:
                channels = json.loads(str(row["channels_json"]))
            except json.JSONDecodeError:
                channels = []
            channels = self._channels_with_grants(user_id, channels if isinstance(channels, list) else [])
            try:
                routing = json.loads(str(row["routing_json"]))
            except json.JSONDecodeError:
                routing = {}
            result.append({
                "ownerUserId": user_id,
                "channels": channels,
                "routing": routing if isinstance(routing, dict) else {},
                "channelCount": len(channels) if isinstance(channels, list) else 0,
                "updatedAt": str(row["updated_at"] or ""),
                "createdAt": str(row["created_at"] or ""),
            })
        return result

    def delete_user_channel_config(self, user_id: int) -> bool:
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("DELETE FROM channel_access_grants WHERE owner_user_id = ?", (int(user_id),))
            cursor = connection.execute(
                "DELETE FROM user_channel_configs WHERE owner_user_id = ?",
                (user_id,),
            )
            return cursor.rowcount > 0

    def granted_submission_channels(self, user_id: int) -> List[Dict[str, Any]]:
        """Return only the enabled channels this Telegram user may submit to."""
        self._ensure_initialized()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.owner_user_id, c.channels_json
                FROM user_channel_configs c
                JOIN channel_access_grants g ON g.owner_user_id = c.owner_user_id
                WHERE g.member_user_id = ?
                ORDER BY c.owner_user_id, g.channel_id
                """,
                (int(user_id or 0),),
            ).fetchall()
            grants = connection.execute(
                "SELECT owner_user_id, channel_id FROM channel_access_grants WHERE member_user_id = ?",
                (int(user_id or 0),),
            ).fetchall()
        wanted = {(int(row["owner_user_id"]), str(row["channel_id"])) for row in grants}
        result: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            owner_user_id = int(row["owner_user_id"])
            try:
                channels = json.loads(str(row["channels_json"]))
            except json.JSONDecodeError:
                channels = []
            for channel in channels if isinstance(channels, list) else []:
                channel_id = str(channel.get("id") or "").strip() if isinstance(channel, dict) else ""
                key = (owner_user_id, channel_id)
                if not channel_id or key not in wanted or key in seen or channel.get("enabled") is False:
                    continue
                seen.add(key)
                result.append({"ownerUserId": owner_user_id, "channel": dict(channel)})
        return result

    def channel_user_allowed(self, owner_user_id: int, channel_id: str, user_id: int) -> bool:
        owner_user_id = int(owner_user_id or 0)
        if owner_user_id <= 0 or not str(channel_id or "").strip() or int(user_id or 0) <= 0:
            return False
        if owner_user_id == int(user_id):
            return True
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM channel_access_grants WHERE owner_user_id = ? AND channel_id = ? AND member_user_id = ?",
                (owner_user_id, str(channel_id).strip(), int(user_id)),
            ).fetchone()
        return bool(row)

    def channel_owner_count(self, chat_id: str) -> int:
        return len(self._channel_owners(chat_id))

    def _channel_owners(self, chat_id: str) -> set[int]:
        wanted = str(chat_id or "").strip()
        if not wanted:
            return set()
        owners = set()
        for item in self.list_users_with_channel_configs():
            for channel in item.get("channels") or []:
                if isinstance(channel, dict) and str(channel.get("chatId") or "").strip() == wanted:
                    owner = int(item.get("ownerUserId") or 0)
                    if owner > 0:
                        owners.add(owner)
        return owners

    def _channels_with_grants(self, owner_user_id: int, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self._ensure_initialized()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id, member_user_id FROM channel_access_grants WHERE owner_user_id = ? ORDER BY member_user_id",
                (int(owner_user_id),),
            ).fetchall()
        grants: Dict[str, List[int]] = {}
        for row in rows:
            grants.setdefault(str(row["channel_id"]), []).append(int(row["member_user_id"]))
        result = []
        for item in channels:
            if not isinstance(item, dict):
                continue
            channel = {key: value for key, value in item.items() if key != "allowedUserIds"}
            channel["allowedUserIds"] = grants.get(str(channel.get("id") or "").strip(), [])
            result.append(channel)
        return result

    def _normalize_channels_and_grants(self, channels: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[tuple[str, int]]]:
        normalized: List[Dict[str, Any]] = []
        grants: List[tuple[str, int]] = []
        seen_ids = set()
        for item in channels:
            if not isinstance(item, dict):
                continue
            channel_id = str(item.get("id") or "").strip()
            if not channel_id or channel_id in seen_ids:
                raise ValueError("频道 ID 不能为空且同一用户内不能重复")
            seen_ids.add(channel_id)
            channel = {key: value for key, value in item.items() if key != "allowedUserIds"}
            channel["id"] = channel_id
            normalized.append(channel)
            for value in item.get("allowedUserIds") or []:
                member_user_id = optional_int(value)
                if member_user_id and member_user_id > 0:
                    grants.append((channel_id, member_user_id))
        return normalized, sorted(set(grants))

    def read_value(self, key: str) -> Optional[Any]:
        self._ensure_initialized()
        return self._read_kv_value(key)

    def _read_kv_value(self, key: str) -> Optional[Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(str(row["value"]))
        except json.JSONDecodeError:
            return None

    def write_value(self, key: str, value: Any) -> None:
        self._ensure_initialized()
        self._write_kv_value(key, value)

    def _write_kv_value(self, key: str, value: Any) -> None:
        now = utc_now_iso()
        payload = json.dumps(value, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kv(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, payload, now),
            )

    def delete_value(self, key: str) -> None:
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("DELETE FROM kv WHERE key = ?", (key,))

    def fail_unfinished_tasks(self, message: str) -> int:
        self._ensure_initialized()
        now = utc_now_iso()
        text = str(message or "后台任务已取消，请重新提交").strip()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'failed', message = ?, error = '', finished_at = ?, updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (text, now, now),
            )
        return int(cursor.rowcount or 0)

    def record_submission_publication(self, publication: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_initialized()
        now = utc_now_iso()
        publication_id = str(publication.get("id") or os.urandom(16).hex())
        row = {
            "id": publication_id,
            "channelChatId": str(publication.get("channelChatId") or ""),
            "channelId": str(publication.get("channelId") or ""),
            "channelTitle": str(publication.get("channelTitle") or ""),
            "routeOwnerUserId": optional_int(publication.get("routeOwnerUserId")),
            "messageId": int(publication.get("messageId") or 0),
            "seedMessageIds": [
                int(message_id)
                for value in publication.get("seedMessageIds") or []
                for message_id in [optional_int(value)]
                if message_id and message_id > 0
            ],
            "identityKey": str(publication.get("identityKey") or ""),
            "mediaType": str(publication.get("mediaType") or ""),
            "tmdbId": optional_int(publication.get("tmdbId")),
            "titleKey": str(publication.get("titleKey") or ""),
            "title": str(publication.get("title") or ""),
            "resourceName": str(publication.get("resourceName") or ""),
            "shareUrl": str(publication.get("shareUrl") or ""),
            "fastLink": bool(publication.get("fastLink")),
            "draftId": str(publication.get("draftId") or ""),
            "publishedAt": str(publication.get("publishedAt") or now),
            "deletedAt": str(publication.get("deletedAt") or ""),
            "createdAt": str(publication.get("createdAt") or now),
            "updatedAt": now,
        }
        if not row["routeOwnerUserId"]:
            owners = self._channel_owners(str(row["channelChatId"]))
            if len(owners) == 1:
                row["routeOwnerUserId"] = next(iter(owners))
        if not row["channelChatId"] or not row["messageId"] or not row["identityKey"]:
            return row
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO submission_publications(
                  id, channel_chat_id, channel_id, channel_title, route_owner_user_id, message_id, identity_key,
                  media_type, tmdb_id, title_key, title, resource_name, share_url, fast_link,
                  draft_id, published_at, deleted_at, created_at, updated_at, seed_message_ids_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  channel_chat_id = excluded.channel_chat_id,
                  channel_id = excluded.channel_id,
                  channel_title = excluded.channel_title,
                  route_owner_user_id = excluded.route_owner_user_id,
                  message_id = excluded.message_id,
                  seed_message_ids_json = excluded.seed_message_ids_json,
                  identity_key = excluded.identity_key,
                  media_type = excluded.media_type,
                  tmdb_id = excluded.tmdb_id,
                  title_key = excluded.title_key,
                  title = excluded.title,
                  resource_name = excluded.resource_name,
                  share_url = excluded.share_url,
                  fast_link = excluded.fast_link,
                  draft_id = excluded.draft_id,
                  published_at = excluded.published_at,
                  deleted_at = excluded.deleted_at,
                  updated_at = excluded.updated_at
                """,
                (
                    row["id"],
                    row["channelChatId"],
                    row["channelId"],
                    row["channelTitle"],
                    row["routeOwnerUserId"],
                    row["messageId"],
                    row["identityKey"],
                    row["mediaType"],
                    row["tmdbId"],
                    row["titleKey"],
                    row["title"],
                    row["resourceName"],
                    row["shareUrl"],
                    1 if row["fastLink"] else 0,
                    row["draftId"],
                    row["publishedAt"],
                    row["deletedAt"] or None,
                    row["createdAt"],
                    row["updatedAt"],
                    json.dumps(row["seedMessageIds"], ensure_ascii=False),
                ),
            )
        return row

    def find_submission_publications(self, channel_chat_id: str, identity_key: str, before_message_id: int = 0, limit: int = 20, route_owner_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        self._ensure_initialized()
        channel_chat_id = str(channel_chat_id or "").strip()
        identity_key = str(identity_key or "").strip()
        if not channel_chat_id or not identity_key:
            return []
        limit = max(1, min(int(limit or 20), 100))
        params: List[Any] = [channel_chat_id, identity_key]
        owner_clause = ""
        if route_owner_user_id is not None:
            owner_clause = "AND route_owner_user_id = ?"
            params.append(int(route_owner_user_id))
        before_clause = ""
        if int(before_message_id or 0) > 0:
            before_clause = "AND message_id < ?"
            params.append(int(before_message_id))
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM submission_publications
                WHERE channel_chat_id = ?
                  AND identity_key = ?
                  AND deleted_at IS NULL
                  {owner_clause}
                  {before_clause}
                ORDER BY published_at DESC, message_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [submission_publication_from_row(row) for row in rows]

    def find_submission_publications_by_tmdb_share(self, channel_chat_id: str, tmdb_id: int, before_message_id: int = 0, limit: int = 20, route_owner_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """按 TMDB ID 兜底查询发布记录，用于多版本场景下匹配相同内容的旧帖。"""
        self._ensure_initialized()
        channel_chat_id = str(channel_chat_id or "").strip()
        tmdb_id = int(tmdb_id or 0)
        if not channel_chat_id or tmdb_id <= 0:
            return []
        limit = max(1, min(int(limit or 20), 100))
        params: List[Any] = [channel_chat_id, tmdb_id]
        owner_clause = ""
        if route_owner_user_id is not None:
            owner_clause = "AND route_owner_user_id = ?"
            params.append(int(route_owner_user_id))
        before_clause = ""
        if int(before_message_id or 0) > 0:
            before_clause = "AND message_id < ?"
            params.append(int(before_message_id))
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM submission_publications
                WHERE channel_chat_id = ?
                  AND tmdb_id = ?
                  AND deleted_at IS NULL
                  {owner_clause}
                  {before_clause}
                ORDER BY published_at DESC, message_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [submission_publication_from_row(row) for row in rows]

    def mark_submission_publications_deleted(self, publication_ids: List[str]) -> None:
        self._ensure_initialized()
        ids = [str(value or "").strip() for value in publication_ids if str(value or "").strip()]
        if not ids:
            return
        now = utc_now_iso()
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE submission_publications
                SET deleted_at = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [now, now, *ids],
            )

    def save_transfer_task(self, task: Dict[str, Any]) -> None:
        self._ensure_initialized()
        now = utc_now_iso()
        task_id = str(task.get("id") or os.urandom(16).hex())
        files = task.get("files")
        logs = task.get("logs")
        notice_ids = task.get("transferNoticeMessageIds")
        files_json = json.dumps(files, ensure_ascii=False) if isinstance(files, list) else None
        logs_json = json.dumps(logs, ensure_ascii=False) if isinstance(logs, list) else None
        notice_ids_json = json.dumps(notice_ids, ensure_ascii=False) if isinstance(notice_ids, list) else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transfer_tasks(
                  id, kind, source, source_text, chat_id, user_id, message_id,
                  share_url, share_code, receive_code, title, status,
                  total_files, done_files, files_json, logs_json,
                  created_at, updated_at, started_at, finished_at, error,
                  transfer_notice_chat_id, transfer_notice_message_ids_json, transfer_final_message_id,
                  remote_task_id, target_dir_id, share_owner_user_id
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  kind = excluded.kind,
                  source = excluded.source,
                  source_text = excluded.source_text,
                  chat_id = excluded.chat_id,
                  user_id = excluded.user_id,
                  message_id = excluded.message_id,
                  share_url = excluded.share_url,
                  share_code = excluded.share_code,
                  receive_code = excluded.receive_code,
                  title = excluded.title,
                  status = excluded.status,
                  total_files = excluded.total_files,
                  done_files = excluded.done_files,
                  files_json = excluded.files_json,
                  logs_json = excluded.logs_json,
                  updated_at = excluded.updated_at,
                  started_at = excluded.started_at,
                  finished_at = excluded.finished_at,
                  error = excluded.error,
                  transfer_notice_chat_id = excluded.transfer_notice_chat_id,
                  transfer_notice_message_ids_json = excluded.transfer_notice_message_ids_json,
                  transfer_final_message_id = excluded.transfer_final_message_id,
                  remote_task_id = excluded.remote_task_id,
                  target_dir_id = excluded.target_dir_id,
                  share_owner_user_id = excluded.share_owner_user_id
                """,
                (
                    task_id,
                    str(task.get("kind") or "pan115_share"),
                    str(task.get("source") or ""),
                    str(task.get("sourceText") or ""),
                    str(task.get("chatId") or ""),
                    optional_int(task.get("userId")),
                    optional_int(task.get("messageId")),
                    str(task.get("shareUrl") or ""),
                    str(task.get("shareCode") or ""),
                    str(task.get("receiveCode") or ""),
                    str(task.get("title") or ""),
                    str(task.get("status") or "queued"),
                    max(0, int(task.get("totalFiles") or 0)),
                    max(0, int(task.get("doneFiles") or 0)),
                    files_json,
                    logs_json,
                    str(task.get("createdAt") or now),
                    str(task.get("updatedAt") or now),
                    str(task.get("startedAt")) if task.get("startedAt") else None,
                    str(task.get("finishedAt")) if task.get("finishedAt") else None,
                    str(task.get("error") or ""),
                    str(task.get("transferNoticeChatId") or ""),
                    notice_ids_json,
                    optional_int(task.get("transferFinalMessageId")),
                    optional_int(task.get("remoteTaskId")),
                    str(task.get("targetDirId") or ""),
                    optional_int(task.get("shareOwnerUserId")),
                ),
            )

    def get_transfer_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM transfer_tasks WHERE id = ?", (task_id,)).fetchone()
        return transfer_task_from_row(row) if row else None

    def delete_transfer_task(self, task_id: str) -> bool:
        self._ensure_initialized()
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM transfer_tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0

    def list_transfer_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        self._ensure_initialized()
        limit = max(1, min(int(limit or 100), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transfer_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [transfer_task_from_row(row) for row in rows]

    def next_queued_transfer_task(self, kind: str = "", exclude_kind: str = "") -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        with self._connect() as connection:
            if kind:
                row = connection.execute(
                    "SELECT * FROM transfer_tasks WHERE status = 'queued' AND kind = ? ORDER BY created_at ASC LIMIT 1",
                    (kind,),
                ).fetchone()
            elif exclude_kind:
                row = connection.execute(
                    "SELECT * FROM transfer_tasks WHERE status = 'queued' AND kind != ? ORDER BY created_at ASC LIMIT 1",
                    (exclude_kind,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM transfer_tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
        return transfer_task_from_row(row) if row else None

    def reset_running_transfer_tasks(self) -> int:
        self._ensure_initialized()
        now = utc_now_iso()
        changed = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, files_json, logs_json FROM transfer_tasks WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                files = json_value(row["files_json"], [])
                if not isinstance(files, list):
                    files = []
                reset_files = []
                for file in files:
                    if isinstance(file, dict) and file.get("status") == "running":
                        reset_files.append({**file, "status": "pending", "error": "服务重启后重试"})
                    else:
                        reset_files.append(file)
                logs = json_value(row["logs_json"], [])
                if isinstance(logs, list):
                    logs = [
                        *logs,
                        {"time": now, "level": "warn", "message": "服务重启后自动重新排队"},
                    ][-200:]
                else:
                    logs = [{"time": now, "level": "warn", "message": "服务重启后自动重新排队"}]
                connection.execute(
                    """
                    UPDATE transfer_tasks
                    SET status = 'queued',
                        error = ?,
                        started_at = NULL,
                        updated_at = ?,
                        files_json = ?,
                        logs_json = ?
                    WHERE id = ?
                    """,
                    (
                        "服务重启后自动重新排队",
                        now,
                        json.dumps(reset_files, ensure_ascii=False),
                        json.dumps(logs, ensure_ascii=False),
                        row["id"],
                    ),
                )
                changed += 1
            cursor = connection.execute(
                """
                UPDATE transfer_tasks
                SET status = 'queued', started_at = NULL, updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            changed += cursor.rowcount
        return changed

    def get_transfer_hash(self, sha1: str, size: int) -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transfer_hashes WHERE sha1 = ? AND size = ?", (sha1, size)
            ).fetchone()
        if not row:
            return None
        return {
            "sha1": row["sha1"],
            "size": row["size"],
            "etag": row["etag"],
            "name": row["name"],
            "updatedAt": row["updated_at"],
        }

    def save_transfer_hash(self, sha1: str, size: int, etag: str, name: str = "") -> bool:
        self._ensure_initialized()
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transfer_hashes(sha1, size, etag, name, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(sha1, size) DO UPDATE SET
                  etag = excluded.etag,
                  name = excluded.name,
                  updated_at = excluded.updated_at
                """,
                (sha1, size, etag, name, now),
            )
        return True

    def get_pan123_open_token(self, token_key: str) -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        token_key = str(token_key or "").strip()
        if not token_key:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pan123_open_tokens WHERE token_key = ?", (token_key,)
            ).fetchone()
        if not row:
            return None
        return {
            "accessToken": row["access_token"],
            "expiresAt": float(row["expires_at"]),
            "updatedAt": row["updated_at"],
        }

    def save_pan123_open_token(self, token_key: str, access_token: str, expires_at: float) -> None:
        self._ensure_initialized()
        token_key = str(token_key or "").strip()
        if not token_key or not access_token:
            return
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pan123_open_tokens(token_key, access_token, expires_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(token_key) DO UPDATE SET
                  access_token = excluded.access_token,
                  expires_at = excluded.expires_at,
                  updated_at = excluded.updated_at
                """,
                (token_key, access_token, float(expires_at), now),
            )

    def delete_pan123_open_token(self, token_key: str) -> None:
        self._ensure_initialized()
        token_key = str(token_key or "").strip()
        if not token_key:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM pan123_open_tokens WHERE token_key = ?", (token_key,))

    def credentials_match(self, session: Dict[str, Any], user: str, password: str) -> bool:
        if str(session.get("user") or "") != user:
            return False
        encoded = str(session.get("passwordHash") or "")
        if not encoded:
            return False
        return verify_password(password, encoded)

    def build_session(self, user: str, password: str, token: str, login_uuid: str, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "token": token,
            "user": user,
            "loginUuid": login_uuid,
            "passwordHash": hash_password(password),
            "updatedAt": utc_now_iso(),
            **({"profile": profile} if profile else {}),
        }

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS kv (
                      key TEXT PRIMARY KEY,
                      value TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS submission_publications (
                      id TEXT PRIMARY KEY,
                      channel_chat_id TEXT NOT NULL,
                      channel_id TEXT NOT NULL DEFAULT '',
                      channel_title TEXT NOT NULL DEFAULT '',
                      route_owner_user_id INTEGER,
                      message_id INTEGER NOT NULL,
                      seed_message_ids_json TEXT,
                      identity_key TEXT NOT NULL,
                      media_type TEXT NOT NULL DEFAULT '',
                      tmdb_id INTEGER,
                      title_key TEXT NOT NULL DEFAULT '',
                      title TEXT NOT NULL DEFAULT '',
                      resource_name TEXT NOT NULL DEFAULT '',
                      share_url TEXT NOT NULL DEFAULT '',
                      fast_link INTEGER NOT NULL DEFAULT 0,
                      draft_id TEXT NOT NULL DEFAULT '',
                      published_at TEXT NOT NULL,
                      deleted_at TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                self._ensure_column(connection, "submission_publications", "seed_message_ids_json", "TEXT")
                self._ensure_column(connection, "submission_publications", "route_owner_user_id", "INTEGER")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS transfer_tasks (
                      id TEXT PRIMARY KEY,
                      kind TEXT NOT NULL DEFAULT 'pan115_share',
                      source TEXT NOT NULL DEFAULT '',
                      source_text TEXT NOT NULL DEFAULT '',
                      chat_id TEXT NOT NULL DEFAULT '',
                      user_id INTEGER,
                      message_id INTEGER,
                      share_url TEXT NOT NULL DEFAULT '',
                      share_code TEXT NOT NULL DEFAULT '',
                      receive_code TEXT NOT NULL DEFAULT '',
                      title TEXT NOT NULL DEFAULT '',
                      status TEXT NOT NULL DEFAULT 'queued',
                      total_files INTEGER NOT NULL DEFAULT 0,
                      done_files INTEGER NOT NULL DEFAULT 0,
                      files_json TEXT,
                      logs_json TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      started_at TEXT,
                      finished_at TEXT,
                      error TEXT NOT NULL DEFAULT '',
                      transfer_notice_chat_id TEXT NOT NULL DEFAULT '',
                      transfer_notice_message_ids_json TEXT,
                      transfer_final_message_id INTEGER,
                      remote_task_id INTEGER,
                      target_dir_id TEXT NOT NULL DEFAULT '',
                      share_owner_user_id INTEGER
                    )
                    """
                )
                self._ensure_column(connection, "transfer_tasks", "kind", "TEXT NOT NULL DEFAULT 'pan115_share'")
                self._ensure_column(connection, "transfer_tasks", "remote_task_id", "INTEGER")
                self._ensure_column(connection, "transfer_tasks", "target_dir_id", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(connection, "transfer_tasks", "share_owner_user_id", "INTEGER")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS transfer_hashes (
                      sha1 TEXT NOT NULL,
                      size INTEGER NOT NULL,
                      etag TEXT NOT NULL DEFAULT '',
                      name TEXT NOT NULL DEFAULT '',
                      updated_at TEXT NOT NULL,
                      PRIMARY KEY(sha1, size)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pan123_open_tokens (
                      token_key TEXT PRIMARY KEY,
                      access_token TEXT NOT NULL,
                      expires_at REAL NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_channel_configs (
                      owner_user_id INTEGER NOT NULL PRIMARY KEY,
                      channels_json TEXT NOT NULL DEFAULT '[]',
                      routing_json TEXT NOT NULL DEFAULT '{}',
                      updated_at TEXT NOT NULL,
                      created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS channel_access_grants (
                      owner_user_id INTEGER NOT NULL,
                      channel_id TEXT NOT NULL,
                      member_user_id INTEGER NOT NULL,
                      created_at TEXT NOT NULL,
                      PRIMARY KEY(owner_user_id, channel_id, member_user_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_submission_publications_identity
                    ON submission_publications(channel_chat_id, identity_key, deleted_at, published_at DESC)
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_channel_access_grants_member ON channel_access_grants(member_user_id, owner_user_id, channel_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_submission_publications_route_owner ON submission_publications(channel_chat_id, route_owner_user_id, identity_key, deleted_at, published_at DESC)"
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_submission_publications_message
                    ON submission_publications(channel_chat_id, message_id)
                    """
                )
            self._initialized = True
            self._seed_defaults()
            self._migrate_legacy_json()
            self._migrate_user_channel_configs()
            self._migrate_channel_access_grants()
            self._backfill_submission_publication_owners()

    def _connect(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_file, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _seed_defaults(self) -> None:
        if self.read_value(ADMIN_CONFIG_KEY) is None:
            self.write_value(ADMIN_CONFIG_KEY, {**DEFAULT_ADMIN_CONFIG, "updatedAt": utc_now_iso()})
        if self.read_value(SUBMISSION_CONFIG_KEY) is None:
            self.write_value(SUBMISSION_CONFIG_KEY, merge_dicts(DEFAULT_SUBMISSION_CONFIG, {"updatedAt": utc_now_iso()}))

    def _migrate_user_channel_configs(self) -> None:
        """将旧全局频道配置迁移给频道所有者列表中的首个用户。"""
        with self._connect() as connection:
            existing = connection.execute("SELECT COUNT(*) AS cnt FROM user_channel_configs").fetchone()
        if existing and int(existing["cnt"]) > 0:
            self._clear_legacy_global_channels()
            return
        config = self.read_submission_config()
        global_channels = config.get("channels") or []
        global_routing = config.get("routing") or {}
        # 仅当全局频道有实质性内容（超过默认的 private 频道）时迁移
        if not global_channels or (len(global_channels) == 1 and str(global_channels[0].get("id") or "") == "private" and not str(global_channels[0].get("chatId") or "")):
            return
        allowed_raw = config.get("channelOwnerUserIds") or config.get("allowedUserIds") or []
        allowed_ids = []
        for value in allowed_raw:
            try:
                n = int(value)
                if n > 0:
                    allowed_ids.append(n)
            except (TypeError, ValueError):
                pass
        if not allowed_ids:
            return
        first_user = allowed_ids[0]
        migrated_channels = copy.deepcopy(global_channels)
        # 为每个频道添加 allowedUserIds 默认值
        for ch in migrated_channels:
            if "allowedUserIds" not in ch:
                ch["allowedUserIds"] = []
        migrated_routing = copy.deepcopy(global_routing) if isinstance(global_routing, dict) else copy.deepcopy(DEFAULT_USER_ROUTING)
        self.write_user_channel_config(first_user, {"channels": migrated_channels, "routing": migrated_routing})
        self._clear_legacy_global_channels()

    def _clear_legacy_global_channels(self) -> None:
        raw = self._read_kv_value(SUBMISSION_CONFIG_KEY)
        if not isinstance(raw, dict) or not raw.get("channels"):
            return
        next_config = dict(raw)
        next_config["channels"] = []
        next_config["routing"] = {}
        self._write_kv_value(SUBMISSION_CONFIG_KEY, next_config)

    def _migrate_channel_access_grants(self) -> None:
        """Move the short-lived JSON whitelist representation into the grant table."""
        with self._connect() as connection:
            rows = connection.execute("SELECT owner_user_id, channels_json, routing_json FROM user_channel_configs").fetchall()
        for row in rows:
            try:
                channels = json.loads(str(row["channels_json"]))
            except json.JSONDecodeError:
                channels = []
            if not isinstance(channels, list) or not any(isinstance(item, dict) and "allowedUserIds" in item for item in channels):
                continue
            try:
                routing = json.loads(str(row["routing_json"]))
            except json.JSONDecodeError:
                routing = {}
            self.write_user_channel_config(int(row["owner_user_id"]), {"channels": channels, "routing": routing if isinstance(routing, dict) else {}})

    def _backfill_submission_publication_owners(self) -> None:
        """Attach legacy publication rows only when a single config owns that chat."""
        owners_by_chat: Dict[str, set[int]] = {}
        for item in self.list_users_with_channel_configs():
            owner = int(item.get("ownerUserId") or 0)
            for channel in item.get("channels") or []:
                if not isinstance(channel, dict):
                    continue
                chat_id = str(channel.get("chatId") or "").strip()
                if chat_id and owner > 0:
                    owners_by_chat.setdefault(chat_id, set()).add(owner)
        with self._connect() as connection:
            for chat_id, owners in owners_by_chat.items():
                if len(owners) != 1:
                    continue
                connection.execute(
                    "UPDATE submission_publications SET route_owner_user_id = ? WHERE channel_chat_id = ? AND route_owner_user_id IS NULL",
                    (next(iter(owners)), chat_id),
                )

    def _migrate_legacy_json(self) -> None:
        self._migrate_legacy_file(self.session_file, PAN123_SESSION_KEY)
        self._migrate_legacy_file(self.config_file, ADMIN_CONFIG_KEY)

    def _migrate_legacy_file(self, path: Path, key: str) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            if key == ADMIN_CONFIG_KEY:
                raw = merge_dicts(self.read_config(), raw)
                raw["updatedAt"] = utc_now_iso()
            elif self.read_value(key) is not None:
                raw = self.read_value(key)
            self.write_value(key, raw)
        try:
            path.unlink()
        except OSError:
            pass

def merge_dicts(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


LEGACY_MOVIE_ORGANIZE_TEMPLATE = "{{title}}{% if year %}.{{year}}{% endif %}{% if videoFormat %}.{{videoFormat}}{% endif %}{% if mediaSource %}.{{mediaSource}}{% endif %}{% if resourceType %}.{{resourceType}}{% endif %}{% if effect %}.{{effect}}{% endif %}{% if frameRate %}.{{frameRate}}{% endif %}{% if originalEdition %}.{{originalEdition}}{% endif %}{% if videoCodec %}.{{videoCodec}}{% endif %}{% if audioCodec %}.{{audioCodec}}{% endif %}{{releaseGroupSuffix}}{{fileExt}}"
LEGACY_TV_ORGANIZE_TEMPLATE = "{{title}}{% if year %}.{{year}}{% endif %}{% if seasonEpisode %}.{{seasonEpisode}}{% endif %}{% if videoFormat %}.{{videoFormat}}{% endif %}{% if mediaSource %}.{{mediaSource}}{% endif %}{% if resourceType %}.{{resourceType}}{% endif %}{% if highQuality %}.{{highQuality}}{% endif %}{% if dolbyVision %}.{{dolbyVision}}{% endif %}{% if dynamicRange %}.{{dynamicRange}}{% endif %}{% if frameRate %}.{{frameRate}}{% endif %}{% if colorDepth %}.{{colorDepth}}{% endif %}{% if videoCodec %}.{{videoCodec}}{% endif %}{% if audioCodec %}.{{audioCodec}}{% endif %}{{releaseGroupSuffix}}{{fileExt}}"


def normalize_rule_config(value: Any) -> Dict[str, Any]:
    defaults = DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") if isinstance(DEFAULT_SUBMISSION_CONFIG.get("ruleConfig"), dict) else {}
    source = value if isinstance(value, dict) else {}
    organize = source.get("organize") if isinstance(source.get("organize"), dict) else {}
    recognition = source.get("recognition") if isinstance(source.get("recognition"), dict) else {}
    display = source.get("display") if isinstance(source.get("display"), dict) else {}
    default_organize = defaults.get("organize") if isinstance(defaults.get("organize"), dict) else {}
    default_recognition = defaults.get("recognition") if isinstance(defaults.get("recognition"), dict) else {}
    default_display = defaults.get("display") if isinstance(defaults.get("display"), dict) else {}

    return {
        "quality": normalize_alias_list(source.get("quality"), defaults.get("quality") if isinstance(defaults.get("quality"), list) else [], "quality"),
        "source": normalize_alias_list(source.get("source"), defaults.get("source") if isinstance(defaults.get("source"), list) else [], "source"),
        "effect": normalize_alias_list(source.get("effect"), defaults.get("effect") if isinstance(defaults.get("effect"), list) else [], "effect"),
        "webSource": normalize_alias_list(source.get("webSource"), defaults.get("webSource") if isinstance(defaults.get("webSource"), list) else [], "web"),
        "videoCodec": normalize_alias_list(source.get("videoCodec"), defaults.get("videoCodec") if isinstance(defaults.get("videoCodec"), list) else [], "video"),
        "audioCodec": normalize_alias_list(source.get("audioCodec"), defaults.get("audioCodec") if isinstance(defaults.get("audioCodec"), list) else [], "audio"),
        "edition": normalize_alias_list(source.get("edition"), defaults.get("edition") if isinstance(defaults.get("edition"), list) else [], "edition"),
        "recognition": {
            "excludeWords": normalize_list(recognition.get("excludeWords"), default_recognition.get("excludeWords") if isinstance(default_recognition.get("excludeWords"), list) else []),
            "replacements": normalize_replacement_rules(recognition.get("replacements")),
            "movieKeywords": normalize_list(recognition.get("movieKeywords"), default_recognition.get("movieKeywords") if isinstance(default_recognition.get("movieKeywords"), list) else []),
            "tvKeywords": normalize_list(recognition.get("tvKeywords"), default_recognition.get("tvKeywords") if isinstance(default_recognition.get("tvKeywords"), list) else []),
            "releaseGroups": normalize_list(recognition.get("releaseGroups"), default_recognition.get("releaseGroups") if isinstance(default_recognition.get("releaseGroups"), list) else []),
        },
        "organize": {
            "fixedCategories": normalize_list(organize.get("fixedCategories"), default_organize.get("fixedCategories") if isinstance(default_organize.get("fixedCategories"), list) else []),
            "fallbackMovieCategory": normalize_text(organize.get("fallbackMovieCategory"), str(default_organize.get("fallbackMovieCategory") or "电影")),
            "fallbackTvCategory": normalize_text(organize.get("fallbackTvCategory"), str(default_organize.get("fallbackTvCategory") or "剧集")),
            "excludeWords": normalize_list(organize.get("excludeWords"), default_organize.get("excludeWords") if isinstance(default_organize.get("excludeWords"), list) else []),
            "discardSidecarExtensions": normalize_extensions(organize.get("discardSidecarExtensions")),
            "conflictPriority": normalize_conflict_priority(organize.get("conflictPriority")),
            "movieTemplate": normalize_organize_template(organize.get("movieTemplate"), str(default_organize.get("movieTemplate") or "{{title}}{{fileExt}}"), LEGACY_MOVIE_ORGANIZE_TEMPLATE),
            "tvTemplate": normalize_organize_template(organize.get("tvTemplate"), str(default_organize.get("tvTemplate") or "{{title}}{{fileExt}}"), LEGACY_TV_ORGANIZE_TEMPLATE),
            "mediaFolderTemplate": normalize_template(organize.get("mediaFolderTemplate"), str(default_organize.get("mediaFolderTemplate") or "{{folderTitle}}{% if year %} ({{year}}){% endif %}{% if tmdbId %} {tmdb-{{tmdbId}}}{% endif %}")),
            "seasonFolderTemplate": normalize_template(organize.get("seasonFolderTemplate"), str(default_organize.get("seasonFolderTemplate") or "Season {{season}}")),
            "categoryRules": normalize_category_rules(organize.get("categoryRules")),
        },
        "display": {
            "sourceLabels": normalize_source_label_rules(display.get("sourceLabels")),
        },
    }


def normalize_replacement_rules(value: Any) -> List[Dict[str, Any]]:
    defaults = (((DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") or {}).get("recognition") or {}).get("replacements") or [])
    if not isinstance(value, list):
        return copy.deepcopy(defaults)
    rules: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        record = item if isinstance(item, dict) else {}
        pattern = str(record.get("pattern") or "").strip()
        if not pattern:
            continue
        rules.append(
            {
                "id": str(record.get("id") or f"replace_{index}_{slug_id(pattern or 'rule')}"),
                "name": str(record.get("name") or pattern or f"替换规则 {index + 1}"),
                "enabled": record.get("enabled") is not False,
                "pattern": pattern,
                "replacement": str(record.get("replacement") or ""),
                "useRegex": bool(record.get("useRegex")),
                "flags": normalize_regex_flags(record.get("flags")),
            }
        )
    return rules


def normalize_alias_list(value: Any, defaults: List[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    source = value if isinstance(value, list) else defaults
    if prefix == "web":
        source = migrate_legacy_max_web_source_rules(source, defaults)
    default_by_id = {str(rule.get("id") or ""): rule for rule in defaults if isinstance(rule, dict)}
    default_by_value = {alias_key(str(rule.get("value") or "")): rule for rule in defaults if isinstance(rule, dict)}
    rules: List[Dict[str, Any]] = []
    for index, item in enumerate(source):
        record = item if isinstance(item, dict) else {}
        base = default_by_id.get(str(record.get("id") or "")) or default_by_value.get(alias_key(str(record.get("value") or "")))
        rule = normalize_alias_rule(record, base if isinstance(base, dict) else None, f"{prefix}_{index}")
        if rule.get("value"):
            rules.append(rule)

    # 把新版默认规则里老配置没有的项追加进来（例如新增 MAXPLUS 时），
    # 这样发布新版加入默认别名后，老用户的"识别映射"与预览会自动同步。
    existing_ids = {str(rule.get("id") or "") for rule in rules if isinstance(rule, dict)}
    existing_values = {alias_key(str(rule.get("value") or "")) for rule in rules if isinstance(rule, dict)}
    for default in defaults:
        if not isinstance(default, dict):
            continue
        did = str(default.get("id") or "")
        dvalue = alias_key(str(default.get("value") or ""))
        if did in existing_ids or dvalue in existing_values:
            continue
        appended = normalize_alias_rule(default, default, f"{prefix}_default_{len(rules)}")
        if appended.get("value"):
            rules.append(appended)
            existing_ids.add(did)
            existing_values.add(dvalue)
    return rules


def migrate_legacy_max_web_source_rules(value: List[Any], defaults: List[Dict[str, Any]]) -> List[Any]:
    records = list(value)
    legacy_indexes = [
        index
        for index, item in enumerate(records)
        if isinstance(item, dict) and str(item.get("id") or "") in {"web_hmax", "web_max"}
    ]
    if not legacy_indexes:
        return records

    default = next(
        (item for item in defaults if isinstance(item, dict) and str(item.get("id") or "") == "web_max"),
        {"id": "web_max", "enabled": True, "value": "MAX", "aliases": ["MAX", "HMAX"], "order": 94},
    )
    legacy = [records[index] for index in legacy_indexes]
    aliases: List[str] = []
    seen = set()
    for alias in ["MAX", "HMAX", *[entry for item in legacy for entry in (item.get("aliases") or [])]]:
        clean = str(alias or "").strip()
        key = alias_key(clean)
        if clean and key not in seen:
            seen.add(key)
            aliases.append(clean)
    merged = {
        **default,
        "id": "web_max",
        "enabled": any(item.get("enabled") is not False for item in legacy),
        "value": "MAX",
        "aliases": aliases,
        "order": max([int(item.get("order") or 0) for item in legacy] + [int(default.get("order") or 0)]),
    }
    first_index = legacy_indexes[0]
    return [
        *[item for index, item in enumerate(records[:first_index]) if index not in legacy_indexes],
        merged,
        *[item for index, item in enumerate(records[first_index + 1 :], start=first_index + 1) if index not in legacy_indexes],
    ]


def normalize_alias_rule(item: Dict[str, Any], base: Optional[Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    value_source = item["value"] if "value" in item else ((base or {}).get("value") if isinstance(base, dict) else "")
    value = str(value_source or "").strip()
    alias_source = item["aliases"] if "aliases" in item else ((base or {}).get("aliases") if isinstance(base, dict) else [value])
    aliases = normalize_list(alias_source, (base or {}).get("aliases") if isinstance((base or {}).get("aliases"), list) else [value])
    if value and alias_key(value) not in {alias_key(alias) for alias in aliases}:
        aliases.insert(0, value)
    return {
        "id": str(item.get("id") or (base or {}).get("id") or f"{prefix}_{slug_id(value or 'rule')}"),
        "name": str(item.get("name") or (base or {}).get("name") or value or ""),
        "enabled": item["enabled"] if "enabled" in item else ((base or {}).get("enabled") if isinstance(base, dict) and "enabled" in base else True),
        "value": value,
        "aliases": aliases,
        **({"rank": item["rank"]} if "rank" in item else ({"rank": (base or {}).get("rank")} if isinstance(base, dict) and "rank" in base else {})),
        **({"order": number} if (number := normalize_number(item.get("order"), (base or {}).get("order") if isinstance(base, dict) else None)) is not None else {}),
        **({"priority": number} if (number := normalize_number(item.get("priority"), (base or {}).get("priority") if isinstance(base, dict) else None)) is not None else {}),
    }


def normalize_category_rules(value: Any) -> List[Dict[str, Any]]:
    defaults = (((DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") or {}).get("organize") or {}).get("categoryRules") or [])
    source = value if isinstance(value, list) else defaults
    default_by_id = {str(rule.get("id") or ""): rule for rule in defaults if isinstance(rule, dict)}
    rules: List[Dict[str, Any]] = []
    for index, item in enumerate(source):
        record = item if isinstance(item, dict) else {}
        base = default_by_id.get(str(record.get("id") or "")) or {}
        category = str(record.get("category") if "category" in record else base.get("category") or "").strip()
        media_types = normalize_media_types(record.get("mediaTypes") if "mediaTypes" in record else base.get("mediaTypes"))
        genre_ids = normalize_positive_int_list(record.get("genreIds") if "genreIds" in record else base.get("genreIds"))
        rule = {
            "id": str(record.get("id") or base.get("id") or f"category_{index}_{slug_id(category or 'rule')}"),
            "name": str(record.get("name") or base.get("name") or category or ""),
            "enabled": record["enabled"] if "enabled" in record else (base.get("enabled") if "enabled" in base else True),
            "category": category,
            "keywords": normalize_list(record.get("keywords") if "keywords" in record else base.get("keywords"), base.get("keywords") if isinstance(base.get("keywords"), list) else []),
            **({"mediaTypes": media_types} if media_types else {}),
            **({"genreIds": genre_ids} if genre_ids else {}),
        }
        if rule["category"] and (rule["keywords"] or genre_ids):
            rules.append(rule)
    return rules


def normalize_source_label_rules(value: Any) -> List[Dict[str, Any]]:
    defaults = (((DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") or {}).get("display") or {}).get("sourceLabels") or [])
    source = value if isinstance(value, list) else defaults
    default_by_id = {str(rule.get("id") or ""): rule for rule in defaults if isinstance(rule, dict)}
    default_by_source = {alias_key(str(rule.get("source") or "")): rule for rule in defaults if isinstance(rule, dict)}
    rules: List[Dict[str, Any]] = []
    for index, item in enumerate(source):
        record = item if isinstance(item, dict) else {}
        base = default_by_id.get(str(record.get("id") or "")) or default_by_source.get(alias_key(str(record.get("source") or ""))) or {}
        source_text = str(record.get("source") if "source" in record else base.get("source") or "").strip()
        template = str(record.get("template") if "template" in record else base.get("template") or "").strip()
        number = normalize_number(record.get("order"), base.get("order") if isinstance(base, dict) else None)
        rule = {
            "id": str(record.get("id") or base.get("id") or f"display_{index}_{slug_id(source_text or 'rule')}"),
            "name": str(record.get("name") or base.get("name") or source_text or ""),
            "enabled": record["enabled"] if "enabled" in record else (base.get("enabled") if "enabled" in base else True),
            "source": source_text,
            "template": template,
            **({"order": number} if number is not None else {}),
        }
        if rule["source"] and rule["template"]:
            rules.append(rule)
    return sorted(rules, key=lambda item: int(item.get("order") or 0), reverse=True)


def normalize_extensions(value: Any) -> List[str]:
    defaults = (((DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") or {}).get("organize") or {}).get("discardSidecarExtensions") or [])
    return [extension.lstrip(".").lower() for extension in normalize_list(value, defaults) if extension.lstrip(".").strip()]


def normalize_conflict_priority(value: Any) -> List[str]:
    allowed = ["remuxBluray", "resolution", "dolby", "size"]
    defaults = (((DEFAULT_SUBMISSION_CONFIG.get("ruleConfig") or {}).get("organize") or {}).get("conflictPriority") or allowed)
    source = value if isinstance(value, list) else defaults
    selected: List[str] = []
    for item in source:
        key = str(item or "")
        if key in allowed and key not in selected:
            selected.append(key)
    return selected + [item for item in allowed if item not in selected]


def normalize_list(value: Any, fallback: Optional[List[Any]] = None) -> List[str]:
    source = value if isinstance(value, list) else (fallback if isinstance(fallback, list) else [])
    seen = set()
    out: List[str] = []
    for item in source:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def normalize_media_types(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (str(item or "").strip() for item in value) if item in {"movie", "tv", "unknown"}]


def normalize_positive_int_list(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    normalized: List[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in normalized:
            normalized.append(number)
    return normalized


def merge_missing_template_blocks(persisted: str, default: str) -> str:
    """把默认模板里有的、但老模板里没有的 {% if X %}...{% endif %} 块补到老模板里。

    同时按默认模板的字段顺序重建 {% if %} 块，确保升级后字段排列与默认值一致。
    这样发布新版在默认值里新增占位符（例如 originalEdition）或调整顺序时，
    老用户的持久化命名模板会自动同步，无需手动重置。
    """
    block_pattern = re.compile(r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}", re.S)
    default_blocks = block_pattern.findall(default)
    persisted_blocks = block_pattern.findall(persisted)
    persisted_by_name = {name: body for name, body in persisted_blocks}
    default_names = [name for name, _ in default_blocks]
    default_body_by_name = {name: body for name, body in default_blocks}

    new_blocks: List[Tuple[str, str]] = []
    for name in default_names:
        if name in persisted_by_name:
            # 保留用户在 body 里可能改过的内容，但确保变量名出现在默认里
            new_blocks.append((name, persisted_by_name[name]))
        else:
            new_blocks.append((name, default_body_by_name[name]))

    rebuilt_blocks = "".join(f"{{% if {name} %}}{body}{{% endif %}}" for name, body in new_blocks)

    # 取默认模板的前导（首块前）与收尾（{{releaseGroupSuffix}} 之后），
    # 保证升级后模板结构与默认值一致（例如始终以 {{title}} 开头）。
    first_block = default.find("{% if")
    preamble = default[:first_block] if first_block >= 0 else ""
    anchor = "{{releaseGroupSuffix}}"
    anchor_idx = default.find(anchor)
    postamble = default[anchor_idx:] if anchor_idx >= 0 else "{{fileExt}}"

    # 若持久化模板已有 {{title}} 前导则保留，否则用默认的前导
    if persisted.startswith("{{title}}"):
        head_prefix = "{{title}}"
    else:
        head_prefix = preamble

    # 若持久化已有锚点则保留其后的尾巴（含 {{fileExt}}），否则用默认的收尾
    if anchor in persisted:
        _, _, tail = persisted.partition(anchor)
        return head_prefix + rebuilt_blocks + anchor + tail
    return head_prefix + rebuilt_blocks + postamble


def normalize_template(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def normalize_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def normalize_regex_flags(value: Any) -> str:
    flags = "".join(dict.fromkeys(re.sub(r"[^dgimsuvy]", "", str(value or "giu"))))
    return flags or "giu"


def normalize_organize_template(value: Any, fallback: str, legacy_default: Optional[str] = None) -> str:
    normalized = re.sub(r"{{\s*namingTitle\s*}}", "{{title}}", normalize_template(value, fallback))
    normalized = merge_missing_template_blocks(normalized, fallback)
    return fallback if legacy_default and normalized == legacy_default else normalized


def normalize_number(value: Any, fallback: Any = None) -> Optional[int]:
    try:
        number = int(float(value if value is not None else fallback))
    except (TypeError, ValueError):
        return None
    return number


def alias_key(value: str) -> str:
    return re.sub(r"[\s._-]+", "", str(value or "")).upper()


def slug_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "rule").lower()).strip("_")
    return (slug or "rule")[:40]


def sanitize_submission_config(config: Dict[str, Any], migrate_legacy_authorization: bool = True) -> Dict[str, Any]:
    legacy_allowed = positive_user_ids(config.get("allowedUserIds") or [])
    admins = positive_user_ids(config.get("telegramAdminUserIds") or [])
    owners = positive_user_ids(config.get("channelOwnerUserIds") or [])
    # Existing installations used one global whitelist.  Preserve that
    # privilege until the administrator deliberately narrows either list.
    config["allowedUserIds"] = legacy_allowed
    config["telegramAdminUserIds"] = admins if admins or not migrate_legacy_authorization else legacy_allowed
    config["channelOwnerUserIds"] = owners if owners or not migrate_legacy_authorization else legacy_allowed
    config["channelSettingsUrl"] = str(config.get("channelSettingsUrl") or "").strip()
    telegram_api = config.get("telegramApi")
    if isinstance(telegram_api, dict):
        # Older saves could persist apiId as a number; every client expects
        # these three to be strings.
        for key in ("apiId", "apiHash", "session"):
            if key in telegram_api:
                telegram_api[key] = str(telegram_api.get(key) or "").strip()
        config["telegramApi"] = telegram_api
    config["ruleConfig"] = normalize_rule_config(config.get("ruleConfig"))
    helper = config.get("pan115Helper")
    if isinstance(helper, dict):
        for legacy_key in ("botMode", "linkTargetDirId", "cleanTargetDirIds", "offlineRequestIntervalMs"):
            helper.pop(legacy_key, None)
        config["pan115Helper"] = helper
    system = config.get("system")
    if not isinstance(system, dict):
        system = {}
    system.pop("publicBaseUrl", None)
    system.pop("webhookUrl", None)
    config["system"] = system
    return config


def positive_user_ids(values: Any) -> List[int]:
    result = []
    for value in values if isinstance(values, list) else []:
        parsed = optional_int(value)
        if parsed and parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def submission_publication_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "channelChatId": row["channel_chat_id"],
        "channelId": row["channel_id"],
        "channelTitle": row["channel_title"],
        "routeOwnerUserId": int(row["route_owner_user_id"] or 0) if "route_owner_user_id" in row.keys() else 0,
        "messageId": int(row["message_id"] or 0),
        "seedMessageIds": [
            int(message_id)
            for value in json_value(row["seed_message_ids_json"], [])
            for message_id in [optional_int(value)]
            if message_id and message_id > 0
        ]
        if "seed_message_ids_json" in row.keys()
        else [],
        "identityKey": row["identity_key"],
        "mediaType": row["media_type"],
        "tmdbId": int(row["tmdb_id"]) if row["tmdb_id"] is not None else None,
        "titleKey": row["title_key"],
        "title": row["title"],
        "resourceName": row["resource_name"],
        "shareUrl": row["share_url"],
        "fastLink": bool(row["fast_link"]),
        "draftId": row["draft_id"],
        "publishedAt": row["published_at"],
        "deletedAt": row["deleted_at"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def transfer_task_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    files = []
    if row["files_json"]:
        try:
            files = json.loads(row["files_json"])
        except json.JSONDecodeError:
            files = []
    logs = []
    if row["logs_json"]:
        try:
            logs = json.loads(row["logs_json"])
        except json.JSONDecodeError:
            logs = []
    notice_message_ids = []
    if row["transfer_notice_message_ids_json"]:
        try:
            notice_message_ids = json.loads(row["transfer_notice_message_ids_json"])
        except json.JSONDecodeError:
            notice_message_ids = []
    return {
        "id": row["id"],
        "kind": row["kind"] or "pan115_share",
        "source": row["source"],
        "sourceText": row["source_text"],
        "chatId": row["chat_id"],
        "userId": row["user_id"],
        "messageId": row["message_id"],
        "shareUrl": row["share_url"],
        "shareCode": row["share_code"],
        "receiveCode": row["receive_code"],
        "title": row["title"],
        "status": row["status"],
        "totalFiles": row["total_files"],
        "doneFiles": row["done_files"],
        "files": files,
        "logs": logs,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "startedAt": row["started_at"] or "",
        "finishedAt": row["finished_at"] or "",
        "error": row["error"],
        "transferNoticeChatId": row["transfer_notice_chat_id"],
        "transferNoticeMessageIds": notice_message_ids,
        "transferFinalMessageId": row["transfer_final_message_id"],
        "remoteTaskId": row["remote_task_id"],
        "targetDirId": row["target_dir_id"],
        "shareOwnerUserId": row["share_owner_user_id"],
    }


def pan123_open_token_key(client_id: str, client_secret: str) -> str:
    """按 clientId:clientSecret 生成稳定 key，用于 pan123_open_tokens 表主键。"""
    raw = f"{str(client_id or '').strip()}:{str(client_secret or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def json_value(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text.encode("ascii"))
        expected = base64.b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


