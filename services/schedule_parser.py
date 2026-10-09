"""Interpreta el "cuándo" de /recordar: relativo (30m), con hora/fecha (mañana 8:00)
y repetido (cada lunes 9:00). Es código puro: no toca Telegram ni el scheduler."""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, available_timezones


class ReminderError(Exception):
    """Error amigable para el usuario."""


class _InPast(ReminderError):
    """La fecha/hora pedida ya pasó. Aparte porque, sin zona horaria, ese juicio no es fiable."""


NEED_TIMEZONE = (
    "Para usar horas y fechas necesito saber tu zona horaria 🌎\n"
    "Dímela con /zona, por ejemplo: /zona Lima  o  /zona America/Mexico_City\n"
    "(Los recordatorios relativos como 30m no la necesitan.)"
)

DURATION_FULL = re.compile(r"^(?:\d+[mhd])+$")
DURATION_PART = re.compile(r"(\d+)([mhd])")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
MIN_INTERVAL = timedelta(minutes=5)  # Evita recordatorios repetidos que parezcan spam
MAX_DELAY = timedelta(days=365)
MAX_TEXT = 1000

TIME_PATTERN = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$")
DATE_PATTERN = re.compile(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?$")

# Días de la semana ya sin tildes (ver _norm); 0 = lunes, como en datetime y en APScheduler
WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
    "sabado": 5, "sabados": 5, "domingo": 6, "domingos": 6,
}
WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass(frozen=True)
class Schedule:
    kind: str  # "once" | "interval" | "daily" | "weekly"
    description: str  # Texto para el usuario: "mañana a las 08:00", "cada lunes a las 09:00"...
    run_at: datetime | None = None  # once: instante exacto, en UTC
    interval: timedelta | None = None  # interval
    hour: int = 0  # daily / weekly (hora local del chat)
    minute: int = 0
    weekday: int | None = None  # weekly


def _norm(text: str) -> str:
    """Minúsculas y sin tildes: 'Mañana' -> 'manana', 'MIÉRCOLES' -> 'miercoles'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def parse_duration(text: str) -> timedelta:
    """Convierte '10m', '2h', '1d' o '1h30m' en un timedelta."""
    text = text.strip().lower()
    if not DURATION_FULL.match(text):
        raise ReminderError(
            "Formato de tiempo inválido. Usa algo como: 10m, 2h, 1d o 1h30m (minutos, horas, días)."
        )
    try:
        total = sum(
            (timedelta(**{UNITS[unit]: int(amount)}) for amount, unit in DURATION_PART.findall(text)),
            timedelta(),
        )
    except OverflowError:
        total = MAX_DELAY + timedelta(days=1)
    if total <= timedelta(0):
        raise ReminderError("El tiempo tiene que ser mayor que cero.")
    if total > MAX_DELAY:
        raise ReminderError("Máximo puedo recordarte con un año de anticipación.")
    return total


def _parse_time(norm: list[str], i: int, allow_bare: bool) -> tuple[int, int, int] | None:
    """Lee una hora en norm[i]: 8:00, 18:30, 8am, 8:30pm, '8 pm' o (si allow_bare) solo '8'.

    Devuelve (hora, minuto, siguiente_índice), None si no parece una hora,
    o lanza ReminderError si lo parece pero es imposible (25:00).
    """
    if i >= len(norm):
        return None
    m = TIME_PATTERN.match(norm[i])
    if not m:
        return None
    hour, minute, suffix = int(m[1]), int(m[2] or 0), m[3]
    j = i + 1
    bare = m[2] is None and suffix is None  # Un número suelto, sin minutos ni am/pm
    if suffix is None and j < len(norm) and norm[j] in ("am", "pm"):  # "8 pm" / "8:30 pm"
        suffix = norm[j]
        j += 1
    elif bare and not allow_bare:  # Un número suelto solo es hora si se espera una ("a las 8")
        return None
    if suffix:
        if not 1 <= hour <= 12:
            raise ReminderError("Hora inválida: con am/pm usa del 1 al 12.")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ReminderError("Hora inválida 🕐 Usa un formato como 8:00, 18:30 o 8pm.")
    return hour, minute, j


def _skip_time_fillers(norm: list[str], i: int) -> tuple[int, bool]:
    """Salta 'a las' / 'a la'. Devuelve (índice, hubo_artículo): tras 'a las', un '8' suelto es hora."""
    saw_article = False
    while i < len(norm) and norm[i] in ("a", "las", "la"):
        saw_article = saw_article or norm[i] in ("las", "la")
        i += 1
    return i, saw_article


def _finish_text(tokens: list[str], i: int) -> str:
    text = " ".join(tokens[i:]).strip()
    if not text:
        raise ReminderError("Falta el mensaje: ¿qué quieres que te recuerde?")
    if len(text) > MAX_TEXT:
        raise ReminderError(f"El mensaje es muy largo (máximo {MAX_TEXT} caracteres).")
    return text


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def describe_once(dt: datetime, now: datetime) -> str:
    """'hoy a las 18:30', 'mañana a las 08:00', 'el lunes 12/10 a las 09:00'."""
    days = (dt.date() - now.date()).days
    at = f"a las {_hhmm(dt.hour, dt.minute)}"
    if days == 0:
        return f"hoy {at}"
    if days == 1:
        return f"mañana {at}"
    year = f"/{dt.year}" if dt.year != now.year else ""
    return f"el {WEEKDAY_NAMES[dt.weekday()]} {dt.day:02d}/{dt.month:02d}{year} {at}"


def format_when(dt: datetime, tz: tzinfo, now: datetime | None = None) -> str:
    """Versión corta para listas: 'hoy 18:30', 'mañana 08:00', 'lun 12/10 09:00'."""
    local = dt.astimezone(tz)
    now = (now or datetime.now(tz)).astimezone(tz)
    days = (local.date() - now.date()).days
    hhmm = _hhmm(local.hour, local.minute)
    if days == 0:
        return f"hoy {hhmm}"
    if days == 1:
        return f"mañana {hhmm}"
    year = f"/{local.year}" if local.year != now.year else ""
    return f"{WEEKDAY_NAMES[local.weekday()][:3]} {local.day:02d}/{local.month:02d}{year} {hhmm}"


def _parse_recurring(norm: list[str], tokens: list[str]) -> tuple[Schedule, str] | None:
    """cada día 8:00 · cada lunes 9:00 · cada 2h · todos los días 7:30 · los lunes 9:00 · diario 8:00"""
    kind: str | None = None
    weekday: int | None = None
    interval: timedelta | None = None
    i = 0

    if norm[0] == "cada":
        if len(norm) < 2:
            raise ReminderError("Después de «cada» pon: día, un día de la semana (lunes...) o un intervalo como 2h.")
        if norm[1] in ("dia", "dias"):
            kind, i = "daily", 2
        elif norm[1] in WEEKDAYS:
            kind, weekday, i = "weekly", WEEKDAYS[norm[1]], 2
        elif DURATION_FULL.match(norm[1]):
            kind, interval, i = "interval", parse_duration(norm[1]), 2
        else:
            raise ReminderError("Después de «cada» pon: día, un día de la semana (lunes...) o un intervalo como 2h.")
    elif norm[:3] == ["todos", "los", "dias"]:
        kind, i = "daily", 3
    elif norm[0] in ("diario", "diariamente"):
        kind, i = "daily", 1
    elif norm[0] == "los" and len(norm) > 1 and norm[1] in WEEKDAYS:
        kind, weekday, i = "weekly", WEEKDAYS[norm[1]], 2
    else:
        return None

    if kind == "interval":
        if interval < MIN_INTERVAL:
            raise ReminderError("Para repetir usa intervalos de al menos 5 minutos (ej. cada 5m).")
        return Schedule("interval", f"cada {norm[1]}", interval=interval), _finish_text(tokens, i)

    i, saw_article = _skip_time_fillers(norm, i)
    parsed = _parse_time(norm, i, allow_bare=saw_article)
    if parsed is None:
        raise ReminderError("Falta la hora 🕐 Ejemplo: /recordar cada día 8:00 Tomar vitaminas")
    hour, minute, i = parsed
    when = f"a las {_hhmm(hour, minute)}"
    if kind == "daily":
        return Schedule("daily", f"cada día {when}", hour=hour, minute=minute), _finish_text(tokens, i)
    return (
        Schedule("weekly", f"cada {WEEKDAY_NAMES[weekday]} {when}", hour=hour, minute=minute, weekday=weekday),
        _finish_text(tokens, i),
    )


def _parse_absolute(norm: list[str], tokens: list[str], now: datetime) -> tuple[Schedule, str]:
    """[hoy|mañana|pasado mañana|lunes|25/12[/2027]] [a las] HH:MM"""
    tz = now.tzinfo
    i = 0
    while i < len(norm) and norm[i] in ("el", "este", "proximo"):
        i += 1

    today = now.date()
    offset: int | None = None  # Días desde hoy (hoy / mañana / pasado mañana)
    weekday: int | None = None
    explicit: tuple[int, int, int | None] | None = None  # (día, mes, año)
    has_date = True

    word = norm[i] if i < len(norm) else ""
    if word == "hoy":
        offset, i = 0, i + 1
    elif word == "manana":
        offset, i = 1, i + 1
    elif norm[i : i + 2] == ["pasado", "manana"]:
        offset, i = 2, i + 2
    elif word in WEEKDAYS:
        weekday, i = WEEKDAYS[word], i + 1
    elif (dm := DATE_PATTERN.match(word)):
        year = int(dm[3]) if dm[3] else None
        if year is not None and year < 100:
            year += 2000
        explicit, i = (int(dm[1]), int(dm[2]), year), i + 1
    else:
        has_date = False

    i, saw_article = _skip_time_fillers(norm, i)
    parsed = _parse_time(norm, i, allow_bare=saw_article)
    if parsed is None:
        if has_date:
            raise ReminderError("Falta la hora 🕐 Ejemplo: /recordar mañana 8:00 Sacar la basura")
        raise ReminderError(
            "No entendí el cuándo 🤔 Prueba con 30m, 18:30, «mañana 8:00», «lunes 9:00» o «cada día 8:00»."
        )
    hour, minute, i = parsed

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

    if offset is not None:
        candidate = at(today + timedelta(days=offset))
        if candidate <= now:
            raise _InPast("Esa hora ya pasó hoy 😅 Usa «mañana» o una hora futura.")
    elif weekday is not None:
        candidate = at(today + timedelta(days=(weekday - today.weekday()) % 7))
        if candidate <= now:
            candidate += timedelta(days=7)
    elif explicit is not None:
        day, month, year = explicit
        try:
            candidate = at(date(year or today.year, month, day))
            if candidate <= now:
                if year is not None:
                    raise _InPast("Esa fecha ya pasó 😅")
                candidate = at(date(today.year + 1, month, day))
        except ValueError:
            raise ReminderError("Fecha inválida 📅 Usa día/mes, por ejemplo 25/12 o 25/12/2027.") from None
    else:  # Solo hora: hoy si todavía no pasó, si no mañana
        candidate = at(today)
        if candidate <= now:
            candidate += timedelta(days=1)

    schedule = Schedule(
        "once", describe_once(candidate, now), run_at=candidate.astimezone(timezone.utc)
    )
    return schedule, _finish_text(tokens, i)


def parse_reminder(
    args: str, tz: ZoneInfo | None, now: datetime | None = None
) -> tuple[Schedule, str]:
    """Interpreta '<cuándo> <mensaje>' y devuelve (agenda, mensaje).

    `tz` es la zona del chat; es obligatoria para horas, fechas y repetidos,
    pero no para tiempos relativos (30m). `now` solo se usa en pruebas.
    """
    tokens = args.split()
    if not tokens:
        raise ReminderError("Falta el cuándo y el mensaje.")
    norm = [_norm(t) for t in tokens]

    # 1) Relativo: 30m, 2h, 1d, 1h30m
    if DURATION_FULL.match(norm[0]):
        delay = parse_duration(norm[0])
        base = now or datetime.now(timezone.utc)
        run_at = (base + delay).astimezone(timezone.utc)
        return Schedule("once", f"en {norm[0]}", run_at=run_at), _finish_text(tokens, 1)

    # 2) Repetido y 3) con hora/fecha: dependen de la zona horaria del usuario
    base_tz = tz or timezone.utc
    local_now = (now or datetime.now(base_tz)).astimezone(base_tz)
    try:
        result = _parse_recurring(norm, tokens) or _parse_absolute(norm, tokens, local_now)
    except _InPast:
        if tz is None:  # Sin zona, "ya pasó" se calcularía con la hora equivocada
            raise ReminderError(NEED_TIMEZONE) from None
        raise
    if tz is None:
        raise ReminderError(NEED_TIMEZONE)
    return result


def find_timezones(text: str) -> list[str]:
    """Busca zonas IANA por nombre exacto ('America/Lima') o por ciudad ('lima', 'Bogotá')."""
    wanted = _norm(text.strip()).replace(" ", "_")
    if not wanted:
        return []
    zones = sorted(available_timezones())
    exact = [z for z in zones if z.lower() == wanted]
    if exact:
        return exact
    return [z for z in zones if _norm(z).rsplit("/", 1)[-1] == wanted]
