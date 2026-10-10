"""Comando /modelo: ver o cambiar el modelo de texto desde el chat."""
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from services.groq_service import GroqError, GroqService, is_chat_model

router = Router()

RESET_WORDS = {"reset", "default", "predeterminado"}
AUTO_WORDS = {"auto", "automático", "automatico", "default", "predeterminado"}


@router.message(Command("modelo"))
async def cmd_modelo(message: Message, command: CommandObject, ai: GroqService) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    arg = (command.args or "").strip()

    if arg.lower() in RESET_WORDS:
        ai.clear_model(user_id)
        await message.answer(f"Volví al modelo predeterminado: {ai.get_model(user_id)} ✅")
        return

    try:
        available = await ai.list_models()
    except GroqError as e:
        await message.answer(str(e))
        return

    if not arg:
        options = "\n".join(f"- {m}" for m in available if is_chat_model(m))
        await message.answer(
            f"Modelo actual: {ai.get_model(user_id)}\n\n"
            f"Disponibles:\n{options}\n\n"
            "Cambia con /modelo <nombre>, o /modelo reset para volver al predeterminado."
        )
        return

    if arg not in available:
        await message.answer(
            "No encuentro ese modelo 🤔 Escribe /modelo para ver la lista y copia el nombre exacto."
        )
        return

    ai.set_model(user_id, arg)
    await message.answer(
        f"Listo, ahora uso {arg} ✅\n"
        "Ojo: no todos los modelos soportan herramientas; si no, respondo sin buscar en internet."
    )


@router.message(Command("cerebro"))
async def cmd_cerebro(message: Message, command: CommandObject, ai: GroqService) -> None:
    """Selecciona una IA, doble cerebro, triple cerebro o modo automático."""
    if message.from_user is None:
        return
    user_id = message.from_user.id
    arg = (command.args or "").strip().lower()
    current = ai.get_brain_mode(user_id)
    if not arg:
        current_name = "auto" if current == 0 else str(current)
        await message.answer(
            f"Cerebro actual: {current_name}\n"
            f"Cerebros disponibles: {ai.available_brains}\n\n"
            "Usa /cerebro auto, /cerebro 1, /cerebro 2 o /cerebro 3."
        )
        return
    if arg in AUTO_WORDS:
        mode = 0
    elif arg in {"1", "2", "3"}:
        mode = int(arg)
    else:
        await message.answer("Usa /cerebro auto, /cerebro 1, /cerebro 2 o /cerebro 3.")
        return
    try:
        ai.set_brain_mode(user_id, mode)
    except GroqError as e:
        await message.answer(str(e))
        return
    label = "automático" if mode == 0 else f"{mode} cerebro(s)"
    await message.answer(f"Listo: ahora estoy en modo {label}. 🧠")
