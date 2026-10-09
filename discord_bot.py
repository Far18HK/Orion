"""Frente de Discord: el mismo agente de Telegram, atendiendo mensajes directos y menciones.

Se activa solo si defines DISCORD_TOKEN. Corre en el mismo proceso y event loop que Telegram.
En servidores responde cuando lo mencionas (@bot); en mensajes directos responde siempre.
"""
import logging
import time
from collections import defaultdict, deque

import discord

from handlers.chat import split_text
from services.groq_service import GroqError, GroqService

logger = logging.getLogger(__name__)

DISCORD_LIMIT = 2000  # Máximo de caracteres por mensaje en Discord

HELP_TEXT = (
    "Soy tu asistente. Escríbeme normal (en servidores, mencióname) y yo decido qué herramienta "
    "usar: buscar en internet, leer enlaces, recordatorios, notas, calculadora, clima, hora y GitHub.\n"
    "Comandos: `id` (tu id de Discord), `reset` (borra la memoria de la conversación), `ayuda`."
)


class DiscordFront(discord.Client):
    def __init__(self, ai: GroqService, rate_messages: int, rate_window: int) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # Hay que activarlo también en el Developer Portal
        super().__init__(intents=intents)
        self.ai = ai
        self.rate_messages = rate_messages
        self.rate_window = rate_window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def _rate_limited(self, user_id: int) -> int:
        """Segundos que debe esperar el usuario (0 si puede escribir)."""
        if self.rate_messages <= 0:
            return 0
        now = time.monotonic()
        hits = self._hits[user_id]
        while hits and now - hits[0] > self.rate_window:
            hits.popleft()
        if len(hits) >= self.rate_messages:
            return int(self.rate_window - (now - hits[0])) + 1
        hits.append(now)
        return 0

    async def on_ready(self) -> None:
        logger.info("Discord conectado como %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None:
            return
        is_dm = message.guild is None
        if not is_dm and self.user not in message.mentions:
            return  # En servidores solo respondemos si nos mencionan

        text = message.content
        for mention in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            text = text.replace(mention, "")
        text = text.strip()
        if not text:
            await message.reply(HELP_TEXT)
            return

        command = text.lower().lstrip("!/")
        if command == "id":
            await message.reply(f"Tu id de Discord es {message.author.id}")
            return
        if command == "reset":
            self.ai.reset(message.author.id, platform="discord")
            await message.reply("Listo, memoria borrada ✨")
            return
        if command in ("ayuda", "help"):
            await message.reply(HELP_TEXT)
            return

        wait = self._rate_limited(message.author.id)
        if wait:
            await message.reply(f"Vas muy rápido 😅 Espera unos {wait} s y sigo contigo.")
            return

        async with message.channel.typing():
            try:
                answer = await self.ai.ask(
                    message.author.id, text, chat_id=message.channel.id, platform="discord"
                )
            except GroqError as e:
                await message.reply(str(e))
                return

        for chunk in split_text(answer, DISCORD_LIMIT):
            await message.channel.send(chunk)
