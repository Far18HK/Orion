"""Persistencia SQLite de las funciones autónomas de Orion."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4


class AssistantStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assistant_tasks (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automations (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, instruction TEXT NOT NULL,
                    schedule TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, next_run TEXT, last_run TEXT,
                    last_status TEXT, last_result TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
                    action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
                    phase TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
                    action TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(automations)")}
            for name, definition in (
                ("next_run", "TEXT"),
                ("last_run", "TEXT"),
                ("last_status", "TEXT"),
                ("last_result", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE automations ADD COLUMN {name} {definition}")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def remember(self, user_id: int, content: str) -> str:
        item_id = uuid4().hex[:12]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?)", (item_id, user_id, content, self._now())
            )
        return item_id

    def memories(self, user_id: int, query: str = "", limit: int = 10) -> list[tuple[str, str]]:
        sql = "SELECT id, content FROM memories WHERE user_id = ?"
        args: list[object] = [user_id]
        if query.strip():
            sql += " AND content LIKE ?"
            args.append(f"%{query.strip()}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(min(max(limit, 1), 50))
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(sql, args).fetchall()

    def forget(self, user_id: int, item_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return (
                conn.execute(
                    "DELETE FROM memories WHERE id = ? AND user_id = ?", (item_id, user_id)
                ).rowcount
                > 0
            )

    def add_task(self, user_id: int, title: str) -> str:
        item_id = uuid4().hex[:12]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO assistant_tasks VALUES (?, ?, ?, 'open', ?)",
                (item_id, user_id, title, self._now()),
            )
        return item_id

    def tasks(self, user_id: int, include_done: bool = False) -> list[tuple[str, str, str]]:
        sql = "SELECT id, title, status FROM assistant_tasks WHERE user_id = ?"
        args: list[object] = [user_id]
        if not include_done:
            sql += " AND status = 'open'"
        sql += " ORDER BY created_at DESC LIMIT 50"
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(sql, args).fetchall()

    def complete_task(self, user_id: int, item_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return (
                conn.execute(
                    "UPDATE assistant_tasks SET status = 'done' WHERE id = ? AND user_id = ?",
                    (item_id, user_id),
                ).rowcount
                > 0
            )

    def request_approval(self, user_id: int, action: str, payload: dict) -> str:
        item_id = uuid4().hex[:12]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, 'pending', ?)",
                (item_id, user_id, action, json.dumps(payload, ensure_ascii=False), self._now()),
            )
        return item_id

    def get_approval(self, user_id: int, item_id: str) -> tuple[str, dict] | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT action, payload FROM approvals "
                "WHERE id = ? AND user_id = ? AND status = 'pending'",
                (item_id, user_id),
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def resolve_approval(self, user_id: int, item_id: str, status: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return (
                conn.execute(
                    "UPDATE approvals SET status = ? "
                    "WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (status, item_id, user_id),
                ).rowcount
                > 0
            )

    def add_automation(
        self, user_id: int, instruction: str, schedule: str, next_run: datetime | None = None
    ) -> str:
        item_id = uuid4().hex[:12]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO automations "
                "(id,user_id,instruction,schedule,enabled,created_at,next_run) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    item_id,
                    user_id,
                    instruction,
                    schedule,
                    self._now(),
                    next_run.isoformat() if next_run else None,
                ),
            )
        return item_id

    def due_automations(self, now: datetime) -> list[dict]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id,user_id,instruction,schedule,next_run FROM automations "
                "WHERE enabled = 1 AND (next_run IS NULL OR next_run <= ?) LIMIT 20",
                (now.isoformat(),),
            ).fetchall()
        return [
            dict(zip(("id", "user_id", "instruction", "schedule", "next_run"), row, strict=True))
            for row in rows
        ]

    def finish_automation(
        self, item_id: str, next_run: datetime | None, status: str, result: str
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE automations SET next_run=?, last_run=?, last_status=?, "
                "last_result=?, enabled=? WHERE id=?",
                (
                    next_run.isoformat() if next_run else None,
                    self._now(),
                    status,
                    result[:4000],
                    1 if next_run else 0,
                    item_id,
                ),
            )

    def audit(self, user_id: int, platform: str, action: str, details: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
                (uuid4().hex[:12], user_id, platform, action, details[:2000], self._now()),
            )

    def audit_entries(self, user_id: int, limit: int = 20) -> list[tuple[str, str, str]]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT action, details, created_at FROM audit_log "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, min(max(limit, 1), 50)),
            ).fetchall()

    def save_checkpoint(self, user_id: int, platform: str, phase: str, payload: dict) -> None:
        checkpoint_id = f"{platform}:{user_id}"
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET phase=excluded.phase, payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (
                    checkpoint_id,
                    user_id,
                    platform,
                    phase,
                    json.dumps(payload, ensure_ascii=False),
                    self._now(),
                ),
            )

    def clear_checkpoint(self, user_id: int, platform: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("DELETE FROM checkpoints WHERE id = ?", (f"{platform}:{user_id}",))

    def begin_operation(
        self, user_id: int, platform: str, operation_id: str, action: str
    ) -> str | None:
        """Devuelve el resultado previo si ya se ejecutó; None reserva la operación."""
        now = self._now()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                "SELECT status, result FROM operations WHERE id = ? AND user_id = ? AND platform = ?",
                (operation_id, user_id, platform),
            ).fetchone()
            if row and row[0] == "done":
                return row[1]
            conn.execute(
                "INSERT OR REPLACE INTO operations "
                "(id,user_id,platform,action,status,result,created_at,updated_at) "
                "VALUES (?, ?, ?, ?, 'running', '', COALESCE((SELECT created_at FROM operations WHERE id = ?), ?), ?)",
                (operation_id, user_id, platform, action, operation_id, now, now),
            )
        return None

    def finish_operation(self, user_id: int, platform: str, operation_id: str, result: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE operations SET status='done', result=?, updated_at=? "
                "WHERE id=? AND user_id=? AND platform=?",
                (result[:6000], self._now(), operation_id, user_id, platform),
            )

    def project(self, user_id: int, name: str, description: str = "") -> str:
        project_id = uuid4().hex[:12]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO projects (id,user_id,name,description,created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id,name) DO UPDATE SET description=excluded.description",
                (project_id, user_id, name.strip(), description.strip(), self._now()),
            )
        return project_id

    def projects(self, user_id: int) -> list[tuple[str, str, str]]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT id,name,description FROM projects WHERE user_id=? ORDER BY name LIMIT 50",
                (user_id,),
            ).fetchall()

    def pending_checkpoint(self, user_id: int, platform: str) -> tuple[str, str] | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT phase, updated_at FROM checkpoints WHERE id=?",
                (f"{platform}:{user_id}",),
            ).fetchone()
        return row if row else None

    def get_checkpoint(self, user_id: int, platform: str) -> tuple[str, dict, str] | None:
        """Turno guardado de este usuario: (fase, datos, fecha). None si no hay ninguno.

        Fases: ``running`` (turno en curso o interrumpido por un reinicio) y
        ``awaiting_approval`` (pausado esperando un sí/no del usuario).
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT phase, payload, updated_at FROM checkpoints WHERE id=?",
                (f"{platform}:{user_id}",),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[1])
        except json.JSONDecodeError:
            payload = {}
        return row[0], payload if isinstance(payload, dict) else {}, row[2]
