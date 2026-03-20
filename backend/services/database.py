from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash

from settings import Settings
from utils import extract_text_from_file, get_logger, log_event

SCHEMA_LOCK_ID = 842021


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_text_for_storage(value: str | None) -> str:
    return (value or "").replace("\x00", "")


@dataclass
class DatabaseService:
    settings: Settings = field(default_factory=Settings)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.db")
        Path(self.settings.uploads_dir).mkdir(parents=True, exist_ok=True)
        self.pool = ConnectionPool(
            conninfo=self.settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self._initialize()

    def _initialize(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_ID,))
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            name TEXT NOT NULL,
                            email TEXT NOT NULL UNIQUE,
                            password_hash TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS sessions (
                            token TEXT PRIMARY KEY,
                            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                            role TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            last_used_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS chat_threads (
                            id TEXT PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            title TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            active_conversation_id TEXT,
                            active_input_key TEXT
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS chat_messages (
                            id SERIAL PRIMARY KEY,
                            thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            agent TEXT,
                            created_at TIMESTAMPTZ NOT NULL,
                            is_summarized BOOLEAN NOT NULL DEFAULT FALSE
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS chat_summaries (
                            id SERIAL PRIMARY KEY,
                            thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
                            summary TEXT NOT NULL,
                            covered_message_count INTEGER NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS uploaded_files (
                            id SERIAL PRIMARY KEY,
                            original_name TEXT NOT NULL,
                            stored_name TEXT NOT NULL,
                            file_path TEXT NOT NULL,
                            content_type TEXT,
                            size_bytes BIGINT NOT NULL,
                            uploaded_by TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                finally:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_ID,))
            conn.commit()
        log_event(self.logger, 20, "database_initialized", provider="postgres", database_url=self.settings.database_url)

    def register_user(self, name: str, email: str, password: str) -> dict[str, Any]:
        password_hash = generate_password_hash(password)
        created_at = utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (name, email, password_hash, created_at) VALUES (%s, %s, %s, %s) RETURNING id, name, email",
                    (clean_text_for_storage(name), email.lower(), password_hash, created_at),
                )
                row = cur.fetchone()
            conn.commit()
        log_event(self.logger, 20, "user_registered", user_id=row["id"], email=row["email"])
        return {"id": row["id"], "name": row["name"], "email": row["email"], "role": "user"}

    def authenticate_user(self, email: str, password: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, email, password_hash FROM users WHERE email = %s", (email.lower(),))
                row = cur.fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return {"id": row["id"], "name": row["name"], "email": row["email"], "role": "user"}

    def authenticate_admin(self, username: str, password: str) -> dict[str, Any] | None:
        if username == self.settings.admin_username and password == self.settings.admin_password:
            return {"id": None, "name": username, "email": None, "role": "admin"}
        return None

    def create_session(self, user: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        now = utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (token, user_id, role, created_at, last_used_at) VALUES (%s, %s, %s, %s, %s)",
                    (token, user.get("id"), user["role"], now, now),
                )
            conn.commit()
        return token

    def get_session(self, token: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.token, s.user_id, s.role, u.name, u.email
                    FROM sessions s
                    LEFT JOIN users u ON u.id = s.user_id
                    WHERE s.token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE sessions SET last_used_at = %s WHERE token = %s", (utc_now(), token))
            conn.commit()
        if not row:
            return None
        return {
            "token": row["token"],
            "user_id": row["user_id"],
            "role": row["role"],
            "name": row["name"] or self.settings.admin_username,
            "email": row["email"],
        }

    def create_chat_thread(self, user_id: int, title: str = "New chat") -> dict[str, Any]:
        thread_id = secrets.token_hex(16)
        now = utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_threads (id, user_id, title, created_at, updated_at, active_conversation_id, active_input_key) VALUES (%s, %s, %s, %s, %s, NULL, NULL)",
                    (thread_id, user_id, clean_text_for_storage(title), now, now),
                )
            conn.commit()
        return self.get_chat_thread(user_id, thread_id)

    def list_chat_threads(self, user_id: int) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, created_at, updated_at FROM chat_threads WHERE user_id = %s ORDER BY updated_at DESC",
                    (user_id,),
                )
                rows = cur.fetchall()
        return [_serialize_row(row) for row in rows]

    def get_chat_thread(self, user_id: int, thread_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM chat_threads WHERE id = %s AND user_id = %s", (thread_id, user_id))
                row = cur.fetchone()
        return _serialize_row(row) if row else None

    def update_thread_state(self, thread_id: str, conversation_id: str | None, input_key: str | None) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE chat_threads SET active_conversation_id = %s, active_input_key = %s, updated_at = %s WHERE id = %s",
                    (conversation_id, input_key, utc_now(), thread_id),
                )
            conn.commit()

    def update_thread_title_if_default(self, thread_id: str, content: str) -> None:
        title = clean_text_for_storage(content).strip()[:60] or "New chat"
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title FROM chat_threads WHERE id = %s", (thread_id,))
                row = cur.fetchone()
                if row and row["title"] == "New chat":
                    cur.execute("UPDATE chat_threads SET title = %s, updated_at = %s WHERE id = %s", (title, utc_now(), thread_id))
            conn.commit()

    def append_message(self, thread_id: str, role: str, content: str, agent: str | None = None) -> dict[str, Any]:
        now = utc_now()
        safe_content = clean_text_for_storage(content)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages (thread_id, role, content, agent, created_at, is_summarized) VALUES (%s, %s, %s, %s, %s, FALSE) RETURNING id, role, content, agent, created_at",
                    (thread_id, role, safe_content, clean_text_for_storage(agent), now),
                )
                row = cur.fetchone()
                cur.execute("UPDATE chat_threads SET updated_at = %s WHERE id = %s", (now, thread_id))
            conn.commit()
        return _serialize_row(row)

    def list_unsummarized_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, role, content, agent, created_at FROM chat_messages WHERE thread_id = %s AND is_summarized = FALSE ORDER BY id ASC",
                    (thread_id,),
                )
                rows = cur.fetchall()
        return [_serialize_row(row) for row in rows]

    def list_recent_messages(self, thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, role, content, agent, created_at FROM chat_messages WHERE thread_id = %s AND is_summarized = FALSE ORDER BY id ASC LIMIT %s",
                    (thread_id, limit),
                )
                rows = cur.fetchall()
        return [_serialize_row(row) for row in rows]

    def list_chat_summaries(self, thread_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, summary, covered_message_count, created_at FROM chat_summaries WHERE thread_id = %s ORDER BY id ASC",
                    (thread_id,),
                )
                rows = cur.fetchall()
        return [_serialize_row(row) for row in rows]

    def create_summary(self, thread_id: str, summary: str, message_ids: list[int]) -> dict[str, Any]:
        now = utc_now()
        safe_summary = clean_text_for_storage(summary)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_summaries (thread_id, summary, covered_message_count, created_at) VALUES (%s, %s, %s, %s) RETURNING id, summary, covered_message_count, created_at",
                    (thread_id, safe_summary, len(message_ids), now),
                )
                summary_row = cur.fetchone()
                cur.execute("UPDATE chat_messages SET is_summarized = TRUE WHERE id = ANY(%s)", (message_ids,))
            conn.commit()
        return _serialize_row(summary_row)

    def save_uploaded_file(self, original_name: str, content_type: str | None, file_bytes: bytes, uploaded_by: str) -> dict[str, Any]:
        stored_name = f"{secrets.token_hex(12)}_{original_name}"
        file_path = Path(self.settings.uploads_dir) / stored_name
        file_path.write_bytes(file_bytes)
        now = utc_now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO uploaded_files (original_name, stored_name, file_path, content_type, size_bytes, uploaded_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, original_name, stored_name, file_path, content_type, size_bytes, uploaded_by, created_at
                    """,
                    (clean_text_for_storage(original_name), clean_text_for_storage(stored_name), str(file_path), clean_text_for_storage(content_type), len(file_bytes), clean_text_for_storage(uploaded_by), now),
                )
                row = cur.fetchone()
            conn.commit()
        record = _serialize_row(row)
        log_event(self.logger, 20, "uploaded_file_saved", file_id=record["id"], original_name=original_name, size_bytes=len(file_bytes))
        return record

    def list_uploaded_files(self) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, original_name, stored_name, file_path, content_type, size_bytes, uploaded_by, created_at FROM uploaded_files ORDER BY created_at DESC"
                )
                rows = cur.fetchall()
        return [_serialize_row(row) for row in rows]

    def get_uploaded_file(self, file_id: int) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, original_name, stored_name, file_path, content_type, size_bytes, uploaded_by, created_at FROM uploaded_files WHERE id = %s",
                    (file_id,),
                )
                row = cur.fetchone()
        return _serialize_row(row) if row else None

    def delete_uploaded_file(self, file_id: int) -> dict[str, Any] | None:
        record = self.get_uploaded_file(file_id)
        if not record:
            return None
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM uploaded_files WHERE id = %s", (file_id,))
            conn.commit()
        try:
            os.remove(record["file_path"])
        except FileNotFoundError:
            pass
        log_event(self.logger, 20, "uploaded_file_deleted", file_id=file_id, original_name=record["original_name"])
        return record

    def load_uploaded_documents(self) -> list[dict[str, Any]]:
        documents = []
        for record in self.list_uploaded_files():
            path = Path(record["file_path"])
            if not path.exists():
                continue
            try:
                text = extract_text_from_file(path, allowed_base=self.settings.uploads_dir)
            except (OSError, ValueError):
                continue
            text = clean_text_for_storage(text)
            if not text.strip():
                continue
            documents.append(
                {
                    "id": f"upload-{record['id']}",
                    "source": f"upload:{record['original_name']}",
                    "category": "uploaded_file",
                    "text": text,
                    "metadata": {
                        "file_id": record["id"],
                        "name": record["original_name"],
                        "uploaded_by": record["uploaded_by"],
                        "content_type": record["content_type"],
                    },
                }
            )
        return documents


def _serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    serialized = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value
    return serialized
