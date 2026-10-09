"""Herramientas del agente: el modelo decide solo cuál usar (function calling).

Para añadir una herramienta nueva: escribe un handler `async def _mi_tool(ctx, args) -> str`
y agrégalo a TOOLS con su descripción. Nada más: el agente la verá en la siguiente respuesta.
"""
import ast
import asyncio
import html
import ipaddress
import json
import logging
import math
import operator
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp
from ddgs import DDGS

from services.discord_monitor import DiscordMonitorError, read_recent_messages
from services.github import GitHubError, GitHubService
from services.notion import NotionError, NotionService
from services.reminders import ReminderError, ReminderService
from services.schedule_parser import find_timezones, format_when

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_RESULTS = 5
MAX_SNIPPET = 300
MAX_TOOL_RESULT = 6000  # Tope de caracteres que le devolvemos al modelo por herramienta
MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 3

DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@dataclass(frozen=True)
class ToolContext:
    """Quién está hablando y a qué servicios puede llegar el agente en este turno."""

    chat_id: int
    user_id: int
    reminders: ReminderService | None = None
    notion: NotionService | None = None
    github: GitHubService | None = None  # Solo para los dueños autorizados (ver GroqService.ask)
    platform: str = "telegram"  # "telegram" | "discord": decide por dónde llegan los recordatorios
    discord_token: str | None = None  # Solo para usuarios autorizados a mandar DMs
    discord_monitor_token: str | None = None  # Solo para usuarios autorizados a leer canales
    discord_monitor_channel_ids: frozenset[int] = frozenset()
    discord_dm_ids: frozenset[int] = frozenset()  # Únicos destinatarios permitidos
    team_runner: Callable[[str], Awaitable[str]] | None = None


def format_now(now: datetime) -> str:
    """'jueves 8 de octubre de 2026, 23:51' (sin depender del locale del servidor)."""
    return f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month - 1]} de {now.year}, {now:%H:%M}"


def user_now(ctx: ToolContext) -> tuple[datetime, bool]:
    """Hora actual en la zona del chat. El bool dice si la zona es conocida (si no, UTC)."""
    tz = ctx.reminders.get_timezone(ctx.chat_id) if ctx.reminders else None
    return datetime.now(tz or timezone.utc), tz is not None


# ---------------------------------------------------------------- búsqueda y clima

WEATHER_CODES = {
    0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa",
    56: "llovizna helada", 57: "llovizna helada intensa",
    61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte",
    66: "lluvia helada", 67: "lluvia helada fuerte",
    71: "nevada débil", 73: "nevada", 75: "nevada fuerte", 77: "granizo fino",
    80: "chubascos débiles", 81: "chubascos", 82: "chubascos violentos",
    85: "chubascos de nieve", 86: "chubascos de nieve fuertes",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta fuerte con granizo",
}


def _search_sync(query: str, kind: str) -> list[dict]:
    # ddgs es síncrono: se ejecuta en un hilo para no bloquear el bot
    ddgs = DDGS()
    if kind == "news":
        return list(ddgs.news(query, max_results=MAX_RESULTS))
    return list(ddgs.text(query, max_results=MAX_RESULTS))


async def web_search(query: str, kind: str = "web") -> str:
    query = query.strip()
    if not query:
        return "Error: la consulta está vacía."
    results = await asyncio.to_thread(_search_sync, query, "news" if kind == "news" else "web")
    if not results:
        return "No encontré resultados."

    lines = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "")
        url = r.get("href") or r.get("url", "")
        body = (r.get("body") or "")[:MAX_SNIPPET]
        extra = " | ".join(x for x in (r.get("source", ""), r.get("date", "")) if x)
        lines.append(f"{i}. {title}" + (f" ({extra})" if extra else "") + f"\n   {body}\n   {url}")
    return "\n".join(lines)


async def get_weather(city: str) -> str:
    city = city.strip()
    if not city:
        return "Error: falta la ciudad."

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        # Open-Meteo busca por nombre; si viene "Ciudad, País" probamos solo con la ciudad
        name = city.split(",")[0].strip()
        async with session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "es"},
        ) as resp:
            resp.raise_for_status()
            geo = await resp.json()
        places = geo.get("results")
        if not places:
            return f"No encontré la ciudad «{city}»."
        place = places[0]

        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 3,
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

    cur = data["current"]
    where = ", ".join(x for x in (place.get("name"), place.get("admin1"), place.get("country")) if x)
    out = [
        f"Clima en {where}:",
        f"Ahora: {WEATHER_CODES.get(cur['weather_code'], 'sin datos')}, {cur['temperature_2m']}°C "
        f"(sensación {cur['apparent_temperature']}°C), humedad {cur['relative_humidity_2m']}%, "
        f"viento {cur['wind_speed_10m']} km/h, precipitación {cur['precipitation']} mm.",
        "Pronóstico:",
    ]
    daily = data["daily"]
    for i, day in enumerate(daily["time"]):
        out.append(
            f"- {day}: {WEATHER_CODES.get(daily['weather_code'][i], 'sin datos')}, "
            f"mín {daily['temperature_2m_min'][i]}°C / máx {daily['temperature_2m_max'][i]}°C, "
            f"prob. de lluvia {daily['precipitation_probability_max'][i]}%"
        )
    return "\n".join(out)


async def _t_web_search(ctx: ToolContext, args: dict) -> str:
    return await web_search(str(args.get("query", "")), str(args.get("kind", "web")))


async def _t_get_weather(ctx: ToolContext, args: dict) -> str:
    return await get_weather(str(args.get("city", "")))


# ---------------------------------------------------------------- leer páginas web

def _check_public_url(url: str) -> None:
    """Rechaza URLs que no sean http(s) o que apunten a redes internas (SSRF).

    Ojo: es una validación previa a conectar; no cubre DNS rebinding, pero el bot
    no maneja secretos accesibles por HTTP interno más allá de esto.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Solo puedo abrir enlaces http(s).")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("No pude resolver ese dominio.") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Ese enlace apunta a una red privada; no lo abro.")


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|br)>|<br\s*/?>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


MAX_LINKS = 25
_LINK = re.compile(r"""(?is)<a\s[^>]*?href\s*=\s*["']([^"'#][^"']*)["'][^>]*>(.*?)</a>""")


def _extract_links(raw: str, base_url: str) -> list[tuple[str, str]]:
    """Enlaces http(s) únicos de la página: [(texto, url absoluta)]. Para que el agente navegue."""
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for href, label in _LINK.findall(raw):
        url = urljoin(base_url, html.unescape(href.strip()))
        if urlparse(url).scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", label)).split())
        if text:
            links.append((text[:80], url))
        if len(links) >= MAX_LINKS:
            break
    return links


async def _t_read_webpage(ctx: ToolContext, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AsistenteBot/1.0)"}
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                await asyncio.to_thread(_check_public_url, url)
            except ValueError as e:
                return f"Error: {e}"
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    url = urljoin(url, resp.headers["Location"])
                    continue
                if resp.status >= 400:
                    return f"Error: la página respondió con código {resp.status}."
                kind = resp.headers.get("Content-Type", "")
                if not any(k in kind for k in ("html", "text", "json", "xml")):
                    return f"Error: no puedo leer contenido de tipo «{kind or 'desconocido'}»."
                body = await resp.content.read(MAX_PAGE_BYTES)
                charset = resp.charset or "utf-8"
                break
        else:
            return "Error: demasiadas redirecciones."

    raw = body.decode(charset, errors="replace")
    text = _html_to_text(raw) if "html" in kind else raw
    if not text.strip():
        return "La página no tiene texto legible."
    links = _extract_links(raw, url) if "html" in kind else []
    links_text = "\n".join(f"- {label}: {link}" for label, link in links)
    # Texto y enlaces comparten el tope: dejamos hueco fijo para los enlaces
    room = MAX_TOOL_RESULT - 200 - (len(links_text) + 40 if links else 0)
    out = f"Contenido de {url}:\n{text[:max(room, 1000)]}"
    if links:
        out += f"\n\nEnlaces de la página (puedes abrirlos con read_webpage):\n{links_text}"
    return out


# ---------------------------------------------------------------- hora y cálculo

async def _t_get_current_time(ctx: ToolContext, args: dict) -> str:
    place = str(args.get("timezone", "")).strip()
    if place:
        matches = find_timezones(place)
        if not matches:
            return f"Error: no conozco la zona «{place}»."
        if len(matches) > 1:
            return "Hay varias zonas posibles, elige una: " + ", ".join(matches[:8])
        now, note = datetime.now(ZoneInfo(matches[0])), matches[0]
    else:
        now, known = user_now(ctx)
        note = now.tzinfo.key if known else "UTC (el usuario aún no configura su zona horaria)"  # type: ignore[union-attr]
    return f"{format_now(now)} ({note})"


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "abs": abs, "round": round, "min": min, "max": max,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponente demasiado grande.")
        return _BIN_OPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        return _FUNCS[node.func.id](*(_eval_node(a) for a in node.args))
    raise ValueError("Expresión no permitida.")


async def _t_calculate(ctx: ToolContext, args: dict) -> str:
    expr = str(args.get("expression", "")).strip().replace("^", "**")
    if not expr:
        return "Error: la expresión está vacía."
    try:
        result = _eval_node(ast.parse(expr, mode="eval").body)
    except ZeroDivisionError:
        return "Error: división entre cero."
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        return f"Error: no pude calcular eso ({e})."
    if isinstance(result, float):
        result = round(result, 10)
    return f"{expr} = {result}"


# ---------------------------------------------------------------- recordatorios y zona

async def _t_create_reminder(ctx: ToolContext, args: dict) -> str:
    when = str(args.get("when", "")).strip()
    message = str(args.get("message", "")).strip()
    if not when or not message:
        return "Error: faltan 'when' y/o 'message'."
    try:
        text, description = ctx.reminders.create(ctx.chat_id, f"{when} {message}", ctx.platform)
    except ReminderError as e:
        return f"Error: {e}"
    return f"Recordatorio creado: «{text}» {description}."


async def _t_list_reminders(ctx: ToolContext, args: dict) -> str:
    items = ctx.reminders.list_jobs(ctx.chat_id)
    if not items:
        return "No hay recordatorios pendientes."
    tz = ctx.reminders.get_timezone(ctx.chat_id) or timezone.utc
    lines = []
    for item in items:
        when = format_when(item.next_run, tz) if item.next_run else "pausado"
        kind = f"repetido ({item.description})" if item.recurring else "una vez"
        lines.append(f"- id={item.id} | «{item.text}» | {kind} | próximo: {when}")
    return "\n".join(lines)


async def _t_cancel_reminder(ctx: ToolContext, args: dict) -> str:
    info = ctx.reminders.cancel(ctx.chat_id, str(args.get("id", "")).strip())
    if info is None:
        return "Error: no existe un recordatorio con ese id (usa list_reminders para ver los ids)."
    return f"Recordatorio cancelado: «{info.text}»."


async def _t_set_timezone(ctx: ToolContext, args: dict) -> str:
    place = str(args.get("place", "")).strip()
    matches = find_timezones(place)
    if not matches:
        return f"Error: no encontré la zona «{place}». Pide al usuario su ciudad o una zona como America/Lima."
    if len(matches) > 1:
        return "Hay varias coincidencias, pregunta al usuario cuál es: " + ", ".join(matches[:8])
    tz = ctx.reminders.set_timezone(ctx.chat_id, matches[0])
    return f"Zona horaria guardada: {tz.key} (ahora son las {datetime.now(tz):%H:%M})."


# ---------------------------------------------------------------- notas (Notion)

def _format_notes(notes) -> str:
    if not notes:
        return "No hay notas."
    lines = []
    for note in notes:
        tags = " ".join(f"#{t}" for t in note.tags)
        meta = " · ".join(x for x in (note.created, tags) if x)
        lines.append(
            f"- {note.title}" + (f" ({meta})" if meta else "") + f" {note.url} [id:{note.id}]"
        )
    return "\n".join(lines)


async def _t_save_note(ctx: ToolContext, args: dict) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Error: la nota está vacía."
    tags = [str(t).lstrip("#") for t in (args.get("tags") or []) if str(t).strip()][:5]
    try:
        url = await ctx.notion.add_note(text, tags)
    except NotionError as e:
        return f"Error: {e}"
    return f"Nota guardada en Notion: {url}"


async def _t_list_notes(ctx: ToolContext, args: dict) -> str:
    try:
        limit = min(max(int(args.get("limit", 5)), 1), 10)
        return _format_notes(await ctx.notion.recent(limit))
    except (NotionError, ValueError) as e:
        return f"Error: {e}"


async def _t_search_notes(ctx: ToolContext, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: falta qué buscar."
    try:
        return _format_notes(await ctx.notion.search(query))
    except NotionError as e:
        return f"Error: {e}"


async def _t_delete_notes(ctx: ToolContext, args: dict) -> str:
    try:
        if args.get("all"):
            if args.get("confirmed") is not True:
                return (
                    "Error: borrar TODAS las notas requiere confirmación. Pregunta al usuario "
                    "«¿Seguro que quieres borrar todas tus notas?» y, solo si responde que sí, "
                    "vuelve a llamar con all=true y confirmed=true."
                )
            ids = [n.id for n in await ctx.notion.all_notes()]
        else:
            ids = [str(i) for i in (args.get("note_ids") or []) if str(i).strip()][:50]
        if not ids:
            return "Error: no hay notas que borrar (o falta note_ids)."
        n = await ctx.notion.delete_notes(ids)
    except NotionError as e:
        return f"Error: {e}"
    return f"{n} nota(s) enviadas a la papelera de Notion (recuperables unos 30 días)."


async def _t_edit_note(ctx: ToolContext, args: dict) -> str:
    note_id = str(args.get("note_id", "")).strip()
    if not note_id:
        return "Error: falta note_id (búscala antes con list_notes o search_notes)."
    clean = lambda xs: [str(t).lstrip("#") for t in (xs or []) if str(t).strip()]  # noqa: E731
    try:
        note = await ctx.notion.edit_note(
            note_id,
            text=str(args.get("text") or "").strip() or None,
            add_tags=clean(args.get("add_tags")),
            remove_tags=clean(args.get("remove_tags")),
        )
    except NotionError as e:
        return f"Error: {e}"
    return "Nota actualizada:\n" + _format_notes([note])


async def _t_count_notes(ctx: ToolContext, args: dict) -> str:
    try:
        notes = await ctx.notion.all_notes()
    except NotionError as e:
        return f"Error: {e}"
    tags: dict[str, int] = {}
    for n in notes:
        for tag in n.tags:
            tags[tag] = tags.get(tag, 0) + 1
    summary = ", ".join(f"#{k} ({v})" for k, v in sorted(tags.items(), key=lambda kv: -kv[1]))
    return f"Hay {len(notes)} nota(s)." + (f" Etiquetas: {summary}." if summary else "")


# ---------------------------------------------------------------- Discord (mensajes directos)

async def _t_send_discord_dm(ctx: ToolContext, args: dict) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Error: el mensaje está vacío."
    raw = str(args.get("discord_user_id") or "").strip()
    if not raw and ctx.platform == "discord":
        raw = str(ctx.user_id)  # Pidió desde Discord: «mándame» = a él mismo
    if not raw.isdigit():
        return "Error: falta el id de Discord del destinatario (solo números)."
    recipient = int(raw)
    if recipient not in ctx.discord_dm_ids:
        return "Error: ese id no está autorizado para recibir mensajes. Díselo al usuario."

    base = "https://discord.com/api/v10"
    headers = {
        "Authorization": f"Bot {ctx.discord_token}",
        "User-Agent": "DiscordBot (asistente, 1.0)",
    }
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
        async with session.post(f"{base}/users/@me/channels", json={"recipient_id": str(recipient)}) as r:
            if r.status != 200:
                return f"Error: Discord no abrió el chat privado (HTTP {r.status})."
            channel_id = (await r.json())["id"]
        async with session.post(
            f"{base}/channels/{channel_id}/messages", json={"content": text[:2000]}
        ) as r:
            if r.status == 403:
                return (
                    "Error: Discord no deja escribirle a ese usuario. Debe compartir un servidor "
                    "con el bot y tener los mensajes directos abiertos."
                )
            if r.status not in (200, 201):
                return f"Error: Discord rechazó el mensaje (HTTP {r.status})."
    return "Mensaje enviado por Discord."


async def _t_read_discord_channel(ctx: ToolContext, args: dict) -> str:
    """Lee mensajes solo cuando el usuario lo pide; no vigila continuamente."""
    raw_channel = str(args.get("channel_id") or "").strip()
    if not raw_channel and ctx.platform == "discord":
        raw_channel = str(ctx.chat_id)
    if not raw_channel.isdigit():
        return "Error: indica el ID numérico del canal de Discord."
    try:
        return await read_recent_messages(
            ctx.discord_monitor_token,
            int(raw_channel),
            ctx.discord_monitor_channel_ids,
            limit=int(args.get("limit", 25)),
            query=str(args.get("query", "")),
        )
    except (DiscordMonitorError, ValueError) as e:
        return f"Error: {e}"


async def _t_team_reason(ctx: ToolContext, args: dict) -> str:
    task = str(args.get("task", "")).strip()
    if not task:
        return "Error: falta la tarea para el equipo de IAs."
    if ctx.team_runner is None:
        return "Error: el modo multi-cerebro no está configurado; usa 2 o 3 API keys."
    return await ctx.team_runner(task)


# ---------------------------------------------------------------- GitHub (solo lectura)

def _gh(fn):
    """Adapta un método de GitHubService a handler de herramienta: GitHubError -> texto."""
    async def handler(ctx: ToolContext, args: dict) -> str:
        try:
            return await fn(ctx.github, args)
        except GitHubError as e:
            return f"Error: {e}"
    return handler


def _int(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


_t_gh_list_repos = _gh(lambda gh, a: gh.list_repos(_int(a, "limit", 20)))
_t_gh_overview = _gh(lambda gh, a: gh.overview(str(a.get("repo", ""))))
_t_gh_list_files = _gh(
    lambda gh, a: gh.list_files(str(a.get("repo", "")), str(a.get("path", "")), a.get("ref") or None)
)
_t_gh_read_file = _gh(
    lambda gh, a: gh.read_file(
        str(a.get("repo", "")), str(a.get("path", "")), a.get("ref") or None, _int(a, "start_line", 1)
    )
)
_t_gh_search = _gh(lambda gh, a: gh.search_code(str(a.get("query", "")), a.get("repo") or None))
_t_gh_commits = _gh(
    lambda gh, a: gh.recent_commits(str(a.get("repo", "")), _int(a, "limit", 10), a.get("path") or None)
)
_t_gh_issues = _gh(
    lambda gh, a: gh.issues(str(a.get("repo", "")), str(a.get("state", "open")), _int(a, "limit", 10))
)


# ---------------------------------------------------------------- registro

Handler = Callable[[ToolContext, dict], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    handler: Handler
    needs: str | None = None  # "reminders" | "notion": solo se ofrece si ese servicio existe

    def spec(self) -> dict:
        """Formato de function calling de Groq/OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                },
            },
        }


def _str(description: str, **extra) -> dict:
    return {"type": "string", "description": description, **extra}


TOOLS: list[Tool] = [
    Tool(
        "web_search",
        "Busca en internet información actual o que no conoces: noticias, precios, "
        "resultados deportivos, datos recientes, lanzamientos, etc.",
        {
            "query": _str("Consulta de búsqueda, corta y específica."),
            "kind": _str("'news' para noticias recientes, 'web' para lo demás.", enum=["web", "news"]),
        },
        ("query",),
        _t_web_search,
    ),
    Tool(
        "read_webpage",
        "Abre una página web y devuelve su texto y sus enlaces. Úsala cuando el usuario te pase "
        "una URL, para leer a fondo un resultado de web_search, o para navegar: si la respuesta "
        "no está en la página, abre uno de sus enlaces y sigue (máximo unas pocas páginas). "
        "No ejecuta JavaScript ni hace clics ni formularios.",
        {"url": _str("URL completa, con http:// o https://.")},
        ("url",),
        _t_read_webpage,
    ),
    Tool(
        "get_weather",
        "Obtiene el clima actual y el pronóstico de 3 días de una ciudad.",
        {"city": _str("Ciudad, p. ej. 'Lima' o 'Madrid, España'.")},
        ("city",),
        _t_get_weather,
    ),
    Tool(
        "get_current_time",
        "Devuelve fecha y hora exactas. Sin argumentos usa la zona horaria del usuario; "
        "con 'timezone' da la hora de otra ciudad o zona.",
        {"timezone": _str("Opcional: ciudad o zona IANA, p. ej. 'Tokio' o 'Europe/Madrid'.")},
        (),
        _t_get_current_time,
    ),
    Tool(
        "calculate",
        "Calculadora exacta. Úsala para cualquier cuenta que no sea trivial en vez de calcular "
        "de cabeza. Soporta + - * / // % ^, paréntesis, sqrt, sin, cos, tan, log, log10, abs, "
        "round, min, max, pi y e.",
        {"expression": _str("Expresión matemática, p. ej. '(1200*1.16)/12'.")},
        ("expression",),
        _t_calculate,
    ),
    Tool(
        "create_reminder",
        "Programa un recordatorio que el bot enviará por este chat a su hora. Úsala cuando "
        "el usuario pida que le avises o recuerdes algo.",
        {
            "when": _str(
                "Cuándo, en uno de estos formatos EXACTOS: relativo '30m', '2h', '1d', '1h30m'; "
                "hora '18:30'; fecha+hora 'mañana 8:00', 'pasado mañana 8:00', 'lunes 9:00', "
                "'25/12 10:00'; repetido 'cada día 8:00', 'cada lunes 9:00', 'cada 2h' "
                "(mínimo cada 5m). Traduce lo que diga el usuario a este formato; la hora va en "
                "24h o con am/pm y es hora local del usuario."
            ),
            "message": _str("Qué recordarle, redactado como el texto que verá el usuario."),
        },
        ("when", "message"),
        _t_create_reminder,
        needs="reminders",
    ),
    Tool(
        "list_reminders",
        "Lista los recordatorios pendientes de este chat con su id.",
        {},
        (),
        _t_list_reminders,
        needs="reminders",
    ),
    Tool(
        "cancel_reminder",
        "Cancela un recordatorio por su id. Si no sabes el id, llama antes a list_reminders.",
        {"id": _str("Id exacto devuelto por list_reminders.")},
        ("id",),
        _t_cancel_reminder,
        needs="reminders",
    ),
    Tool(
        "set_timezone",
        "Guarda la zona horaria del usuario. Úsala cuando la diga o cuando create_reminder "
        "falle por no tenerla (pregúntale su ciudad primero).",
        {"place": _str("Ciudad o zona IANA, p. ej. 'Lima' o 'America/Mexico_City'.")},
        ("place",),
        _t_set_timezone,
        needs="reminders",
    ),
    Tool(
        "save_note",
        "Guarda una nota en la base de Notion del usuario. Úsala cuando pida anotar, guardar "
        "o apuntar algo para después.",
        {
            "text": _str("Texto de la nota."),
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas opcionales sin '#', máximo 5.",
            },
        },
        ("text",),
        _t_save_note,
        needs="notion",
    ),
    Tool(
        "list_notes",
        "Muestra las últimas notas guardadas en Notion.",
        {"limit": {"type": "integer", "description": "Cuántas (1-10, por defecto 5)."}},
        (),
        _t_list_notes,
        needs="notion",
    ),
    Tool(
        "search_notes",
        "Busca notas de Notion por texto en el título, o por etiqueta si empieza con '#'.",
        {"query": _str("Texto a buscar, o '#etiqueta'.")},
        ("query",),
        _t_search_notes,
        needs="notion",
    ),
    Tool(
        "delete_notes",
        "Borra notas de Notion (van a la papelera, recuperables ~30 días). Para notas concretas "
        "busca antes sus ids con list_notes/search_notes y pásalos en note_ids. Para «borra todas» "
        "usa all=true, pero PRIMERO pide confirmación al usuario y solo con su «sí» envía "
        "confirmed=true. Nunca muestres los ids al usuario.",
        {
            "note_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ids de las notas a borrar (los [id:...] de las listas).",
            },
            "all": {"type": "boolean", "description": "Borrar todas las notas."},
            "confirmed": {
                "type": "boolean",
                "description": "true solo si el usuario ya confirmó borrar todas.",
            },
        },
        (),
        _t_delete_notes,
        needs="notion",
    ),
    Tool(
        "edit_note",
        "Edita una nota existente: cambia su texto/título y/o añade o quita etiquetas. "
        "Obtén antes el id con list_notes o search_notes. No muestres el id al usuario.",
        {
            "note_id": _str("Id de la nota (el [id:...] de las listas)."),
            "text": _str("Nuevo texto de la nota (reemplaza el título). Opcional."),
            "add_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas a añadir, sin '#'.",
            },
            "remove_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas a quitar, sin '#'.",
            },
        },
        ("note_id",),
        _t_edit_note,
        needs="notion",
    ),
    Tool(
        "count_notes",
        "Cuenta cuántas notas hay en Notion y cuántas por etiqueta.",
        {},
        (),
        _t_count_notes,
        needs="notion",
    ),
    Tool(
        "send_discord_dm",
        "Envía un mensaje directo de Discord. Úsala cuando el usuario pida que le escribas o "
        "avises por Discord. Solo funciona con ids de Discord autorizados. No puedes leer "
        "mensajes de Discord con esta herramienta, solo enviarlos.",
        {
            "text": _str("Mensaje a enviar."),
            "discord_user_id": _str(
                "Id numérico de Discord del destinatario. Si el usuario ya escribe desde "
                "Discord y dice «mándame», puede omitirse."
            ),
        },
        ("text",),
        _t_send_discord_dm,
        needs="discord_token",
    ),
    Tool(
        "read_discord_channel",
        "Lee bajo demanda los mensajes recientes de un canal de Discord autorizado. "
        "No mantiene vigilancia continua. Si el usuario pide revisar Discord, usa esta "
        "herramienta; desde Discord puedes omitir channel_id para revisar el canal actual.",
        {
            "channel_id": _str("ID numérico del canal; opcional si la petición viene de Discord."),
            "limit": {"type": "integer", "description": "Cantidad de mensajes, entre 1 y 100."},
            "query": _str("Texto opcional para filtrar los mensajes recientes."),
        },
        (),
        _t_read_discord_channel,
        needs="discord_monitor_token",
    ),
    Tool(
        "team_reason",
        "Coordina 2 o 3 IAs para una tarea compleja: varias analizan en paralelo y una "
        "sintetiza una respuesta. Úsala cuando el usuario pida doble/triple cerebro, "
        "comparación, revisión profunda o una segunda opinión. No la uses para preguntas simples.",
        {"task": _str("Tarea completa que deben analizar las IAs.")},
        ("task",),
        _t_team_reason,
        needs="team_runner",
    ),
    Tool(
        "github_list_repos",
        "Lista los repositorios de GitHub del usuario (privados incluidos), el más reciente primero.",
        {"limit": {"type": "integer", "description": "Cuántos (1-50, por defecto 20)."}},
        (),
        _t_gh_list_repos,
        needs="github",
    ),
    Tool(
        "github_repo_overview",
        "Resumen de un repo: descripción, lenguajes, carpetas de la raíz y el inicio del README. "
        "Es el primer paso para analizar un proyecto.",
        {"repo": _str("'nombre' (del usuario) o 'usuario/nombre'.")},
        ("repo",),
        _t_gh_overview,
        needs="github",
    ),
    Tool(
        "github_list_files",
        "Lista los archivos de un repo (sin node_modules, venv, etc.), con tamaños.",
        {
            "repo": _str("'nombre' o 'usuario/nombre'."),
            "path": _str("Opcional: carpeta para acotar, p. ej. 'handlers'."),
            "ref": _str("Opcional: rama, tag o commit. Por defecto la rama principal."),
        },
        ("repo",),
        _t_gh_list_files,
        needs="github",
    ),
    Tool(
        "github_read_file",
        "Lee un archivo del repo con números de línea. Devuelve ~3500 caracteres por llamada; "
        "si el archivo sigue, repite con el start_line que indica el final. Lee solo lo relevante.",
        {
            "repo": _str("'nombre' o 'usuario/nombre'."),
            "path": _str("Ruta del archivo, p. ej. 'services/tools.py'."),
            "start_line": {"type": "integer", "description": "Línea desde la que leer (por defecto 1)."},
            "ref": _str("Opcional: rama, tag o commit."),
        },
        ("repo", "path"),
        _t_gh_read_file,
        needs="github",
    ),
    Tool(
        "github_search_code",
        "Busca texto en el código (rama principal) de un repo o de todos los repos del usuario. "
        "Devuelve rutas; luego lee con github_read_file.",
        {
            "query": _str("Texto o identificador a buscar, p. ej. 'def create_reminder'."),
            "repo": _str("Opcional: limitar a 'nombre' o 'usuario/nombre'."),
        },
        ("query",),
        _t_gh_search,
        needs="github",
    ),
    Tool(
        "github_recent_commits",
        "Últimos commits de un repo (opcionalmente solo los que tocan una ruta).",
        {
            "repo": _str("'nombre' o 'usuario/nombre'."),
            "limit": {"type": "integer", "description": "1-20, por defecto 10."},
            "path": _str("Opcional: archivo o carpeta."),
        },
        ("repo",),
        _t_gh_commits,
        needs="github",
    ),
    Tool(
        "github_issues",
        "Issues y pull requests de un repo.",
        {
            "repo": _str("'nombre' o 'usuario/nombre'."),
            "state": _str("'open' (defecto), 'closed' o 'all'.", enum=["open", "closed", "all"]),
            "limit": {"type": "integer", "description": "1-20, por defecto 10."},
        },
        ("repo",),
        _t_gh_issues,
        needs="github",
    ),
]
_BY_NAME = {t.name: t for t in TOOLS}


def _available(tool: Tool, ctx: ToolContext) -> bool:
    return tool.needs is None or getattr(ctx, tool.needs) is not None


def build_tool_specs(ctx: ToolContext) -> list[dict]:
    """Solo las herramientas que este bot tiene configuradas (p. ej. sin Notion no hay notas)."""
    return [t.spec() for t in TOOLS if _available(t, ctx)]


async def run_tool(name: str, arguments: str, ctx: ToolContext) -> str:
    """Ejecuta la herramienta pedida por el modelo. Nunca lanza: devuelve el error como texto."""
    tool = _BY_NAME.get(name)
    if tool is None or not _available(tool, ctx):
        return f"Error: herramienta desconocida «{name}»."
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return "Error: argumentos inválidos."

    try:
        result = await tool.handler(ctx, args)
    except Exception as e:  # noqa: BLE001 - el modelo debe poder explicarle el fallo al usuario
        logger.error("Falló la herramienta %s: %s", name, e, exc_info=True)
        return "Error: la herramienta no pudo completar la consulta ahora mismo."
    return result[:MAX_TOOL_RESULT]
