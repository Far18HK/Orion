"""Comando /modelo: ver o cambiar el modelo de texto desde el chat."""
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from services.groq_service import GroqError, GroqService, is_chat_model

router = Router()

RESET_WORDS = {"reset", "default", "predeterminado"}


@router.message(Command("modelo"))
async def cmd_modelo(message: Message, command: CommandObject, ai: GroqService) -> None:
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
