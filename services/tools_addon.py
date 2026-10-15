"""Herramientas adicionales para tools.py.

Añade tres grupos de tools al agente:
  1. Memoria semántica: semantic_remember, semantic_recall, semantic_forget
  2. Sandbox de código: run_code
  3. Ejecuciones autónomas: create_agent_run, get_agent_run

INSTRUCCIONES DE INTEGRACIÓN
──────────────────────────────
1. Copia este archivo a services/tools_addon.py
2. En services/tools.py, al final de los imports añade:
       from services.tools_addon import ADDON_TOOLS, init_addon_context
3. Al final de la lista TOOLS añade:
       *ADDON_TOOLS,
4. En ToolContext (dataclass), añade:
       semantic_memory: "SemanticMemory | None" = None
       agent_runner: "AgentRunner | None" = None
5. En groq_service.py (o donde construyas ToolContext), inicializa:
       ctx = ToolContext(
           ...,
           semantic_memory=self.semantic_memory,   # SemanticMemory | None
           agent_runner=self.agent_runner,          # AgentRunner | None
       )
6. En requirements.txt añade:
       e2b-code-interpreter>=1.0; python_version >= "3.11"
       openai>=1.0  # ya lo tienes, lo usamos para embeddings
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _str(description: str, **extra) -> dict:
    return {"type": "string", "description": description, **extra}


def _arr(description: str, items: dict | None = None) -> dict:
    return {"type": "array", "description": description, "items": items or {"type": "string"}}


# ──────────────────────────────────────────────────────────────────────────────
# Handlers de memoria semántica
# ──────────────────────────────────────────────────────────────────────────────

async def _t_semantic_remember(ctx, args: dict) -> str:
    """Guarda un recuerdo con embedding semántico."""
    if ctx.semantic_memory is None:
        return "La memoria semántica no está habilitada en este despliegue."

    content = str(args.get("content", "")).strip()
    if not content:
        return "Error: el campo 'content' es obligatorio."

    kind = str(args.get("kind", "general"))
    expires_in = args.get("expires_in_days")
    try:
        expires_in = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in = None

    try:
        mem_id = await ctx.semantic_memory.remember(
            ctx.user_id,
            content,
            kind=kind,
            source="agente",
            expires_in_days=expires_in,
        )
        return f"Recuerdo guardado (id={mem_id}, tipo={kind})."
    except Exception as exc:  # noqa: BLE001
        logger.error("semantic_remember falló: %s", exc)
        return f"Error al guardar el recuerdo: {exc}"


async def _t_semantic_recall(ctx, args: dict) -> str:
    """Recupera recuerdos relevantes por similitud semántica."""
    if ctx.semantic_memory is None:
        return "La memoria semántica no está habilitada en este despliegue."

    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: el campo 'query' es obligatorio."

    limit = min(int(args.get("limit", 5)), 10)
    kind = args.get("kind") or None

    try:
        results = await ctx.semantic_memory.recall(
            ctx.user_id, query, limit=limit, kind=kind
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("semantic_recall falló: %s", exc)
        return f"Error al buscar recuerdos: {exc}"

    if not results:
        return "No encontré recuerdos relevantes para esa consulta."

    lines = ["Recuerdos relevantes:"]
    for r in results:
        score_pct = int(r["score"] * 100)
        lines.append(f"• [{r['kind']}] {r['content'][:200]}  (similitud {score_pct}%)")
    return "\n".join(lines)


async def _t_semantic_forget(ctx, args: dict) -> str:
    """Elimina un recuerdo semántico por id."""
    if ctx.semantic_memory is None:
        return "La memoria semántica no está habilitada en este despliegue."

    mem_id = str(args.get("mem_id", "")).strip()
    if not mem_id:
        return "Error: el campo 'mem_id' es obligatorio."

    deleted = ctx.semantic_memory.forget(ctx.user_id, mem_id)
    return f"Recuerdo {mem_id} eliminado." if deleted else f"No encontré el recuerdo {mem_id}."


async def _t_semantic_list(ctx, args: dict) -> str:
    """Lista los recuerdos semánticos guardados."""
    if ctx.semantic_memory is None:
        return "La memoria semántica no está habilitada en este despliegue."

    kind = args.get("kind") or None
    limit = min(int(args.get("limit", 20)), 50)

    results = ctx.semantic_memory.list_all(ctx.user_id, kind=kind, limit=limit)
    if not results:
        return "No hay recuerdos semánticos guardados."

    lines = [f"Recuerdos ({len(results)}):"]
    for r in results:
        expires = f" [expira {r['expires_at'][:10]}]" if r.get("expires_at") else ""
        lines.append(f"• {r['id']} [{r['kind']}]{expires}\n  {r['content'][:150]}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Handlers de sandbox
# ──────────────────────────────────────────────────────────────────────────────

async def _t_run_code(ctx, args: dict) -> str:
    """Ejecuta código Python o bash en un sandbox seguro E2B."""
    from services.sandbox import run_code_tool
    return await run_code_tool(args)


# ──────────────────────────────────────────────────────────────────────────────
# Handlers de ejecuciones autónomas
# ──────────────────────────────────────────────────────────────────────────────

async def _t_create_agent_run(ctx, args: dict) -> str:
    """Crea y registra una ejecución autónoma persistente."""
    if ctx.store is None:
        return "Error: el store no está disponible."

    objective = str(args.get("objective", "")).strip()
    if not objective:
        return "Error: el campo 'objective' es obligatorio."

    max_steps = min(int(args.get("max_steps", 12)), 20)
    budget = {"max_steps": max_steps}

    run_id = ctx.store.create_agent_run(ctx.user_id, ctx.platform, objective, budget)
    return (
        f"Ejecución autónoma creada (id={run_id}).\n"
        f"Objetivo: {objective[:200]}\n"
        f"Presupuesto: {max_steps} pasos máx.\n"
        f"Usa /ejecuciones para seguir el progreso."
    )


async def _t_get_agent_run(ctx, args: dict) -> str:
    """Consulta el estado y pasos de una ejecución autónoma."""
    if ctx.store is None:
        return "Error: el store no está disponible."

    run_id = str(args.get("run_id", "")).strip()
    if not run_id:
        # Sin id: muestra la más reciente
        runs = ctx.store.agent_runs(ctx.user_id, ctx.platform, 1)
        if not runs:
            return "No hay ejecuciones autónomas para este usuario."
        run = runs[0]
    else:
        runs = ctx.store.agent_runs(ctx.user_id, ctx.platform, 50)
        run = next((r for r in runs if r["id"] == run_id), None)
        if not run:
            return f"No encontré la ejecución '{run_id}'."

    steps = ctx.store.agent_steps(run["id"])
    status_map = {
        "running": "⚙️ ejecutando", "planning": "📋 planificando",
        "done": "✅ completada", "failed": "❌ falló",
        "cancelled": "🚫 cancelada", "queued": "⏳ en cola",
        "verifying": "🔍 verificando", "waiting_approval": "⏸ esperando aprobación",
    }
    status = status_map.get(run["status"], run["status"])

    lines = [
        f"Ejecución {run['id']} · {status}",
        f"Objetivo: {run['objective'][:200]}",
        f"Creada: {run['created_at'][:16]}",
    ]
    if run.get("result"):
        lines.append(f"Resultado: {run['result'][:300]}")
    if run.get("failure_reason"):
        lines.append(f"Motivo fallo: {run['failure_reason'][:200]}")

    if steps:
        lines.append(f"\nPasos ({len(steps)}):")
        for s in steps:
            icon = {"done": "✅", "failed": "❌", "running": "⚙️", "pending": "⬜"}.get(s["status"], "•")
            lines.append(f"  {icon} {s['position']+1}. {s['title'][:100]}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Registro de tools (mismo formato que TOOLS en tools.py)
# ──────────────────────────────────────────────────────────────────────────────

# Importamos Tool y _str del módulo principal solo si están disponibles
# (si no, definimos una versión mínima compatible)
try:
    from services.tools import Tool  # type: ignore[import]
except ImportError:
    from dataclasses import dataclass
    from collections.abc import Awaitable, Callable

    @dataclass(frozen=True)
    class Tool:  # type: ignore[no-redef]
        name: str
        description: str
        properties: dict
        required: tuple
        handler: Callable
        needs: str | None = None

        def spec(self) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": {
                        "type": "object",
                        "properties": self.properties,
                        "required": list(self.required),
                    },
                },
            }


ADDON_TOOLS: list[Tool] = [
    # ── Memoria semántica ──────────────────────────────────────────────
    Tool(
        "semantic_remember",
        "Guarda un recuerdo con memoria semántica (embeddings). Usa esto para almacenar "
        "preferencias, datos de personas, objetivos, proyectos o decisiones importantes "
        "que deberías recordar más allá del contexto actual.",
        {
            "content": _str("Texto del recuerdo a guardar. Sé específico y autocontenido."),
            "kind": _str(
                "Tipo de recuerdo.",
                enum=["preferencia", "persona", "proyecto", "objetivo",
                      "decision", "hecho", "temporal", "general"],
            ),
            "expires_in_days": {
                "type": "integer",
                "description": "Días hasta que expire el recuerdo (opcional). "
                               "Útil para información temporal como eventos próximos.",
            },
        },
        ("content",),
        _t_semantic_remember,
    ),
    Tool(
        "semantic_recall",
        "Busca recuerdos semánticos relevantes para una consulta. Devuelve los recuerdos "
        "más parecidos aunque no coincidan exactamente en palabras. Usa esto antes de "
        "responder preguntas sobre preferencias, personas o proyectos del usuario.",
        {
            "query": _str("Consulta en lenguaje natural para buscar recuerdos similares."),
            "kind": _str(
                "Filtrar por tipo de recuerdo (opcional).",
                enum=["preferencia", "persona", "proyecto", "objetivo",
                      "decision", "hecho", "temporal", "general"],
            ),
            "limit": {"type": "integer", "description": "Máximo de resultados (1-10). Default: 5."},
        },
        ("query",),
        _t_semantic_recall,
    ),
    Tool(
        "semantic_forget",
        "Elimina un recuerdo semántico específico por su id.",
        {"mem_id": _str("Id del recuerdo a eliminar (obtenido con semantic_recall o semantic_list).")},
        ("mem_id",),
        _t_semantic_forget,
    ),
    Tool(
        "semantic_list",
        "Lista todos los recuerdos semánticos guardados, opcionalmente filtrados por tipo.",
        {
            "kind": _str("Filtrar por tipo (opcional)."),
            "limit": {"type": "integer", "description": "Máximo de resultados. Default: 20."},
        },
        (),
        _t_semantic_list,
    ),

    # ── Sandbox de código ─────────────────────────────────────────────
    Tool(
        "run_code",
        "Ejecuta código Python o bash en un sandbox seguro y aislado (E2B). "
        "Úsalo para: calcular cosas complejas, transformar datos, generar archivos, "
        "probar algoritmos o automatizar tareas que requieran código real. "
        "NO tiene acceso a tus archivos locales ni a secretos del servidor.",
        {
            "code": _str("Código a ejecutar. Autocontenido: no asumas variables previas."),
            "language": _str(
                "Lenguaje de ejecución.",
                enum=["python", "bash"],
            ),
            "packages": _arr(
                "Paquetes pip a instalar antes de ejecutar (opcional). "
                "Ej: [\"pandas\", \"matplotlib\"]."
            ),
        },
        ("code",),
        _t_run_code,
    ),

    # ── Ejecuciones autónomas ─────────────────────────────────────────
    Tool(
        "create_agent_run",
        "Crea una ejecución autónoma persistente para un objetivo complejo. "
        "El agente planificará pasos, los ejecutará y verificará cada resultado. "
        "La ejecución sobrevive a reinicios. Usa esto para tareas de múltiples pasos "
        "como 'investiga X y escribe un resumen', 'analiza estos datos y genera un informe'.",
        {
            "objective": _str("Objetivo claro y específico que el agente debe alcanzar."),
            "max_steps": {
                "type": "integer",
                "description": "Número máximo de pasos (1-20). Default: 12.",
            },
        },
        ("objective",),
        _t_create_agent_run,
    ),
    Tool(
        "get_agent_run",
        "Consulta el estado y progreso de una ejecución autónoma.",
        {
            "run_id": _str("Id de la ejecución (opcional; sin id muestra la más reciente)."),
        },
        (),
        _t_get_agent_run,
    ),
]
