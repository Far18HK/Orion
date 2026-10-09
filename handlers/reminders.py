"""Comando /recordar para programar recordatorios."""
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from services.reminders import ReminderError, ReminderService, parse_duration

router = Router()


@router.message(Command("recordar"))
async def cmd_recordar(message: Message, command: CommandObject, reminders: ReminderService) -> None:
    # command.args ya viene sin "/recordar" ni "@tubot": funciona igual en grupos
    args = (command.args or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Uso: /recordar <tiempo> <mensaje>\n"
            "Ejemplo: /recordar 30m Tomar agua\n"
            "Unidades válidas: m (minutos), h (horas), d (días)"
        )
        return

    duration_text, text = args
    try:
        delay = parse_duration(duration_text)
    except ReminderError as e:
        await message.answer(str(e))
        return

    reminders.schedule(message.chat.id, delay, text)
    await message.answer(f"Listo, te recuerdo «{text}» en {duration_text} ⏰")
