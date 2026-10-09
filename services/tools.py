"""Herramientas que el modelo puede llamar solo: búsqueda web y clima (sin API keys)."""
import asyncio
import json
import logging

import aiohttp
from ddgs import DDGS

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_RESULTS = 5
MAX_SNIPPET = 300

# Descripción de las herramientas en el formato de function calling de Groq/OpenAI
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Busca en internet información actual o que no conoces: noticias, precios, "
                "resultados deportivos, datos recientes, lanzamientos, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta de búsqueda, corta y específica."},
                    "kind": {
                        "type": "string",
                        "enum": ["web", "news"],
                        "description": "'news' para noticias recientes, 'web' para lo demás.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Obtiene el clima actual y el pronóstico de 3 días de una ciudad.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Ciudad, p. ej. 'Lima' o 'Madrid, España'."},
                },
                "required": ["city"],
            },
        },
    },
]

# Códigos WMO que devuelve Open-Meteo
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
        date = r.get("date", "")
        source = r.get("source", "")
        extra = " | ".join(x for x in (source, date) if x)
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


async def run_tool(name: str, arguments: str) -> str:
    """Ejecuta la herramienta pedida por el modelo. Nunca lanza: devuelve el error como texto."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return "Error: argumentos inválidos."

    try:
        if name == "web_search":
            return await web_search(str(args.get("query", "")), str(args.get("kind", "web")))
        if name == "get_weather":
            return await get_weather(str(args.get("city", "")))
        return f"Error: herramienta desconocida «{name}»."
    except Exception as e:  # noqa: BLE001 - el modelo debe poder explicarle el fallo al usuario
        logger.error("Falló la herramienta %s: %s", name, e)
        return "Error: la herramienta no pudo completar la consulta ahora mismo."
