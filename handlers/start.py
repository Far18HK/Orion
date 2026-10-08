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
        "Soy tu asistente personal con IA. Pregúntame lo que quieras: "
        "dudas, ideas, textos, código, recetas...\n\n"
        "Recuerdo nuestra conversación reciente. Si quieres empezar de cero, usa /reset 🧹"
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, gemini: GeminiService) -> None:
    # `gemini` llega automáticamente desde dp["gemini"] en main.py
    gemini.reset(message.from_user.id)
    await message.answer("Listo, memoria borrada ✨ ¿De qué hablamos ahora?")
