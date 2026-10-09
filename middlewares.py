"""Middlewares de protección contra abuso y acceso no autorizado."""
import time
from collections import defaultdict, deque
from datetime import date
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message


class RateLimitMiddleware(BaseMiddleware):
    """Permite como máximo N mensajes por usuario en una ventana deslizante."""

    def __init__(self, max_messages: int, window_seconds: int) -> None:
        self.max_messages = max_messages
        self.window = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._warned_until: dict[int, float] = {}

    def _purge(self, now: float) -> None:
        for user_id in [u for u, hits in self._hits.items() if not hits or now - hits[-1] > self.window]:
            del self._hits[user_id]
            self._warned_until.pop(user_id, None)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None or self.max_messages <= 0:
            return await handler(event, data)
        now = time.monotonic()
        if len(self._hits) > 1000:
            self._purge(now)
        hits = self._hits[user.id]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_messages:
            if self._warned_until.get(user.id, 0) <= now:
                wait = int(self.window - (now - hits[0])) + 1
                self._warned_until[user.id] = now + wait
                await event.answer(f"Vas muy rápido 😅 Espera unos {wait} s y sigo contigo.")
            return None
        hits.append(now)
        return await handler(event, data)


class UserGuardMiddleware(BaseMiddleware):
    """Aplica lista blanca opcional y un límite diario en memoria."""

    def __init__(self, allowed_ids: frozenset[int], daily_limit: int) -> None:
        self.allowed_ids = allowed_ids
        self.daily_limit = daily_limit
        self._day = date.today()
        self._counts: dict[int, int] = defaultdict(int)

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._counts.clear()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None:
            return await handler(event, data)
        if self.allowed_ids and user.id not in self.allowed_ids:
            await event.answer("Este bot está en modo privado por ahora.")
            return None
        self._reset_if_new_day()
        if self.daily_limit > 0:
            if self._counts[user.id] >= self.daily_limit:
                await event.answer("Alcanzaste el límite diario de mensajes. Inténtalo mañana.")
                return None
            self._counts[user.id] += 1
        return await handler(event, data)
