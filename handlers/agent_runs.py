"""Comandos para supervisar ejecuciones autónomas persistentes."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from services.assistant_store import AssistantStore

router = Router()

_STATUS = {
    "running": "ejecutando",
    "waiting_approval": "esperando aprobación",
    "verifying": "verificando",
    "done": "completada",
    "failed": "falló",
    "cancelled": "cancelada",
    "planning": "planificando",
    "queued": "en cola",
}


@router.message(Command("ejecuciones", "runs"))
async def cmd_runs(message: Message, assistant_store: AssistantStore) -> None:
    if message.from_user is None:
        return
    runs = assistant_store.agent_runs(message.from_user.id, "telegram", 10)
    if not runs:
        await message.answer("No hay ejecuciones autónomas registradas todavía.")
        return
    lines = ["Ejecuciones recientes:"]
    for run in runs:
        status = _STATUS.get(run["status"], run["status"])
        objective = " ".join(run["objective"].split())[:120]
        lines.append(f"• {run['id']} · {status}\n  {objective}")
    await message.answer("\n".join(lines))


@router.message(Command("cancelar_ejecucion", "cancelar_run"))
async def cmd_cancel_run(
    message: Message, command: CommandObject, assistant_store: AssistantStore
) -> None:
    if message.from_user is None:
        return
    run_id = (command.args or "").strip()
    if not run_id:
        await message.answer("Indica el id: /cancelar_ejecucion <id>")
        return
    runs = [
        r for r in assistant_store.agent_runs(message.from_user.id, "telegram", 50)
        if r["id"] == run_id
    ]
    if not runs:
        await message.answer("No encontré esa ejecución o no te pertenece.")
        return
    if runs[0]["status"] in {"done", "failed", "cancelled"}:
        await message.answer(f"Esa ejecución ya está {_STATUS.get(runs[0]['status'], runs[0]['status'])}.")
        return
    assistant_store.update_agent_run(run_id, "cancelled", failure_reason="Cancelada por el usuario")
    await message.answer(f"Ejecución {run_id} cancelada. No iniciaré nuevos pasos para ella.")
