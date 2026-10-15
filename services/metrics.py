"""Registro de métricas de Orion: latencia, tokens, errores, herramientas y modelo usado.

Diseño minimalista: una única tabla SQLite y acceso síncrono (las escrituras son mínimas
y el cuello de botella real es siempre la API del LLM, no un INSERT).

Uso:
    from services.metrics import MetricsStore
    m = MetricsStore("reminders.db")
    m.record_turn(user_id=123, platform="telegram", model="llama3-70b", latency_s=1.2,
                  tokens_in=400, tokens_out=150, tools_used=["web_search"], success=True)
    m.record_error(user_id=123, platform="telegram", error_type="rate_limit", detail="429 groq")
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4


class MetricsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metric_turns (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    model TEXT NOT NULL,
                    latency_s REAL NOT NULL,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    tools_used TEXT NOT NULL DEFAULT '[]',
                    success INTEGER NOT NULL DEFAULT 1,
                    agent_name TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metric_errors (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metric_turns_user
                    ON metric_turns(user_id, platform, created_at);
                CREATE INDEX IF NOT EXISTS idx_metric_errors_user
                    ON metric_errors(user_id, created_at);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def record_turn(
        self,
        user_id: int,
        platform: str,
        model: str,
        latency_s: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tools_used: list[str] | None = None,
        success: bool = True,
        agent_name: str = "general",
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO metric_turns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex[:16],
                    user_id,
                    platform,
                    model[:100],
                    round(latency_s, 3),
                    max(tokens_in, 0),
                    max(tokens_out, 0),
                    json.dumps(tools_used or [], ensure_ascii=False),
                    1 if success else 0,
                    agent_name[:50],
                    self._now(),
                ),
            )

    def record_error(
        self,
        user_id: int,
        platform: str,
        error_type: str,
        detail: str = "",
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO metric_errors VALUES (?,?,?,?,?,?)",
                (
                    uuid4().hex[:16],
                    user_id,
                    platform,
                    error_type[:80],
                    detail[:500],
                    self._now(),
                ),
            )

    def summary(self, user_id: int, platform: str, days: int = 7) -> dict:
        """Resumen de los últimos N días para el usuario."""
        since = f"{datetime.now(UTC).date().isoformat()}T00:00:00"
        # Fecha N días atrás (SQLite no tiene date arithmetic nativo, calculamos en Python)
        from datetime import timedelta
        since = (datetime.now(UTC) - timedelta(days=max(days, 1))).isoformat()

        with closing(sqlite3.connect(self.db_path)) as conn:
            turns_row = conn.execute(
                "SELECT COUNT(*), AVG(latency_s), SUM(tokens_in), SUM(tokens_out), "
                "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) "
                "FROM metric_turns WHERE user_id=? AND platform=? AND created_at>=?",
                (user_id, platform, since),
            ).fetchone()

            tool_rows = conn.execute(
                "SELECT tools_used FROM metric_turns "
                "WHERE user_id=? AND platform=? AND created_at>=? AND tools_used!='[]'",
                (user_id, platform, since),
            ).fetchall()

            error_rows = conn.execute(
                "SELECT error_type, COUNT(*) FROM metric_errors "
                "WHERE user_id=? AND platform=? AND created_at>=? "
                "GROUP BY error_type ORDER BY COUNT(*) DESC LIMIT 5",
                (user_id, platform, since),
            ).fetchall()

            agent_rows = conn.execute(
                "SELECT agent_name, COUNT(*) FROM metric_turns "
                "WHERE user_id=? AND platform=? AND created_at>=? "
                "GROUP BY agent_name ORDER BY COUNT(*) DESC",
                (user_id, platform, since),
            ).fetchall()

        # Conteo de herramientas
        tool_counts: dict[str, int] = {}
        for (raw,) in tool_rows:
            try:
                for t in json.loads(raw):
                    tool_counts[t] = tool_counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:5]

        total, avg_lat, tok_in, tok_out, ok = turns_row or (0, None, 0, 0, 0)
        return {
            "days": days,
            "total_turns": total or 0,
            "success_rate": round((ok / total * 100) if total else 100, 1),
            "avg_latency_s": round(avg_lat, 2) if avg_lat else None,
            "tokens_in": tok_in or 0,
            "tokens_out": tok_out or 0,
            "top_tools": top_tools,
            "errors": [(etype, cnt) for etype, cnt in error_rows],
            "agents": [(name, cnt) for name, cnt in agent_rows],
        }

    def format_summary(self, user_id: int, platform: str, days: int = 7) -> str:
        s = self.summary(user_id, platform, days)
        if s["total_turns"] == 0:
            return f"Sin actividad registrada en los últimos {days} días."

        lines = [f"Métricas — últimos {s['days']} días:"]
        lines.append(f"  Turnos: {s['total_turns']}  |  Éxito: {s['success_rate']}%")
        if s["avg_latency_s"] is not None:
            lines.append(f"  Latencia promedio: {s['avg_latency_s']}s")
        if s["tokens_in"] or s["tokens_out"]:
            lines.append(f"  Tokens — entrada: {s['tokens_in']:,}  salida: {s['tokens_out']:,}")
        if s["top_tools"]:
            tools_str = ", ".join(f"{t} ×{n}" for t, n in s["top_tools"])
            lines.append(f"  Herramientas más usadas: {tools_str}")
        if s["agents"] and len(s["agents"]) > 1:
            agents_str = ", ".join(f"{a} ×{n}" for a, n in s["agents"])
            lines.append(f"  Agentes: {agents_str}")
        if s["errors"]:
            err_str = ", ".join(f"{e} ×{n}" for e, n in s["errors"])
            lines.append(f"  Errores: {err_str}")
        return "\n".join(lines)
