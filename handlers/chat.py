"""Handler principal: cualquier texto se envía a Groq."""
from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from services.groq_service import GroqError, GroqService

router = Router()

TELEGRAM_LIMIT = 4096  # Máximo de caracteres por mensaje en Telegram
APPROVE_DATA = "orion:approve"
REJECT_DATA = "orion:reject"


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


def approval_keyboard() -> InlineKeyboardMarkup:
    """Botones para aprobar o rechazar la acción en pausa."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Aprobar", callback_data=APPROVE_DATA),
                InlineKeyboardButton(text="❌ Rechazar", callback_data=REJECT_DATA),
            ]
        ]
    )


async def send_answer(target: Message, ai: GroqService, user_id: int, answer: str) -> None:
    """Envía la respuesta en trozos; si el turno quedó esperando aprobación, añade los botones."""
    chunks = split_text(answer)
    for index, chunk in enumerate(chunks):
        pending = index == len(chunks) - 1 and ai.has_pending_approval(user_id)
        # Texto plano: evita errores de formato
        await target.answer(
            chunk, parse_mode=None, reply_markup=approval_keyboard() if pending else None
        )


@router.message()  # Sin filtro: atrapa todo lo que no manejaron los routers anteriores
async def chat_with_ai(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    if not message.text:
        await message.answer("Por ahora entiendo texto, fotos, notas de voz y documentos 📝")
        return

    # Muestra "escribiendo..." mientras el modelo piensa
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await ai.ask(message.from_user.id, message.text, chat_id=message.chat.id)
        except GroqError as e:
            await message.answer(str(e))
            return

    await send_answer(message, ai, message.from_user.id, answer)
