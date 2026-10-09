"""Comandos básicos: /start y /reset."""
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from services.gemini import GeminiService

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    nombre = message.from_user.first_name if message.from_user else "amigo"
    await message.answer(
        f"¡Hola, {nombre}! 👋\n\n"
        "Soy tu asistente personal con IA. Pregúntame lo que quieras —incluyendo cosas "
        "actuales, puedo buscar en internet solo cuando lo necesite— o mándame fotos y "
        "notas de voz y te las comento.\n\n"
        "Comandos disponibles:\n"
        "/recordar <tiempo> <texto> — ej. /recordar 30m Tomar agua\n"
        "/nota <texto> — guarda una nota en Notion\n"
        "/reset — borra la memoria de esta conversación\n\n"
        "Recuerdo nuestra conversación reciente mientras hablamos 🧠"
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, gemini: GeminiService) -> None:
    # `gemini` llega automáticamente desde dp["gemini"] en main.py
    gemini.reset(message.from_user.id)
    await message.answer("Listo, memoria borrada ✨ ¿De qué hablamos ahora?")
