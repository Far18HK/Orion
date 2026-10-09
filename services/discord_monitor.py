"""Lectura bajo demanda de mensajes de Discord mediante la API REST."""
from __future__ import annotations

from datetime import datetime

import aiohttp

API = "https://discord.com/api/v10"
TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_MESSAGES = 100


class DiscordMonitorError(Exception):
    """Error controlado del monitor de Discord."""


async def read_recent_messages(
    token: str,
    channel_id: int,
    allowed_channel_ids: frozenset[int],
    limit: int = 25,
    query: str = "",
) -> str:
    """Lee mensajes recientes de un canal explícitamente permitido."""
    if channel_id not in allowed_channel_ids:
        raise DiscordMonitorError(
            "Ese canal no está autorizado. Añade su ID a DISCORD_MONITOR_CHANNEL_IDS."
        )
    limit = min(max(int(limit), 1), MAX_MESSAGES)
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "OrionDiscordMonitor/1.0",
    }
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=headers) as session:
        async with session.get(
            f"{API}/channels/{channel_id}/messages", params={"limit": limit}
        ) as response:
            if response.status == 403:
                raise DiscordMonitorError(
                    "Discord denegó el acceso. El bot necesita View Channel y Read Message History."
                )
            if response.status == 404:
                raise DiscordMonitorError("No encontré ese canal o el bot ya no tiene acceso.")
            if response.status >= 400:
                raise DiscordMonitorError(f"Discord respondió con HTTP {response.status}.")
            messages = await response.json()

    query = query.strip().casefold()
    if query:
        messages = [m for m in messages if query in str(m.get("content", "")).casefold()]
    if not messages:
        return "No encontré mensajes recientes que coincidan."

    lines = [f"Mensajes recientes del canal {channel_id} ({len(messages)}):"]
    for message in reversed(messages):
        author = message.get("author", {}).get("global_name") or message.get("author", {}).get("username", "usuario")
        content = str(message.get("content", "")).strip() or "[sin texto]"
        attachments = message.get("attachments") or []
        if attachments:
            content += " " + " ".join(a.get("url", "") for a in attachments)
        timestamp = message.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
        lines.append(f"[{timestamp}] {author}: {content[:1500]}")
    return "\n".join(lines)
