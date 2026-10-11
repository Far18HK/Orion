"""Acciones de moderación Discord con listas blancas y respuestas controladas."""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp

API = "https://discord.com/api/v10"
TIMEOUT = aiohttp.ClientTimeout(total=15)


class DiscordModerationError(Exception):
    """Error seguro y legible de la API de moderación."""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "User-Agent": "OrionModerator/1.0"}


def _guild_allowed(guild_id: int, allowed: frozenset[int]) -> None:
    if guild_id not in allowed:
        raise DiscordModerationError(
            "Ese servidor no está autorizado. Añade su ID a DISCORD_MODERATOR_GUILD_IDS."
        )


def _parse_iso(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiscordModerationError(
            f"{field} debe estar en ISO 8601, por ejemplo 2026-10-20T20:00:00-06:00."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def create_scheduled_event(
    token: str,
    guild_id: int,
    allowed_guild_ids: frozenset[int],
    name: str,
    description: str,
    start_at: str,
    end_at: str,
    location: str,
) -> str:
    _guild_allowed(guild_id, allowed_guild_ids)
    name = name.strip()[:100]
    description = description.strip()[:1000]
    location = location.strip()[:100]
    if not name or not location:
        raise DiscordModerationError("El evento necesita nombre y ubicación/enlace.")
    start = _parse_iso(start_at, "start_at")
    end = _parse_iso(end_at, "end_at")
    if end <= start:
        raise DiscordModerationError("end_at debe ser posterior a start_at.")
    payload = {
        "name": name,
        "privacy_level": 2,
        "scheduled_start_time": start,
        "scheduled_end_time": end,
        "description": description or None,
        "entity_type": 3,
        "entity_metadata": {"location": location},
    }
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=_headers(token)) as session:
        async with session.post(f"{API}/guilds/{guild_id}/scheduled-events", json=payload) as response:
            if response.status in (401, 403):
                raise DiscordModerationError(
                    "Discord denegó el evento. Revisa Manage Events y los permisos del bot."
                )
            if response.status >= 400:
                detail = await response.text()
                raise DiscordModerationError(f"Discord rechazó el evento (HTTP {response.status}): {detail[:300]}")
            data = await response.json()
    return f"Evento creado: «{data.get('name', name)}» (id {data.get('id', 'desconocido')})."


async def timeout_member(
    token: str,
    guild_id: int,
    allowed_guild_ids: frozenset[int],
    member_id: int,
    until: str,
    reason: str,
) -> str:
    _guild_allowed(guild_id, allowed_guild_ids)
    parsed_until = _parse_iso(until, "until")
    if datetime.fromisoformat(parsed_until.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise DiscordModerationError("until debe estar en el futuro.")
    headers = _headers(token)
    headers["X-Audit-Log-Reason"] = reason.strip()[:512] or "Moderación mediante Orion"
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=headers) as session:
        async with session.patch(
            f"{API}/guilds/{guild_id}/members/{member_id}",
            json={"communication_disabled_until": parsed_until},
        ) as response:
            if response.status in (401, 403):
                raise DiscordModerationError(
                    "Discord denegó el timeout. Revisa Moderate Members y el rol del bot."
                )
            if response.status == 404:
                raise DiscordModerationError("No encontré ese miembro en el servidor.")
            if response.status >= 400:
                raise DiscordModerationError(f"Discord rechazó el timeout (HTTP {response.status}).")
    return f"Miembro {member_id} puesto en timeout hasta {parsed_until}."


async def delete_message(
    token: str,
    guild_id: int,
    allowed_guild_ids: frozenset[int],
    channel_id: int,
    message_id: int,
    reason: str,
) -> str:
    _guild_allowed(guild_id, allowed_guild_ids)
    headers = _headers(token)
    headers["X-Audit-Log-Reason"] = reason.strip()[:512] or "Moderación mediante Orion"
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=headers) as session:
        async with session.delete(f"{API}/channels/{channel_id}/messages/{message_id}") as response:
            if response.status in (401, 403):
                raise DiscordModerationError("Discord denegó borrar el mensaje; revisa Manage Messages.")
            if response.status == 404:
                raise DiscordModerationError("No encontré el mensaje o ya fue borrado.")
            if response.status >= 400:
                raise DiscordModerationError(f"Discord rechazó borrar el mensaje (HTTP {response.status}).")
    return f"Mensaje {message_id} eliminado del canal {channel_id}."
