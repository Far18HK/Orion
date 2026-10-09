"""Servicio de recordatorios: programa mensajes futuros, persistentes en SQLite."""
import re
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DURATION_PATTERN = re.compile(r"^(\d+)([mhd])$")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class ReminderError(Exception):
    """Error amigable para el usuario."""


def parse_duration(text: str) -> timedelta:
    """Convierte '10m', '2h' o '1d' en un timedelta."""
    match = DURATION_PATTERN.match(text.strip().lower())
    if not match:
        raise ReminderError(
            "Formato de tiempo inválido. Usa algo como: 10m, 2h o 1d (minutos, horas, días)."
        )
    amount, unit = match.groups()
    return timedelta(**{UNITS[unit]: int(amount)})


async def _send_reminder(token: str, chat_id: int, text: str) -> None:
    """Función a nivel de módulo: así APScheduler puede serializarla al guardarla en SQLite."""
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id, f"⏰ Recordatorio: {text}")
    finally:
        await bot.session.close()


class ReminderService:
    def __init__(self, token: str, db_path: str = "reminders.db") -> None:
        self.token = token
        # SQLAlchemyJobStore guarda los recordatorios en disco: sobreviven a un reinicio del bot
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
        )

    def start(self) -> None:
        self.scheduler.start()

    def schedule(self, chat_id: int, delay: timedelta, text: str) -> None:
        """Agenda un recordatorio para dentro de `delay`."""
        run_date = datetime.now() + delay
        self.scheduler.add_job(
            _send_reminder,
            trigger="date",
            run_date=run_date,
            args=[self.token, chat_id, text],
            misfire_grace_time=3600,  # Si el bot estuvo apagado, lo manda al volver (hasta 1h después)
        )
