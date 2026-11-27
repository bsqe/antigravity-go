"""SQLite WAL persistence for projects, sessions, messages, attachments, and devices."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import (
    AttachmentInfo,
    DeviceInfo,
    MessageRecord,
    ProjectInfo,
    SessionInfo,
    ToolEventRecord,
)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class DeviceRecord:
    id: str
    name: str
    platform: str
    token_hash: str
    created_at_ms: int
    last_seen_at_ms: int
    revoked_at_ms: int | None


@dataclass(slots=True)
class AttachmentDiskRecord:
    info: AttachmentInfo
    disk_path: Path


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL,
    last_seen_at_ms INTEGER NOT NULL,
    revoked_at_ms INTEGER
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_seq INTEGER NOT NULL DEFAULT 0,
    source_desktop_id TEXT,
    system_context TEXT,
    synced_desktop_step INTEGER NOT NULL DEFAULT -1,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    disk_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    thoughts TEXT NOT NULL DEFAULT '',
    tool_events_json TEXT NOT NULL DEFAULT '[]',
    attachment_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    seq INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_seq
    ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS session_events (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""


class BridgeStore:
    """Thread-safe SQLite repository with WAL enabled."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._ensure_session_migrations()
            self._conn.commit()

    def _ensure_session_migrations(self) -> None:
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "source_desktop_id" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN source_desktop_id TEXT")
        if "system_context" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN system_context TEXT")
        if "synced_desktop_step" not in cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN synced_desktop_step INTEGER NOT NULL DEFAULT -1"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_source_desktop ON sessions(source_desktop_id)"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── Devices ───────────────────────────────────────────────────────────────

    def create_device(self, name: str, platform: str, token_hash: str) -> DeviceRecord:
        ts = now_ms()
        device_id = f"dev_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO devices (id, name, platform, token_hash, created_at_ms, last_seen_at_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, name, platform, token_hash, ts, ts),
            )
            self._conn.commit()
        return DeviceRecord(
            id=device_id,
            name=name,
            platform=platform,
            token_hash=token_hash,
            created_at_ms=ts,
            last_seen_at_ms=ts,
            revoked_at_ms=None,
        )

    def get_active_device_by_hash(self, token_hash: str) -> DeviceRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM devices WHERE token_hash = ? AND revoked_at_ms IS NULL",
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        return DeviceRecord(
            id=row["id"],
            name=row["name"],
            platform=row["platform"],
            token_hash=row["token_hash"],
            created_at_ms=row["created_at_ms"],
            last_seen_at_ms=row["last_seen_at_ms"],
            revoked_at_ms=row["revoked_at_ms"],
        )

    def touch_device(self, device_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET last_seen_at_ms = ? WHERE id = ?",
                (now_ms(), device_id),
            )
            self._conn.commit()

    def list_devices(self) -> list[DeviceInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY created_at_ms DESC"
            ).fetchall()
        return [
            DeviceInfo(
                id=r["id"],
                name=r["name"],
                platform=r["platform"],
                createdAtMs=r["created_at_ms"],
                lastSeenAtMs=r["last_seen_at_ms"],
                revoked=r["revoked_at_ms"] is not None,
            )
            for r in rows
        ]

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE devices SET revoked_at_ms = ? WHERE id = ? AND revoked_at_ms IS NULL",
                (now_ms(), device_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ── Projects ──────────────────────────────────────────────────────────────

    def create_project(self, name: str, path: str) -> ProjectInfo:
        ts = now_ms()
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM projects WHERE path = ?", (path,)
            ).fetchone()
            if existing is not None:
                cnt = self._conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE project_id = ?", (existing["id"],)
                ).fetchone()[0]
                return ProjectInfo(
                    id=existing["id"],
                    name=existing["name"],
                    path=existing["path"],
                    createdAtMs=existing["created_at_ms"],
                    sessionCount=cnt,
                )
            self._conn.execute(
                "INSERT INTO projects (id, name, path, created_at_ms) VALUES (?, ?, ?, ?)",
                (project_id, name, path, ts),
            )
            self._conn.commit()
        return ProjectInfo(id=project_id, name=name, path=path, createdAtMs=ts, sessionCount=0)

    def list_projects(self) -> list[ProjectInfo]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.*, COUNT(s.id) AS session_count
                FROM projects p
                LEFT JOIN sessions s ON s.project_id = p.id
                GROUP BY p.id
                ORDER BY p.created_at_ms DESC
                """
            ).fetchall()
        return [
            ProjectInfo(
                id=r["id"],
                name=r["name"],
                path=r["path"],
                createdAtMs=r["created_at_ms"],
                sessionCount=r["session_count"],
            )
            for r in rows
        ]

    def get_project(self, project_id: str) -> ProjectInfo | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.*, COUNT(s.id) AS session_count
                FROM projects p
                LEFT JOIN sessions s ON s.project_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ProjectInfo(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            createdAtMs=row["created_at_ms"],
            sessionCount=row["session_count"],
        )

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Sessions ──────────────────────────────────────────────────────────────

    def create_session(
        self,
        project_id: str,
        title: str,
        model: str | None = None,
        *,
        source_desktop_id: str | None = None,
        system_context: str | None = None,
        synced_desktop_step: int = -1,
    ) -> SessionInfo | None:
        proj = self.get_project(project_id)
        if proj is None:
            return None
        ts = now_ms()
        session_id = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    id, project_id, title, model, status, last_seq,
                    source_desktop_id, system_context, synced_desktop_step,
                    created_at_ms, updated_at_ms
                )
                VALUES (?, ?, ?, ?, 'idle', 0, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project_id,
                    title,
                    model,
                    source_desktop_id,
                    system_context,
                    synced_desktop_step,
                    ts,
                    ts,
                ),
            )
            self._conn.commit()
        return SessionInfo(
            id=session_id,
            projectId=project_id,
            projectName=proj.name,
            title=title,
            model=model,
            status="idle",
            lastSeq=0,
            sourceDesktopId=source_desktop_id,
            createdAtMs=ts,
            updatedAtMs=ts,
        )

    def get_all_session_ids(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM sessions").fetchall()
        return {str(r["id"]) for r in rows}

    def list_linked_desktop_sessions(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, source_desktop_id
                FROM sessions
                WHERE source_desktop_id IS NOT NULL AND source_desktop_id != ''
                ORDER BY updated_at_ms DESC
                """
            ).fetchall()
        mapping: dict[str, str] = {}
        for r in rows:
            did = str(r["source_desktop_id"])
            if did not in mapping:
                mapping[did] = str(r["id"])
        return mapping

    def find_session_by_desktop_id(
        self, desktop_id: str, project_id: str | None = None
    ) -> SessionInfo | None:
        query = """
            SELECT s.*, p.name AS project_name
            FROM sessions s
            JOIN projects p ON p.id = s.project_id
            WHERE s.source_desktop_id = ?
        """
        params: list[Any] = [desktop_id]
        if project_id is not None:
            query += " AND s.project_id = ?"
            params.append(project_id)
        query += " ORDER BY s.updated_at_ms DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, tuple(params)).fetchone()
        return self._row_to_session(row) if row is not None else None

    def get_session_runtime_context(self, session_id: str) -> tuple[str | None, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT system_context, synced_desktop_step FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None, -1
        return row["system_context"], int(row["synced_desktop_step"])

    def import_desktop_messages(
        self,
        session_id: str,
        messages: list[Any],
        *,
        max_step: int,
        system_context: str,
    ) -> int:
        """Idempotently append only desktop turns newer than `synced_desktop_step`."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seq, synced_desktop_step FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session: {session_id}")
            seq = int(row["last_seq"])
            prev_step = int(row["synced_desktop_step"])
            added = 0
            for msg in messages:
                step_idx = int(getattr(msg, "step_index", -1))
                if step_idx <= prev_step:
                    continue
                seq += 1
                msg_id = f"msg_{uuid.uuid4().hex[:14]}"
                tools_json = json.dumps(
                    [t.model_dump(mode="json") for t in getattr(msg, "tool_events", [])],
                    ensure_ascii=False,
                )
                created = int(getattr(msg, "created_at_ms", 0) or now_ms())
                updated = int(getattr(msg, "updated_at_ms", 0) or created)
                self._conn.execute(
                    """
                    INSERT INTO messages
                    (id, session_id, turn_id, role, content, thoughts, tool_events_json, attachment_ids_json, status, seq, created_at_ms, updated_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'completed', ?, ?, ?)
                    """,
                    (
                        msg_id,
                        session_id,
                        str(getattr(msg, "turn_id", f"desktop_{step_idx}")),
                        str(getattr(msg, "role", "assistant")),
                        str(getattr(msg, "content", "")),
                        str(getattr(msg, "thoughts", "")),
                        tools_json,
                        seq,
                        created,
                        updated,
                    ),
                )
                added += 1

            new_step = max(prev_step, max_step)
            self._conn.execute(
                """
                UPDATE sessions
                SET last_seq = ?, synced_desktop_step = ?, system_context = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (seq, new_step, system_context, now_ms(), session_id),
            )
            self._conn.commit()
            return added

    def list_sessions(self, project_id: str | None = None) -> list[SessionInfo]:
        query = """
            SELECT s.*, p.name AS project_name
            FROM sessions s
            JOIN projects p ON p.id = s.project_id
        """
        params: tuple[Any, ...] = ()
        if project_id:
            query += " WHERE s.project_id = ?"
            params = (project_id,)
        query += " ORDER BY s.updated_at_ms DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_session(self, session_id: str) -> SessionInfo | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.*, p.name AS project_name
                FROM sessions s
                JOIN projects p ON p.id = s.project_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> SessionInfo | None:
        fields: list[str] = []
        values: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if model is not None:
            fields.append("model = ?")
            values.append(model)
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        fields.append("updated_at_ms = ?")
        values.append(now_ms())
        values.append(session_id)

        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def next_seq(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seq FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session: {session_id}")
            seq = int(row["last_seq"]) + 1
            self._conn.execute(
                "UPDATE sessions SET last_seq = ?, updated_at_ms = ? WHERE id = ?",
                (seq, now_ms(), session_id),
            )
            self._conn.commit()
            return seq

    # ── Attachments ───────────────────────────────────────────────────────────

    def create_attachment(
        self,
        *,
        session_id: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        disk_path: Path,
        sha256: str,
    ) -> AttachmentInfo:
        ts = now_ms()
        att_id = f"att_{uuid.uuid4().hex[:14]}"
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO attachments (id, session_id, filename, mime_type, size_bytes, disk_path, sha256, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (att_id, session_id, filename, mime_type, size_bytes, str(disk_path), sha256, ts),
            )
            self._conn.commit()
        return AttachmentInfo(
            id=att_id,
            sessionId=session_id,
            filename=filename,
            mimeType=mime_type,
            sizeBytes=size_bytes,
            sha256=sha256,
            createdAtMs=ts,
        )

    def get_attachment(self, attachment_id: str) -> AttachmentDiskRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
        if row is None:
            return None
        return AttachmentDiskRecord(
            info=AttachmentInfo(
                id=row["id"],
                sessionId=row["session_id"],
                filename=row["filename"],
                mimeType=row["mime_type"],
                sizeBytes=row["size_bytes"],
                sha256=row["sha256"],
                createdAtMs=row["created_at_ms"],
            ),
            disk_path=Path(row["disk_path"]),
        )

    def get_attachments_for_session(
        self, session_id: str, attachment_ids: list[str]
    ) -> list[AttachmentDiskRecord]:
        if not attachment_ids:
            return []
        placeholders = ",".join("?" for _ in attachment_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM attachments WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *attachment_ids),
            ).fetchall()
        by_id = {
            r["id"]: AttachmentDiskRecord(
                info=AttachmentInfo(
                    id=r["id"],
                    sessionId=r["session_id"],
                    filename=r["filename"],
                    mimeType=r["mime_type"],
                    sizeBytes=r["size_bytes"],
                    sha256=r["sha256"],
                    createdAtMs=r["created_at_ms"],
                ),
                disk_path=Path(r["disk_path"]),
            )
            for r in rows
        }
        return [by_id[aid] for aid in attachment_ids if aid in by_id]

    # ── Messages & Replay Event Log ───────────────────────────────────────────

    def create_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        content: str = "",
        thoughts: str = "",
        attachment_ids: list[str] | None = None,
        status: str = "completed",
    ) -> MessageRecord:
        ts = now_ms()
        msg_id = f"msg_{uuid.uuid4().hex[:14]}"
        att_ids = attachment_ids or []
        seq = self.next_seq(session_id)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO messages
                (id, session_id, turn_id, role, content, thoughts, tool_events_json, attachment_ids_json, status, seq, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)
                """,
                (
                    msg_id,
                    session_id,
                    turn_id,
                    role,
                    content,
                    thoughts,
                    json.dumps(att_ids),
                    status,
                    seq,
                    ts,
                    ts,
                ),
            )
            self._conn.commit()
        atts = [r.info for r in self.get_attachments_for_session(session_id, att_ids)]
        return MessageRecord(
            id=msg_id,
            sessionId=session_id,
            turnId=turn_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            thoughts=thoughts,
            toolEvents=[],
            attachments=atts,
            status=status,  # type: ignore[arg-type]
            errorMessage=None,
            seq=seq,
            createdAtMs=ts,
            updatedAtMs=ts,
        )

    def append_message_delta(
        self,
        message_id: str,
        *,
        text_delta: str = "",
        thought_delta: str = "",
        tool_event: ToolEventRecord | None = None,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT content, thoughts, tool_events_json FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return
            content = row["content"] + text_delta
            thoughts = row["thoughts"] + thought_delta
            tools = json.loads(row["tool_events_json"])
            if tool_event is not None:
                replaced = False
                for idx, item in enumerate(tools):
                    if item.get("id") == tool_event.id:
                        tools[idx] = tool_event.model_dump(mode="json")
                        replaced = True
                        break
                if not replaced:
                    tools.append(tool_event.model_dump(mode="json"))
            self._conn.execute(
                """
                UPDATE messages
                SET content = ?, thoughts = ?, tool_events_json = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (content, thoughts, json.dumps(tools), now_ms(), message_id),
            )
            self._conn.commit()

    def finalize_message(
        self,
        message_id: str,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE messages
                SET status = ?, error_message = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (status, error_message, now_ms(), message_id),
            )
            self._conn.commit()

    def list_messages_page(
        self, session_id: str, *, before_seq: int | None = None, limit: int = 50
    ) -> tuple[list[MessageRecord], bool]:
        clamped = max(1, min(limit, 200))
        with self._lock:
            if before_seq is not None:
                rows = self._conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE session_id = ? AND seq < ?
                    ORDER BY seq DESC
                    LIMIT ?
                    """,
                    (session_id, before_seq, clamped + 1),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY seq DESC
                    LIMIT ?
                    """,
                    (session_id, clamped + 1),
                ).fetchall()
        has_more = len(rows) > clamped
        sliced = list(reversed(rows[:clamped]))
        out: list[MessageRecord] = []
        for r in sliced:
            att_ids = json.loads(r["attachment_ids_json"])
            atts = [a.info for a in self.get_attachments_for_session(session_id, att_ids)]
            tools = [ToolEventRecord.model_validate(t) for t in json.loads(r["tool_events_json"])]
            out.append(
                MessageRecord(
                    id=r["id"],
                    sessionId=r["session_id"],
                    turnId=r["turn_id"],
                    role=r["role"],
                    content=r["content"],
                    thoughts=r["thoughts"],
                    toolEvents=tools,
                    attachments=atts,
                    status=r["status"],
                    errorMessage=r["error_message"],
                    seq=r["seq"],
                    createdAtMs=r["created_at_ms"],
                    updatedAtMs=r["updated_at_ms"],
                )
            )
        return out, has_more

    def append_stream_event(self, session_id: str, event_payload: dict[str, Any]) -> int:
        seq = self.next_seq(session_id)
        payload = {**event_payload, "seq": seq}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO session_events (session_id, seq, event_json, created_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, seq, json.dumps(payload, ensure_ascii=False), now_ms()),
            )
            # Keep bounded replay window (last 2000 events per session)
            self._conn.execute(
                """
                DELETE FROM session_events
                WHERE session_id = ? AND seq <= ?
                """,
                (session_id, max(0, seq - 2000)),
            )
            self._conn.commit()
        return seq

    def list_stream_events_after(self, session_id: str, after_seq: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_json FROM session_events
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT 500
                """,
                (session_id, after_seq),
            ).fetchall()
        return [json.loads(r["event_json"]) for r in rows]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionInfo:
        return SessionInfo(
            id=row["id"],
            projectId=row["project_id"],
            projectName=row["project_name"],
            title=row["title"],
            model=row["model"],
            status=row["status"],
            lastSeq=row["last_seq"],
            sourceDesktopId=row["source_desktop_id"],
            createdAtMs=row["created_at_ms"],
            updatedAtMs=row["updated_at_ms"],
        )
