"""Comando /nota para guardar texto en una base de datos de Notion."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.notion import NotionError, NotionService

router = Router()


@router.message(Command("nota"))
async def cmd_nota(message: Message, notion: NotionService | None) -> None:
    if notion is None:
        await message.answer(
            "No tengo Notion configurado todavía.\n"
            "Agrega NOTION_TOKEN y NOTION_DATABASE_ID en tu .env para activar /nota."
        )
        return

    text = message.text.replace("/nota", "", 1).strip()
    if not text:
        await message.answer("Uso: /nota <texto de la nota>")
        return

    try:
        url = await notion.add_note(text)
    except NotionError as e:
        await message.answer(str(e))
        return

    await message.answer(f"Nota guardada en Notion ✅\n{url}")
