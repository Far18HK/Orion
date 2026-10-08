"""Configuración del bot: lee las variables de entorno desde .env."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # Carga el archivo .env


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    gemini_api_key: str
    gemini_model: str
    max_history: int  # Cantidad de mensajes que recuerda (pregunta + respuesta cuentan por separado)


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_TOKEN")
    api_key = os.getenv("GEMINI_API_KEY")

    # Si falta algo, mejor fallar al arrancar que a medio chat
    if not token or not api_key:
        raise RuntimeError(
            "Faltan variables de entorno: TELEGRAM_TOKEN y/o GEMINI_API_KEY. Revisa tu .env"
        )

    # Forzamos número par para que el historial siempre quede en pares usuario/modelo
    max_history = int(os.getenv("MAX_HISTORY", "20"))
    max_history -= max_history % 2

    return Settings(
        telegram_token=token,
        gemini_api_key=api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        max_history=max(max_history, 2),
    )
