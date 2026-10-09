"""Configuración del bot: lee las variables de entorno desde .env."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # Carga el archivo .env


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    groq_api_key: str
    groq_model: str
    max_history: int  # Cantidad de mensajes que recuerda (pregunta + respuesta cuentan por separado)
    notion_token: str | None
    notion_database_id: str | None


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

    return Settings(
        telegram_token=token,
        groq_api_key=api_key,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        max_history=max(max_history, 2),
        notion_token=notion_token,
        notion_database_id=notion_database_id,
    )
