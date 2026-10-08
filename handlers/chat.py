"""Handler principal: cualquier texto se envía a Gemini."""
from aiogram import Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from services.gemini import GeminiError, GeminiService

router = Router()

TELEGRAM_LIMIT = 4096  # Máximo de caracteres por mensaje en Telegram


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Parte respuestas largas en trozos que quepan en Telegram."""
    chunks = []
    while len(text) > limit:
        # Intenta cortar en un salto de línea para no partir frases
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


@router.message()  # Sin filtro: atrapa todo lo que no manejaron los routers anteriores
async def chat_with_gemini(message: Message, gemini: GeminiService) -> None:
    if not message.text:
        await message.answer("Por ahora solo entiendo texto 📝")
        return

    # Muestra "escribiendo..." mientras Gemini piensa
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await gemini.ask(message.from_user.id, message.text)
        except GeminiError as e:
            await message.answer(str(e))
            return

    for chunk in split_text(answer):
        await message.answer(chunk, parse_mode=None)  # Texto plano: evita errores de formato
