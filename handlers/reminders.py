"""Comando /recordar para programar recordatorios."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.reminders import ReminderError, ReminderService, parse_duration

router = Router()


@router.message(Command("recordar"))
async def cmd_recordar(message: Message, reminders: ReminderService) -> None:
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "Uso: /recordar <tiempo> <mensaje>\n"
            "Ejemplo: /recordar 30m Tomar agua\n"
            "Unidades válidas: m (minutos), h (horas), d (días)"
        )
        return

    _, duration_text, text = args
    try:
        delay = parse_duration(duration_text)
    except ReminderError as e:
        await message.answer(str(e))
        return

    reminders.schedule(message.chat.id, delay, text)
    await message.answer(f"Listo, te recuerdo «{text}» en {duration_text} ⏰")
