from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path | None = None):
        configured = os.environ.get("CONCEPT_BRANCH_DB")
        self.path = Path(path or configured or Path.home() / ".local" / "share" / "concept-branch" / "concept-branch.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS discussions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    parent_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    source_message_id TEXT,
                    selected_text TEXT,
                    context_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_root_per_discussion
                    ON nodes(discussion_id) WHERE parent_id IS NULL;
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    provider_id TEXT,
                    provider_model TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_by_node ON messages(node_id, created_at);
                CREATE INDEX IF NOT EXISTS nodes_by_discussion ON nodes(discussion_id, created_at);
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    format TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    content BLOB NOT NULL,
                    extracted_text TEXT NOT NULL,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attachments_by_node ON attachments(node_id, created_at);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            if "provider_id" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN provider_id TEXT")
            if "provider_model" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN provider_model TEXT")
            discussion_columns = {row[1] for row in db.execute("PRAGMA table_info(discussions)")}
            if "user_id" not in discussion_columns:
                db.execute("ALTER TABLE discussions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            node_columns = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
            if "user_id" not in node_columns:
                db.execute("ALTER TABLE nodes ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def user_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._dict(db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone())

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._dict(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def create_user(self, username: str, password_hash: str, salt: str, role: str) -> dict[str, Any]:
        user_id, stamp = str(uuid.uuid4()), now()
        with self.connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", (user_id, username, password_hash, salt, role, stamp))
        return self.get_user(user_id)  # type: ignore[return-value]

    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        stamp = now()
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token_hash, user_id, stamp, expires_at))

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None:
        stamp = now()
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (stamp,))
            row = db.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (token_hash, stamp),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def list_discussions(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM discussions WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def search(self, user_id: str, query: str, limit: int = 50) -> list[dict[str, Any]]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self.connect() as db:
            discussions = db.execute(
                """
                SELECT 'discussion' AS kind, discussions.id AS discussion_id,
                       discussions.title AS discussion_title, nodes.id AS node_id,
                       nodes.title AS node_title, NULL AS message_id, NULL AS role,
                       discussions.title AS match_text, discussions.updated_at
                FROM discussions
                JOIN nodes ON nodes.discussion_id = discussions.id AND nodes.parent_id IS NULL
                WHERE discussions.user_id = ? AND discussions.title LIKE ? ESCAPE '\\' COLLATE NOCASE
                """,
                (user_id, pattern),
            ).fetchall()
            nodes = db.execute(
                """
                SELECT 'node' AS kind, discussions.id AS discussion_id,
                       discussions.title AS discussion_title, nodes.id AS node_id,
                       nodes.title AS node_title, NULL AS message_id, NULL AS role,
                       nodes.title AS match_text, nodes.updated_at
                FROM nodes
                JOIN discussions ON discussions.id = nodes.discussion_id
                WHERE nodes.user_id = ? AND nodes.title LIKE ? ESCAPE '\\' COLLATE NOCASE
                """,
                (user_id, pattern),
            ).fetchall()
            messages = db.execute(
                """
                SELECT 'message' AS kind, discussions.id AS discussion_id,
                       discussions.title AS discussion_title, nodes.id AS node_id,
                       nodes.title AS node_title, messages.id AS message_id,
                       messages.role, messages.content AS match_text, messages.created_at AS updated_at
                FROM messages
                JOIN nodes ON nodes.id = messages.node_id
                JOIN discussions ON discussions.id = nodes.discussion_id
                WHERE nodes.user_id = ? AND messages.content LIKE ? ESCAPE '\\' COLLATE NOCASE
                """,
                (user_id, pattern),
            ).fetchall()
        results = [dict(row) for row in [*discussions, *nodes, *messages]]
        results.sort(key=lambda item: item["updated_at"], reverse=True)
        for item in results:
            text = " ".join(str(item.pop("match_text")).split())
            index = text.casefold().find(query.casefold())
            start = max(0, index - 45) if index >= 0 else 0
            end = min(len(text), start + 130)
            item["snippet"] = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
        return results[:limit]

    def get_discussion(self, discussion_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._dict(db.execute("SELECT * FROM discussions WHERE id = ? AND user_id = ?", (discussion_id, user_id)).fetchone())

    def create_discussion(self, title: str, user_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        discussion_id, node_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), now()
        with self.connect() as db:
            db.execute("INSERT INTO discussions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (discussion_id, user_id, title, stamp, stamp))
            db.execute(
                "INSERT INTO nodes (id, discussion_id, user_id, parent_id, title, source_message_id, selected_text, context_json, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?)",
                (node_id, discussion_id, user_id, "主线", stamp, stamp),
            )
        return self.get_discussion(discussion_id, user_id), self.get_node(node_id, user_id)  # type: ignore[return-value]

    def update_discussion(self, discussion_id: str, user_id: str, title: str) -> dict[str, Any] | None:
        stamp = now()
        with self.connect() as db:
            result = db.execute("UPDATE discussions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?", (title, stamp, discussion_id, user_id))
        return self.get_discussion(discussion_id, user_id) if result.rowcount else None

    def delete_discussion(self, discussion_id: str, user_id: str) -> bool:
        with self.connect() as db:
            result = db.execute("DELETE FROM discussions WHERE id = ? AND user_id = ?", (discussion_id, user_id))
        return bool(result.rowcount)

    def list_nodes(self, discussion_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM nodes WHERE discussion_id = ? AND user_id = ? ORDER BY created_at", (discussion_id, user_id)).fetchall()
        return [self._node_dict(row) for row in rows]

    def get_node(self, node_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM nodes WHERE id = ? AND user_id = ?", (node_id, user_id)).fetchone()
        return self._node_dict(row) if row else None

    @staticmethod
    def _node_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["context"] = json.loads(item.pop("context_json")) if item.get("context_json") else None
        return item

    def create_node(
        self,
        discussion_id: str,
        title: str,
        parent_id: str,
        user_id: str,
        source_message_id: str | None = None,
        selected_text: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_id, stamp = str(uuid.uuid4()), now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO nodes (id, discussion_id, user_id, parent_id, title, source_message_id, selected_text, context_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (node_id, discussion_id, user_id, parent_id, title, source_message_id, selected_text, json.dumps(context, ensure_ascii=False) if context else None, stamp, stamp),
            )
            db.execute("UPDATE discussions SET updated_at = ? WHERE id = ? AND user_id = ?", (stamp, discussion_id, user_id))
        return self.get_node(node_id, user_id)  # type: ignore[return-value]

    def update_node(self, node_id: str, user_id: str, title: str) -> dict[str, Any] | None:
        stamp = now()
        with self.connect() as db:
            result = db.execute("UPDATE nodes SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?", (title, stamp, node_id, user_id))
        return self.get_node(node_id, user_id) if result.rowcount else None

    def delete_node(self, node_id: str, user_id: str) -> bool:
        node = self.get_node(node_id, user_id)
        if not node or node["parent_id"] is None:
            return False
        with self.connect() as db:
            result = db.execute("DELETE FROM nodes WHERE id = ? AND user_id = ?", (node_id, user_id))
        return bool(result.rowcount)

    def list_messages(self, node_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT messages.* FROM messages JOIN nodes ON nodes.id = messages.node_id WHERE messages.node_id = ? AND nodes.user_id = ? ORDER BY messages.created_at, messages.rowid",
                (node_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, message_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._dict(db.execute("SELECT messages.* FROM messages JOIN nodes ON nodes.id = messages.node_id WHERE messages.id = ? AND nodes.user_id = ?", (message_id, user_id)).fetchone())

    def add_exchange(self, node_id: str, user_content: str, assistant_content: str, owner_id: str, provider_id: str | None = None, provider_model: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        user_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
        user_time, assistant_time = now(), now()
        with self.connect() as db:
            node = db.execute("SELECT discussion_id FROM nodes WHERE id = ? AND user_id = ?", (node_id, owner_id)).fetchone()
            if not node:
                raise KeyError(node_id)
            db.execute("INSERT INTO messages (id, node_id, role, content, provider_id, provider_model, created_at) VALUES (?, ?, 'user', ?, ?, ?, ?)", (user_id, node_id, user_content, provider_id, provider_model, user_time))
            db.execute("INSERT INTO messages (id, node_id, role, content, provider_id, provider_model, created_at) VALUES (?, ?, 'assistant', ?, ?, ?, ?)", (assistant_id, node_id, assistant_content, provider_id, provider_model, assistant_time))
            db.execute("UPDATE nodes SET updated_at = ? WHERE id = ?", (assistant_time, node_id))
            db.execute("UPDATE discussions SET updated_at = ? WHERE id = ?", (assistant_time, node["discussion_id"]))
        return self.get_message(user_id, owner_id), self.get_message(assistant_id, owner_id)  # type: ignore[return-value]

    def ancestors(self, node_id: str, user_id: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        current = self.get_node(node_id, user_id)
        while current:
            nodes.append(current)
            current = self.get_node(current["parent_id"], user_id) if current["parent_id"] else None
        return list(reversed(nodes))

    def related_user_message(self, assistant_message_id: str, user_id: str) -> dict[str, Any] | None:
        assistant = self.get_message(assistant_message_id, user_id)
        if not assistant:
            return None
        messages = self.list_messages(assistant["node_id"], user_id)
        related = None
        for message in messages:
            if message["id"] == assistant_message_id:
                break
            if message["role"] == "user":
                related = message
        return related

    @staticmethod
    def _attachment_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item.pop("content", None)
        item.pop("extracted_text", None)
        item["truncated"] = bool(item["truncated"])
        return item

    def create_attachment(
        self,
        node_id: str,
        user_id: str,
        filename: str,
        content_type: str,
        file_format: str,
        content: bytes,
        extracted_text: str,
        truncated: bool,
    ) -> dict[str, Any]:
        attachment_id, stamp = str(uuid.uuid4()), now()
        with self.connect() as db:
            node = db.execute("SELECT id FROM nodes WHERE id = ? AND user_id = ?", (node_id, user_id)).fetchone()
            if not node:
                raise KeyError(node_id)
            db.execute(
                """INSERT INTO attachments
                   (id, node_id, user_id, filename, content_type, format, size_bytes, sha256, content, extracted_text, truncated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (attachment_id, node_id, user_id, filename, content_type, file_format, len(content), hashlib.sha256(content).hexdigest(), content, extracted_text, int(truncated), stamp),
            )
        return self.get_attachment(attachment_id, user_id)  # type: ignore[return-value]

    def get_attachment(self, attachment_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM attachments WHERE id = ? AND user_id = ?", (attachment_id, user_id)).fetchone()
        return self._attachment_public(row) if row else None

    def list_node_attachments(self, node_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM attachments WHERE node_id = ? AND user_id = ? ORDER BY created_at",
                (node_id, user_id),
            ).fetchall()
        return [self._attachment_public(row) for row in rows]

    def list_context_attachments(self, node_id: str, user_id: str, include_text: bool = False) -> list[dict[str, Any]]:
        ancestor_ids = [item["id"] for item in self.ancestors(node_id, user_id)]
        if not ancestor_ids:
            return []
        placeholders = ",".join("?" for _ in ancestor_ids)
        columns = "*" if include_text else "id, node_id, user_id, filename, content_type, format, size_bytes, sha256, truncated, created_at"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {columns} FROM attachments WHERE user_id = ? AND node_id IN ({placeholders}) ORDER BY created_at",
                (user_id, *ancestor_ids),
            ).fetchall()
        if include_text:
            return [dict(row) for row in rows]
        result = [self._attachment_public(row) for row in rows]
        for item in result:
            item["inherited"] = item["node_id"] != node_id
        return result

    def delete_attachment(self, attachment_id: str, node_id: str, user_id: str) -> bool:
        with self.connect() as db:
            result = db.execute(
                "DELETE FROM attachments WHERE id = ? AND node_id = ? AND user_id = ?",
                (attachment_id, node_id, user_id),
            )
        return bool(result.rowcount)
