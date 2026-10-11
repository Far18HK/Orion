"""Comandos de notas en Notion: /nota, /notas y /buscarnota."""
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import LinkPreviewOptions, Message

from services.notion import Note, NotionError, NotionService, extract_tags

router = Router()

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # Evita que Telegram despliegue cada enlace
DEFAULT_LIST = 5
MAX_LIST = 10


def format_notes(notes: list[Note]) -> str:
    lines = []
    for i, note in enumerate(notes, start=1):
        tags = " ".join(f"#{t}" for t in note.tags)
        meta = " · ".join(x for x in (note.created, tags) if x)
        lines.append(f"{i}. {note.title}" + (f" ({meta})" if meta else "") + f"\n{note.url}")
    return "\n\n".join(lines)


async def _notion_missing(message: Message) -> None:
    await message.answer(
        "No tengo Notion configurado todavía.\n"
        "Agrega NOTION_TOKEN y NOTION_DATABASE_ID en tu .env para activar las notas."
    )


@router.message(Command("nota"))
async def cmd_nota(message: Message, command: CommandObject, notion: NotionService | None) -> None:
    if notion is None:
        await _notion_missing(message)
        return

    # command.args ya viene sin "/nota" ni "@tubot", así que funciona igual en grupos
    text, tags = extract_tags((command.args or "").strip())
    if not text:
        await message.answer(
            "Uso: /nota <texto> — puedes añadir #etiquetas\n"
            "Ejemplo: /nota Llamar al dentista #salud #pendiente"
        )
        return

    try:
        url = await notion.add_note(text, tags)
    except NotionError as e:
        await message.answer(str(e))
        return

    await message.answer(f"Nota guardada en Notion ✅\n{url}", link_preview_options=NO_PREVIEW)


@router.message(Command("notas"))
async def cmd_notas(message: Message, command: CommandObject, notion: NotionService | None) -> None:
    if notion is None:
        await _notion_missing(message)
        return

    arg = (command.args or "").strip()
    limit = min(max(int(arg), 1), MAX_LIST) if arg.isdigit() else DEFAULT_LIST

    try:
        notes = await notion.recent(limit)
    except NotionError as e:
        await message.answer(str(e))
        return

    if not notes:
        await message.answer("Todavía no tienes notas guardadas 📭")
        return
    await message.answer(
        f"📝 Tus últimas {len(notes)} notas:\n\n{format_notes(notes)}",
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("buscarnota"))
async def cmd_buscarnota(
    message: Message, command: CommandObject, notion: NotionService | None
) -> None:
    if notion is None:
        await _notion_missing(message)
        return

    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Uso: /buscarnota <texto> — busca en los títulos\n"
            "Para buscar por etiqueta: /buscarnota #salud"
        )
        return

    try:
        notes = await notion.search(query)
    except NotionError as e:
        await message.answer(str(e))
        return

    if not notes:
        await message.answer(f"No encontré notas con «{query}» 🤷")
        return
    await message.answer(
        f"🔎 Encontré {len(notes)} nota(s):\n\n{format_notes(notes)}",
        link_preview_options=NO_PREVIEW,
    )
