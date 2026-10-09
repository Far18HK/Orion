"""Configuración del bot: lee las variables de entorno desde .env."""
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # Carga el archivo .env

logger = logging.getLogger(__name__)

DB_FILENAME = "reminders.db"


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    groq_api_key: str
    groq_model: str
    max_history: int  # Cantidad de mensajes que recuerda (pregunta + respuesta cuentan por separado)
    notion_token: str | None
    notion_database_id: str | None
    timezone: str | None  # Zona por defecto de los recordatorios (cada chat puede cambiarla con /zona)
    rate_limit_messages: int  # Mensajes máximos por usuario en la ventana (0 = sin límite)
    rate_limit_window: int  # Segundos de la ventana
    github_token: str | None  # Token de solo lectura; sin él no hay herramientas de GitHub
    github_owner_ids: frozenset[int]  # Ids de Telegram con permiso de ver tus repos
    db_path: str  # Archivo SQLite de recordatorios y zonas horarias (debe estar en un disco persistente)


def resolve_db_path(env: Mapping[str, str]) -> str:
    """Decide dónde vive la base de datos SQLite.

    1. DB_PATH, si lo defines tú.
    2. Si hay un Volume de Railway adjunto, dentro de él (Railway pone RAILWAY_VOLUME_MOUNT_PATH solo).
    3. Si no, en la carpeta del proyecto: sirve en tu PC, pero en Railway se borra en cada deploy.
    """
    explicit = env.get("DB_PATH") or None
    mount = env.get("RAILWAY_VOLUME_MOUNT_PATH") or None

    if explicit:
        return explicit
    if mount:
        return os.path.join(mount, DB_FILENAME)
    if env.get("RAILWAY_ENVIRONMENT_NAME"):
        logger.warning(
            "Estás en Railway sin Volume: los recordatorios y zonas horarias se borrarán "
            "en cada deploy. Adjunta un Volume al servicio (o define DB_PATH)."
        )
    return DB_FILENAME


def check_db_dir(path: str) -> None:
    """Falla al arrancar si la carpeta de la base de datos no existe o no se puede escribir.

    No la creamos a propósito: si el Volume no está montado, crearla escribiría en el disco
    efímero y perderías los datos sin enterarte.
    """
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise RuntimeError(
            f"La carpeta «{folder}» de la base de datos no existe. "
            "¿Montaste el Volume en esa ruta? Revisa DB_PATH y el mount path del Volume."
        )
    if not os.access(folder or ".", os.W_OK):
        raise RuntimeError(
            f"No tengo permiso de escritura en «{folder or '.'}». "
            "Si usas una imagen con usuario no root en Railway, revisa los permisos del Volume."
        )


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_TOKEN")
    api_key = os.getenv("GROQ_API_KEY")

    # Si falta algo esencial, mejor fallar al arrancar que a medio chat
    if not token or not api_key:
        raise RuntimeError(
            "Faltan variables de entorno: TELEGRAM_TOKEN y/o GROQ_API_KEY. Revisa tu .env"
        )

    # Forzamos número par para que el historial siempre quede en pares usuario/modelo
    max_history = int(os.getenv("MAX_HISTORY", "20"))
    max_history -= max_history % 2

    # Notion es opcional: si no se configura, el comando /nota avisa en vez de fallar
    notion_token = os.getenv("NOTION_TOKEN") or None
    notion_database_id = os.getenv("NOTION_DATABASE_ID") or None

    rate_limit_messages = max(int(os.getenv("RATE_LIMIT_MESSAGES", "10")), 0)
    rate_limit_window = max(int(os.getenv("RATE_LIMIT_WINDOW", "60")), 1)

    timezone = os.getenv("TIMEZONE") or None
    if timezone:
        try:
            ZoneInfo(timezone)
        except Exception:
            raise RuntimeError(
                f"TIMEZONE «{timezone}» no es válida. Usa un nombre como America/Lima"
            ) from None

    github_token = os.getenv("GITHUB_TOKEN") or None
    try:
        github_owner_ids = frozenset(
            int(x) for x in os.getenv("GITHUB_OWNER_IDS", "").replace(" ", "").split(",") if x
        )
    except ValueError:
        raise RuntimeError("GITHUB_OWNER_IDS debe ser una lista de números: 123456,789012") from None

    db_path = resolve_db_path(os.environ)
    check_db_dir(db_path)

    return Settings(
        telegram_token=token,
        groq_api_key=api_key,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        max_history=max(max_history, 2),
        notion_token=notion_token,
        notion_database_id=notion_database_id,
        timezone=timezone,
        rate_limit_messages=rate_limit_messages,
        rate_limit_window=rate_limit_window,
        github_token=github_token,
        github_owner_ids=github_owner_ids,
        db_path=db_path,
    )
