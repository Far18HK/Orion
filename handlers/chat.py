"""Handler principal: cualquier texto se envía al proveedor activo."""
import asyncio
import logging
import re
from pathlib import Path

from aiogram import Router
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from handlers.telegram_utils import split_text
from services.groq_service import GroqError, GroqService

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


def _file_ready(path: Path) -> bool:
    return path.is_file() and path.stat().st_size <= 50 * 1024 * 1024


async def send_answer(target: Message, ai: GroqService, user_id: int, answer: str) -> None:
    """Envía la respuesta en trozos; si el turno quedó esperando aprobación, añade los botones."""
    chunks = split_text(answer)
    for index, chunk in enumerate(chunks):
        pending = index == len(chunks) - 1 and ai.has_pending_approval(user_id)
        await target.answer(chunk, parse_mode=None, reply_markup=approval_keyboard() if pending else None)
    created = re.findall(r"ARCHIVO_CREADO:(\S+)", answer)
    for raw_path in dict.fromkeys(created):
        path = Path(raw_path)
        valid_file = await asyncio.to_thread(_file_ready, path)
        if valid_file:
            await target.answer_document(FSInputFile(path), caption=f"Archivo listo: {path.name}")


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
        except Exception:
            # Red de seguridad: GroqService ya traduce sus errores a GroqError, pero si algo
            # inesperado se escapa de todas formas, el usuario nunca debe quedarse sin respuesta.
            if not request.done():
                request.cancel()
            logging.exception("Fallo inesperado respondiendo al mensaje")
            await message.answer("Algo se rompió por mi lado 🔧 Intenta otra vez en un momento.")
            return
        finally:
            if not progress.done():
                progress.cancel()

    await send_answer(message, ai, message.from_user.id, answer)
