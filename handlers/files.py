"""Comandos para gestionar el workspace de archivos de Orion.

/archivos            — Muestra el uso y los archivos disponibles
/archivos listar     — Igual que /archivos
/archivos borrar <nombre>  — Borra un archivo concreto
/archivos limpiar    — Borra archivos con más de 30 días de antigüedad
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from services.groq_service import GroqService
from services.workspace import (
    WORKSPACE_QUOTA_BYTES,
    WorkspaceError,
    cleanup_old_files,
    delete_file,
    list_files,
    workspace_usage,
)

router = Router()


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


@router.message(Command("archivos"))
async def cmd_archivos(message: Message, command: CommandObject, ai: GroqService) -> None:
    if message.from_user is None:
        return

    user_id = message.from_user.id
    args = (command.args or "").strip()
    root = ai.workspace_root

    # /archivos borrar <nombre>
    if args.lower().startswith("borrar "):
        filename = args[7:].strip()
        if not filename:
            await message.answer("Indica el nombre del archivo. Ej: /archivos borrar inbox/doc.pdf")
            return
        try:
            freed = await _run(delete_file, root, user_id, filename)
            await message.answer(f"✅ Borré «{filename}» ({_fmt_bytes(freed)} liberados).")
        except WorkspaceError as e:
            await message.answer(f"❌ {e}")
        return

    # /archivos limpiar
    if args.lower() == "limpiar":
        count, freed = await _run(cleanup_old_files, root, user_id)
        if count == 0:
            await message.answer("No hay archivos con más de 30 días de antigüedad.")
        else:
            await message.answer(
                f"🧹 Borré {count} archivo{'s' if count != 1 else ''} "
                f"({_fmt_bytes(freed)} liberados)."
            )
        return

    # /archivos o /archivos listar
    used = await _run(workspace_usage, root, user_id)
    quota = WORKSPACE_QUOTA_BYTES
    pct = used / quota * 100 if quota else 0
    bar = _usage_bar(pct)
    listing = await _run(list_files, root, user_id)

    header = (
        f"💾 Almacenamiento: {_fmt_bytes(used)} / {_fmt_bytes(quota)} ({pct:.1f}%)\n"
        f"{bar}\n\n"
    )
    await message.answer(header + listing)
    await message.answer(
        "Comandos:\n"
        "• /archivos borrar <nombre> — borra ese archivo\n"
        "• /archivos limpiar — borra archivos con >30 días de antigüedad"
    )


async def _run(fn, *args, **kwargs):
    """Ejecuta una función de workspace en un hilo para no bloquear el event loop."""
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)


def _usage_bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "🟢" if pct < 70 else "🟡" if pct < 90 else "🔴"
    return f"{color} [{bar}]"
