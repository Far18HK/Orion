"""Handler principal: cualquier texto se envía al proveedor activo."""
import asyncio

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from services.groq_service import GroqError, GroqService
from handlers.telegram_utils import send_long_answer, split_text

router = Router()

APPROVE_DATA = "orion:approve"
REJECT_DATA = "orion:reject"


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
        await target.answer(chunk, parse_mode=None, reply_markup=approval_keyboard() if pending else None)


@router.message()  # Sin filtro: atrapa todo lo que no manejaron los routers anteriores
async def chat_with_ai(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    if not message.text:
        await message.answer("Por ahora entiendo texto, fotos, notas de voz y documentos 📝")
        return

    # Muestra progreso si una consulta con herramientas tarda más de unos segundos.
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        request = asyncio.create_task(
            ai.ask(message.from_user.id, message.text, chat_id=message.chat.id)
        )
        progress = asyncio.create_task(asyncio.sleep(4.0))
        try:
            done, _ = await asyncio.wait({request, progress}, return_when=asyncio.FIRST_COMPLETED)
            if progress in done and not request.done():
                await message.answer("Sigo trabajando en eso… puede tardar un poco si estoy revisando herramientas o código.")
            answer = await request
        except GroqError as e:
            if not request.done():
                request.cancel()
            await message.answer(str(e))
            return
        finally:
            if not progress.done():
                progress.cancel()

    await send_answer(message, ai, message.from_user.id, answer)
