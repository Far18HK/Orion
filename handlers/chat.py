"""Handler principal: cualquier texto se envía a Groq."""
from aiogram import Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from services.groq_service import GroqError, GroqService

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
async def chat_with_ai(message: Message, ai: GroqService) -> None:
    if not message.text:
        await message.answer("Por ahora solo entiendo texto, fotos y notas de voz 📝")
        return

    # Muestra "escribiendo..." mientras el modelo piensa
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await ai.ask(message.from_user.id, message.text)
        except GroqError as e:
            await message.answer(str(e))
            return

    for chunk in split_text(answer):
        await message.answer(chunk, parse_mode=None)  # Texto plano: evita errores de formato
