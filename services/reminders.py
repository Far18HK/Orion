"""Servicio de recordatorios: mensajes futuros únicos o repetidos, persistentes en SQLite."""
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.schedule_parser import ReminderError, parse_duration, parse_reminder  # noqa: F401

MAX_PER_CHAT = 20  # Tope de recordatorios pendientes por chat


async def _send_reminder(token: str, chat_id: int, text: str) -> None:
    """Función a nivel de módulo: así APScheduler puede serializarla al guardarla en SQLite.

    Ojo: no cambiar su firma, los recordatorios ya guardados en reminders.db la usan.
    """
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id, f"⏰ Recordatorio: {text}")
    finally:
        await bot.session.close()


async def _send_discord_reminder(token: str, channel_id: int, text: str) -> None:
    """Igual que _send_reminder pero por la API REST de Discord (no necesita el cliente abierto).

    También a nivel de módulo y con la misma firma, para que APScheduler la pueda guardar.
    """
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (asistente, 1.0)"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(
            url, headers=headers, json={"content": f"⏰ Recordatorio: {text}"[:2000]}
        ) as resp:
            resp.raise_for_status()


@dataclass(frozen=True)
class ReminderInfo:
    id: str
    text: str
    description: str
    recurring: bool
    next_run: datetime | None


class ReminderService:
    def __init__(
        self,
        token: str,
        db_path: str = "reminders.db",
        default_timezone: str | None = None,
        discord_token: str | None = None,
    ) -> None:
        self.token = token
        self.discord_token = discord_token  # Si no hay, los recordatorios pedidos desde Discord se rechazan
        self.db_path = db_path
        self.default_timezone = default_timezone  # Zona para chats que no han usado /zona
        # SQLAlchemyJobStore guarda los recordatorios en disco: sobreviven a un reinicio del bot
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
        )
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_settings "
                "(chat_id INTEGER PRIMARY KEY, timezone TEXT NOT NULL)"
            )

    def start(self) -> None:
        self.scheduler.start()

    # ---- Zona horaria por chat ----

    def get_timezone(self, chat_id: int) -> ZoneInfo | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT timezone FROM chat_settings WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        name = row[0] if row else self.default_timezone
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except Exception:  # Zona guardada que ya no existe: se trata como no configurada
            return None

    def set_timezone(self, chat_id: int, name: str) -> ZoneInfo:
        tz = ZoneInfo(name)  # Lanza error si no es válida
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_settings (chat_id, timezone) VALUES (?, ?)",
                (chat_id, tz.key),
            )
        return tz

    # ---- Crear, listar y cancelar ----

    def _chat_jobs(self, chat_id: int) -> list:
        return [
            j
            for j in self.scheduler.get_jobs()
            if len(j.args) >= 3 and j.args[1] == chat_id
        ]

    def create(self, chat_id: int, args: str, platform: str = "telegram") -> tuple[str, str]:
        """Interpreta '<cuándo> <mensaje>', lo agenda y devuelve (mensaje, descripción del cuándo)."""
        tz = self.get_timezone(chat_id)
        schedule, text = parse_reminder(args, tz)

        if len(self._chat_jobs(chat_id)) >= MAX_PER_CHAT:
            raise ReminderError(
                f"Ya tienes {MAX_PER_CHAT} recordatorios pendientes 😅 Cancela alguno con /recordatorios."
            )

        if platform == "discord":
            if not self.discord_token:
                raise ReminderError("Los recordatorios por Discord no están configurados.")
            send, token = _send_discord_reminder, self.discord_token
        else:
            send, token = _send_reminder, self.token

        common = {
            "args": [token, chat_id, text],
            "id": uuid4().hex,
            "name": schedule.description,
            "misfire_grace_time": 3600,  # Si el bot estuvo apagado, lo manda al volver (hasta 1h después)
        }
        if schedule.kind == "once":
            self.scheduler.add_job(send, trigger="date", run_date=schedule.run_at, **common)
        elif schedule.kind == "interval":
            self.scheduler.add_job(
                send,
                trigger="interval",
                seconds=int(schedule.interval.total_seconds()),
                coalesce=True,  # Si se acumularon varios por estar apagado, manda uno solo
                **common,
            )
        else:  # daily / weekly: cron en la zona del chat (respeta el horario de verano)
            cron = {"hour": schedule.hour, "minute": schedule.minute, "timezone": tz.key}
            if schedule.kind == "weekly":
                cron["day_of_week"] = schedule.weekday
            self.scheduler.add_job(
                send, trigger="cron", coalesce=True, **cron, **common
            )
        return text, schedule.description

    def list_jobs(self, chat_id: int) -> list[ReminderInfo]:
        """Recordatorios pendientes del chat, el más próximo primero."""
        far = datetime.max.replace(tzinfo=timezone.utc)
        jobs = sorted(self._chat_jobs(chat_id), key=lambda j: j.next_run_time or far)
        return [self._info(j) for j in jobs]

    def cancel(self, chat_id: int, job_id: str) -> ReminderInfo | None:
        """Cancela un recordatorio del chat. Devuelve None si no existe o es de otro chat."""
        job = self.scheduler.get_job(job_id)
        if job is None or len(job.args) < 3 or job.args[1] != chat_id:
            return None
        info = self._info(job)
        job.remove()
        return info

    @staticmethod
    def _info(job) -> ReminderInfo:
        description = job.name if job.name and job.name != "_send_reminder" else "una vez"
        return ReminderInfo(
            id=job.id,
            text=job.args[2],
            description=description,
            recurring=type(job.trigger).__name__ != "DateTrigger",
            next_run=job.next_run_time,
        )
