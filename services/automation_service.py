"""Ejecución persistente y prudente de automatizaciones de Orion."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiogram import Bot

from services.assistant_store import AssistantStore

logger = logging.getLogger(__name__)
_INTERVAL = re.compile(
    r"^cada\s+(\d+)\s*(m|min|minuto|minutos|h|hora|horas|d|día|dia|dias|días)$",
    re.IGNORECASE,
)
_DAILY = re.compile(r"^cada\s+(día|dia)\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)


def next_run_for(schedule: str, now: datetime | None = None) -> datetime | None:
    """Convierte frecuencias seguras de intervalo en una próxima ejecución."""
    now = now or datetime.now(UTC)
    value = schedule.strip()
    daily = _DAILY.match(value)
    if daily:
        hour, minute = int(daily.group(2)), int(daily.group(3))
        if hour > 23 or minute > 59:
            return None
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)
    match = _INTERVAL.match(value)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount < 1:
        return None
    if unit in {"m", "min", "minuto", "minutos"}:
        delta = timedelta(minutes=amount)
    elif unit in {"h", "hora", "horas"}:
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(days=amount)
    return now + delta


class AutomationService:
    def __init__(
        self, store: AssistantStore, telegram_token: str, ai, enabled: bool = True
    ) -> None:
        self.store = store
        self.telegram_token = telegram_token
        self.ai = ai
        self.enabled = enabled
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="orion-automations")
            logger.info("Scheduler de automatizaciones activo")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.run_due_once()
            except Exception:  # noqa: BLE001
                logger.exception("Falló el ciclo de automatizaciones")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), timeout=30)

    async def run_due_once(self) -> int:
        now = datetime.now(UTC)
        executed = 0
        for automation in self.store.due_automations(now):
            executed += 1
            await self._run_one(automation, now)
        return executed

    async def _run_one(self, automation: dict, now: datetime) -> None:
        item_id = automation["id"]
        user_id = automation["user_id"]
        instruction = automation["instruction"]
        try:
            result = await self.ai.ask(user_id, instruction, chat_id=user_id, platform="telegram")
            bot = Bot(token=self.telegram_token)
            try:
                await bot.send_message(user_id, f"🤖 Automatización: {result[:4000]}")
            finally:
                await bot.session.close()
            status = "ok"
            details = result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Automatización %s falló", item_id)
            status = "error"
            details = str(exc)
        next_run = next_run_for(automation["schedule"], now)
        self.store.finish_automation(item_id, next_run, status, details)
