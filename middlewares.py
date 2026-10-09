"""Middlewares: límite de mensajes por usuario."""
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message


class RateLimitMiddleware(BaseMiddleware):
    """Permite como máximo `max_messages` mensajes por usuario cada `window_seconds`.

    Los mensajes rechazados no cuentan, así que el usuario se libera solo al pasar la ventana.
    """

    def __init__(self, max_messages: int, window_seconds: int) -> None:
        self.max_messages = max_messages
        self.window = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._warned_until: dict[int, float] = {}

    def _purge(self, now: float) -> None:
        """Olvida a los usuarios inactivos para que los diccionarios no crezcan sin fin."""
        for user_id in [u for u, h in self._hits.items() if not h or now - h[-1] > self.window]:
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
            # Avisa una sola vez por bloqueo, no con cada mensaje que sigan mandando
            if self._warned_until.get(user.id, 0) <= now:
                wait = int(self.window - (now - hits[0])) + 1
                self._warned_until[user.id] = now + wait
                await event.answer(f"Vas muy rápido 😅 Espera unos {wait} s y sigo contigo.")
            return None

        hits.append(now)
        return await handler(event, data)
