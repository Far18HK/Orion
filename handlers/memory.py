"""Comandos de memoria semántica para Telegram.

Comandos añadidos:
    /memoria listar [tipo]       — muestra recuerdos semánticos
    /memoria buscar <consulta>   — búsqueda semántica
    /memoria borrar <id>         — elimina un recuerdo por id
    /memoria stats               — estadísticas por tipo

Integración en main.py:
    from handlers.memory import router as memory_router
    dp.include_router(memory_router)
    # Y pasa semantic_memory al dispatcher:
    dp["semantic_memory"] = semantic_memory_instance
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

logger = logging.getLogger(__name__)
router = Router()

# Intentamos importar SemanticMemory; si no existe, los comandos mostrarán un aviso
try:
    from services.semantic_memory import SemanticMemory
    _SM_AVAILABLE = True
except ImportError:
    _SM_AVAILABLE = False


def _sem_mem(message: Message, data: dict) -> "SemanticMemory | None":
    """Extrae el SemanticMemory del dispatcher data."""
    return data.get("semantic_memory")


@router.message(Command("memoria"))
async def cmd_memoria(
    message: Message,
    command: CommandObject,
    **data,
) -> None:
    if message.from_user is None:
        return

    sm = _sem_mem(message, data)
    if sm is None:
        await message.answer(
            "La memoria semántica no está habilitada. "
            "Configura E2B_API_KEY y asegúrate de que semantic_memory esté inicializado."
        )
        return

    args = (command.args or "").strip().split(maxsplit=1)
    subcmd = args[0].lower() if args else "listar"
    rest = args[1] if len(args) > 1 else ""

    user_id = message.from_user.id

    # ── listar ──────────────────────────────────────────────────────
    if subcmd in {"listar", "list", "ls"}:
        kind = rest.strip() or None
        items = sm.list_all(user_id, kind=kind, limit=25)
        if not items:
            await message.answer("No tienes recuerdos semánticos guardados.")
            return

        lines = [f"🧠 Recuerdos semánticos ({len(items)}):"]
        for item in items:
            expires = f" ⏳{item['expires_at'][:10]}" if item.get("expires_at") else ""
            lines.append(
                f"• `{item['id']}` [{item['kind']}]{expires}\n"
                f"  {item['content'][:120]}"
            )
        await message.answer("\n".join(lines), parse_mode="Markdown")

    # ── buscar ──────────────────────────────────────────────────────
    elif subcmd in {"buscar", "search", "recall"}:
        if not rest:
            await message.answer("Uso: /memoria buscar <consulta>")
            return
        try:
            results = await sm.recall(user_id, rest, limit=6)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"Error al buscar: {exc}")
            return

        if not results:
            await message.answer(f"No encontré recuerdos relevantes para: {rest}")
            return

        lines = [f"🔍 Resultados para «{rest}»:"]
        for r in results:
            score_pct = int(r["score"] * 100)
            lines.append(
                f"• `{r['id']}` [{r['kind']}] {score_pct}% similitud\n"
                f"  {r['content'][:200]}"
            )
        await message.answer("\n".join(lines), parse_mode="Markdown")

    # ── borrar ──────────────────────────────────────────────────────
    elif subcmd in {"borrar", "delete", "forget", "olvidar"}:
        mem_id = rest.strip()
        if not mem_id:
            await message.answer("Uso: /memoria borrar <id>")
            return
        deleted = sm.forget(user_id, mem_id)
        if deleted:
            await message.answer(f"Recuerdo `{mem_id}` eliminado.", parse_mode="Markdown")
        else:
            await message.answer(f"No encontré el recuerdo `{mem_id}` o no te pertenece.", parse_mode="Markdown")

    # ── stats ────────────────────────────────────────────────────────
    elif subcmd in {"stats", "estadisticas", "resumen"}:
        stats = sm.stats(user_id)
        if not stats:
            await message.answer("No tienes recuerdos semánticos todavía.")
            return
        total = sum(stats.values())
        lines = [f"🧠 Memoria semántica ({total} recuerdos total):"]
        for kind, count in sorted(stats.items(), key=lambda x: -x[1]):
            lines.append(f"  • {kind}: {count}")
        await message.answer("\n".join(lines))

    # ── ayuda ────────────────────────────────────────────────────────
    else:
        await message.answer(
            "Comandos de memoria semántica:\n"
            "/memoria listar [tipo] — ver todos los recuerdos\n"
            "/memoria buscar <texto> — buscar por similitud\n"
            "/memoria borrar <id> — eliminar un recuerdo\n"
            "/memoria stats — resumen por tipo\n\n"
            "Tipos: preferencia, persona, proyecto, objetivo, decision, hecho, temporal, general"
        )
