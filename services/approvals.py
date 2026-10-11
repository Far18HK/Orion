"""Aprobación humana de acciones con efectos y reanudación de turnos interrumpidos.

Cuando el agente quiere hacer algo sensible (publicar en Discord, crear una automatización...),
el turno se PAUSA y Orion pregunta. Aquí se responde: botones, /aprobar o /rechazar.
Si el bot se reinició a mitad de un turno, /reanudar lo retoma desde el último paso guardado.
"""
from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from handlers.chat import APPROVE_DATA, REJECT_DATA, send_answer
from services.groq_service import GroqError, GroqService

router = Router()


async def _resolve(message: Message, user_id: int, ai: GroqService, approved: bool) -> None:
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await ai.resolve_pending(user_id, approved, chat_id=message.chat.id)
        except GroqError as e:
            await message.answer(str(e))
            return
    await send_answer(message, ai, user_id, answer)


@router.message(Command("aprobar"))
async def cmd_aprobar(message: Message, ai: GroqService) -> None:
    if message.from_user is not None:
        await _resolve(message, message.from_user.id, ai, approved=True)


@router.message(Command("rechazar"))
async def cmd_rechazar(message: Message, ai: GroqService) -> None:
    if message.from_user is not None:
        await _resolve(message, message.from_user.id, ai, approved=False)


@router.message(Command("reanudar"))
async def cmd_reanudar(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await ai.resume_interrupted(message.from_user.id, chat_id=message.chat.id)
        except GroqError as e:
            await message.answer(str(e))
            return
    await send_answer(message, ai, message.from_user.id, answer)


@router.callback_query(F.data.in_({APPROVE_DATA, REJECT_DATA}))
async def on_approval_button(query: CallbackQuery, ai: GroqService) -> None:
    # Cada usuario solo puede resolver SU propia acción pendiente (resolve_pending usa su id)
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    approved = query.data == APPROVE_DATA
    await query.answer("Aprobado ✅" if approved else "Rechazado ❌")
    with suppress(Exception):  # quita los botones para que no se pulsen dos veces
        await message.edit_reply_markup(reply_markup=None)
    await _resolve(message, query.from_user.id, ai, approved)
