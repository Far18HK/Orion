"""Frente de Discord: el mismo agente de Telegram, atendiendo mensajes directos y menciones.

Se activa solo si defines DISCORD_TOKEN. Corre en el mismo proceso y event loop que Telegram.
En servidores responde cuando lo mencionas (@bot); en mensajes directos responde siempre.
"""
import logging
import time
from collections import defaultdict, deque

import aiohttp
import discord

from handlers.chat import split_text
from services.groq_service import GroqError, GroqService
from services.workspace import WorkspaceError, save_attachment

logger = logging.getLogger(__name__)

DISCORD_LIMIT = 2000  # Máximo de caracteres por mensaje en Discord

HELP_TEXT = (
    "Soy tu asistente. Escríbeme normal (en servidores, mencióname) y yo decido qué herramienta "
    "usar: buscar en internet, leer enlaces, recordatorios, notas, calculadora, clima, hora y GitHub.\n"
    "Comandos: `id` (tu id de Discord), `reset` (borra la memoria de la conversación), `ayuda`.\n"
    "Con permisos de moderador puedo preparar anuncios, crear eventos, aplicar timeouts y borrar "
    "mensajes concretos; siempre te pediré aprobación antes de ejecutar una acción administrativa.\n"
    "Si pido tu aprobación para algo, responde `aprobar` o `rechazar`; `reanudar` retoma un turno "
    "que quedó a medias tras un reinicio."
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
        if message.attachments:
            saved = []
            try:
                async with aiohttp.ClientSession() as session:
                    for attachment in message.attachments[:10]:
                        async with session.get(attachment.url) as response:
                            if response.status >= 400:
                                raise WorkspaceError(f"Discord no permitió descargar {attachment.filename}.")
                            declared = int(response.headers.get("Content-Length", "0") or 0)
                            if declared > 50 * 1024 * 1024:
                                raise WorkspaceError(f"{attachment.filename} supera 50 MB.")
                            chunks = []
                            total = 0
                            async for chunk in response.content.iter_chunked(1024 * 1024):
                                total += len(chunk)
                                if total > 50 * 1024 * 1024:
                                    raise WorkspaceError(f"{attachment.filename} supera 50 MB.")
                                chunks.append(chunk)
                            data = b"".join(chunks)
                        saved.append(save_attachment(self.ai.workspace_root, message.author.id, attachment.filename, data, "discord"))
            except (WorkspaceError, aiohttp.ClientError) as exc:
                await message.reply(f"No pude guardar el adjunto: {exc}")
                return
            prompt = text or "Inspecciona los archivos que acabo de enviar y dime qué puedes hacer con ellos."
            text = (
                f"Recibí estos archivos: {', '.join(p.name for p in saved)}. "
                f"Están en inbox/. Petición: {prompt} Usa list_files/read_file; no inventes contenido."
            )
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
                if command in ("aprobar", "rechazar"):
                    answer = await self.ai.resolve_pending(
                        message.author.id,
                        command == "aprobar",
                        platform="discord",
                        chat_id=message.channel.id,
                        guild_id=message.guild.id if message.guild else None,
                    )
                elif command == "reanudar":
                    answer = await self.ai.resume_interrupted(
                        message.author.id,
                        platform="discord",
                        chat_id=message.channel.id,
                        guild_id=message.guild.id if message.guild else None,
                    )
                else:
                    answer = await self.ai.ask(
                        message.author.id,
                        text,
                        chat_id=message.channel.id,
                        platform="discord",
                        guild_id=message.guild.id if message.guild else None,
                    )
            except GroqError as e:
                await message.reply(str(e))
                return

        for chunk in split_text(answer, DISCORD_LIMIT):
            await message.channel.send(chunk)
