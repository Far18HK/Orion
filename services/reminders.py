"""Recordatorios: /recordar, /recordatorios (y /cancelar) y /zona."""
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.reminders import ReminderError, ReminderService
from services.schedule_parser import find_timezones, format_when

router = Router()

CALLBACK_PREFIX = "rc:"  # callback_data = "rc:<id del recordatorio>" (cabe de sobra en los 64 bytes)
BUTTONS_PER_ROW = 5
PREVIEW_CHARS = 80

USAGE = (
    "Uso: /recordar <cuándo> <mensaje>\n\n"
    "En un rato:\n"
    "/recordar 30m Tomar agua  (m, h, d; también 1h30m)\n\n"
    "A una hora o fecha:\n"
    "/recordar 18:30 Llamar a mamá\n"
    "/recordar mañana 8:00 Sacar la basura\n"
    "/recordar lunes 9:00 Reunión\n"
    "/recordar 25/12 10:00 Felicitar\n\n"
    "Repetido:\n"
    "/recordar cada día 8:00 Vitaminas\n"
    "/recordar cada lunes 9:00 Reporte\n"
    "/recordar cada 2h Tomar agua\n\n"
    "Las horas usan tu zona horaria: /zona\n"
    "Ver y cancelar: /recordatorios"
)


@router.message(Command("recordar"))
async def cmd_recordar(message: Message, command: CommandObject, reminders: ReminderService) -> None:
    # command.args ya viene sin "/recordar" ni "@tubot": funciona igual en grupos
    args = (command.args or "").strip()
    if not args:
        await message.answer(USAGE)
        return

    try:
        text, when = reminders.create(message.chat.id, args)
    except ReminderError as e:
        await message.answer(str(e))
        return

    await message.answer(f"Listo, te recuerdo «{text}» {when} ⏰")


def _render_list(
    reminders: ReminderService, chat_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    items = reminders.list_jobs(chat_id)
    if not items:
        return "No tienes recordatorios pendientes 📭", None

    tz = reminders.get_timezone(chat_id) or ZoneInfo("UTC")
    lines = [f"⏰ Tus recordatorios (hora de {tz.key}):", ""]
    for i, item in enumerate(items, start=1):
        text = item.text if len(item.text) <= PREVIEW_CHARS else item.text[:PREVIEW_CHARS] + "…"
        when = format_when(item.next_run, tz) if item.next_run else "pausado"
        detail = f"{item.description} (próximo: {when})" if item.recurring else when
        lines.append(f"{i}. {text}\n   {detail}")
    lines.append("\nToca ❌ para cancelar uno.")

    buttons = [
        InlineKeyboardButton(text=f"❌ {i}", callback_data=f"{CALLBACK_PREFIX}{item.id}")
        for i, item in enumerate(items, start=1)
    ]
    rows = [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("recordatorios", "cancelar"))
async def cmd_recordatorios(message: Message, reminders: ReminderService) -> None:
    text, markup = _render_list(reminders, message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def cb_cancelar(callback: CallbackQuery, reminders: ReminderService) -> None:
    if callback.message is None:
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    cancelled = reminders.cancel(chat_id, callback.data[len(CALLBACK_PREFIX):])
    await callback.answer("Cancelado ✅" if cancelled else "Ese recordatorio ya no existe")

    # Refresca la lista en el mismo mensaje
    text, markup = _render_list(reminders, chat_id)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # Telegram se queja si el mensaje quedó idéntico: no pasa nada


@router.message(Command("zona"))
async def cmd_zona(message: Message, command: CommandObject, reminders: ReminderService) -> None:
    chat_id = message.chat.id
    arg = (command.args or "").strip()

    if not arg:
        tz = reminders.get_timezone(chat_id)
        if tz:
            await message.answer(
                f"Tu zona horaria es {tz.key} (ahora son las {datetime.now(tz):%H:%M}).\n"
                "Para cambiarla: /zona <ciudad o zona>, por ejemplo /zona Lima"
            )
        else:
            await message.answer(
                "Todavía no tienes zona horaria 🌎\n"
                "Escribe /zona y tu ciudad o zona, por ejemplo:\n"
                "/zona Lima\n/zona America/Mexico_City\n/zona Europe/Madrid"
            )
        return

    matches = find_timezones(arg)
    if not matches:
        await message.answer(
            f"No encontré «{arg}» 🤔 Prueba con el nombre de tu ciudad (Lima, Bogotá, Madrid...) "
            "o con la zona completa, como America/Argentina/Buenos_Aires."
        )
        return
    if len(matches) > 1:
        options = "\n".join(f"/zona {m}" for m in matches[:8])
        await message.answer(f"Hay varias coincidencias, elige una:\n{options}")
        return

    tz = reminders.set_timezone(chat_id, matches[0])
    reply = f"Listo, tu zona horaria es {tz.key} (ahora son las {datetime.now(tz):%H:%M}) 🌎"
    if reminders.list_jobs(chat_id):
        reply += "\nLos recordatorios que ya tenías conservan su hora original."
    await message.answer(reply)
