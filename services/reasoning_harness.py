"""Harness de orquestación: LangGraph controla el bucle, Pydantic valida su política y el
checkpointer (SQLite) lo vuelve reanudable y capaz de pausarse para pedir aprobación humana.

Flujo de un turno:  model -> gate -> tools -> model ... -> fin

- ``model`` llama al LLM y guarda en el estado su respuesta ya serializada (solo dicts/str).
- ``gate`` revisa las herramientas pedidas; si alguna requiere aprobación pausa el grafo con
  ``interrupt()`` hasta que alguien llame a ``resume(..., approved=True|False)``.
- ``tools`` ejecuta las llamadas (las rechazadas no se ejecutan, el modelo recibe el aviso).

Cada paso queda guardado en el checkpointer, así que un turno interrumpido (reinicio, caída)
puede continuar con ``resume(..., approved=None)`` desde el último paso completado.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DENIED_MESSAGE = (
    "El usuario no aprobó esta acción (o no había nadie para aprobarla), así que NO se "
    "ejecutó. Dilo claramente y no la reintentes."
)


class HarnessPolicy(BaseModel):
    """Límites que evitan bucles infinitos y herramientas sin control."""

    max_tool_rounds: int = Field(default=8, ge=1, le=20)


class HarnessState(TypedDict, total=False):
    """Todo lo que vive aquí se guarda en el checkpointer: solo tipos serializables."""

    messages: list[dict]
    model: str
    rounds: int
    assistant: dict  # {"content": str, "tool_calls": [{"id", "name", "arguments"}]}
    denied: list[str]  # ids de llamadas que el usuario no aprobó


@dataclass(frozen=True)
class HarnessResult:
    """Resultado de correr o reanudar un turno."""

    content: str = ""
    thread_id: str = ""
    pending: list[dict] = field(default_factory=list)  # llamadas esperando aprobación

    @property
    def interrupted(self) -> bool:
        return bool(self.pending)


Complete = Callable[..., Awaitable[Any]]
FitBudget = Callable[[list[dict], list[dict] | None], None]
RunCall = Callable[[Any, Any], Awaitable[dict]]
NeedsApproval = Callable[[str, Any], bool]


class ReasoningHarness:
    """Ejecuta un turno de agente como un grafo con estado persistente y pausas."""

    def __init__(
        self,
        complete: Complete,
        fit_budget: FitBudget,
        run_call: RunCall,
        policy: HarnessPolicy | None = None,
        checkpointer: Any | None = None,
        needs_approval: NeedsApproval | None = None,
    ) -> None:
        self.complete = complete
        self.fit_budget = fit_budget
        self.run_call = run_call
        self.policy = policy or HarnessPolicy()
        self.checkpointer = checkpointer
        self.needs_approval = needs_approval
        graph = StateGraph(HarnessState)
        graph.add_node("model", self._model_node)
        graph.add_node("gate", self._gate_node)
        graph.add_node("tools", self._tools_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", self._route_after_model, {"gate": "gate", END: END})
        graph.add_edge("gate", "tools")
        graph.add_edge("tools", "model")
        self._graph = graph.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------ API pública

    async def run(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        ctx: Any,
        *,
        thread_id: str | None = None,
        interactive: bool = True,
    ) -> HarnessResult:
        """Corre un turno nuevo. ``interactive=False`` (automatizaciones) rechaza solo
        cualquier acción que requiera aprobación, porque no hay nadie para darla."""
        state: HarnessState = {
            "messages": messages,
            "model": model,
            "rounds": 0,
            "denied": [],
        }
        return await self._invoke(state, thread_id or uuid4().hex, tools, ctx, interactive)

    async def resume(
        self,
        thread_id: str,
        tools: list[dict],
        ctx: Any,
        *,
        approved: bool | None = None,
        interactive: bool = True,
    ) -> HarnessResult:
        """Continúa un turno guardado.

        - ``approved=True/False``: responde a la pausa de aprobación.
        - ``approved=None``: retoma desde el último paso completado (turno interrumpido).
        """
        if self.checkpointer is None:
            raise RuntimeError("Reanudar un turno requiere un checkpointer.")
        payload = None if approved is None else Command(resume=approved)
        return await self._invoke(payload, thread_id, tools, ctx, interactive)

    async def discard(self, thread_id: str) -> None:
        """Borra del checkpointer los pasos de un turno ya terminado o abandonado."""
        if self.checkpointer is None:
            return
        delete = getattr(self.checkpointer, "adelete_thread", None)
        if delete is None:
            return
        try:
            await delete(thread_id)
        except Exception:  # noqa: BLE001 - limpiar es opcional; nunca debe romper un turno
            logger.debug("No pude borrar el hilo %s del checkpointer", thread_id, exc_info=True)

    # ------------------------------------------------------------------ internos

    async def _invoke(
        self,
        payload: HarnessState | Command | None,
        thread_id: str,
        tools: list[dict],
        ctx: Any,
        interactive: bool,
    ) -> HarnessResult:
        config = {
            "configurable": {
                "thread_id": thread_id,
                # Valores de ejecución (no primitivos): LangGraph no los guarda en el checkpoint
                "context": ctx,
                "tools": tools,
                "interactive": interactive,
            }
        }
        result = await self._graph.ainvoke(payload, config=config)
        if self.checkpointer is not None:
            snapshot = await self._graph.aget_state(config)
            pending = [item.value for task in snapshot.tasks for item in task.interrupts]
            if pending:
                first = pending[0]
                calls = first.get("calls", []) if isinstance(first, dict) else []
                return HarnessResult(thread_id=thread_id, pending=list(calls))
        assistant = (result or {}).get("assistant") or {}
        return HarnessResult(content=assistant.get("content") or "", thread_id=thread_id)

    async def _model_node(self, state: HarnessState, config: RunnableConfig) -> HarnessState:
        configurable = config.get("configurable", {})
        rounds = state.get("rounds", 0)
        tools = configurable.get("tools")
        # Pasada la última vuelta se quitan las herramientas para forzar una respuesta final
        round_tools = tools if rounds < self.policy.max_tool_rounds else None
        # Copia: el recorte de presupuesto no debe tocar el estado ya guardado
        messages = [dict(m) for m in state["messages"]]
        self.fit_budget(messages, round_tools)
        response = await self.complete(state["model"], messages, tools=round_tools)
        message = response.choices[0].message
        calls = [
            {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
            for call in (message.tool_calls or [])
        ]
        return {
            "assistant": {"content": message.content or "", "tool_calls": calls},
            "rounds": rounds + 1,
            "denied": [],
        }

    async def _gate_node(self, state: HarnessState, config: RunnableConfig) -> HarnessState:
        """Pausa para pedir aprobación humana. Antes de ``interrupt`` no hay efectos
        secundarios: al reanudar, LangGraph vuelve a ejecutar este nodo desde el principio."""
        calls = (state.get("assistant") or {}).get("tool_calls") or []
        configurable = config.get("configurable", {})
        ctx = configurable.get("context")
        pending = [
            call
            for call in calls
            if self.needs_approval is not None and self.needs_approval(call["name"], ctx)
        ]
        if not pending:
            return {"denied": []}
        everyone_denied = {"denied": [call["id"] for call in pending]}
        # Sin persona (automatización) o sin checkpointer no se puede pausar: se rechaza
        if not configurable.get("interactive", True) or self.checkpointer is None:
            return everyone_denied
        decision = interrupt({"calls": pending})
        return {"denied": []} if decision is True else everyone_denied

    async def _tools_node(self, state: HarnessState, config: RunnableConfig) -> HarnessState:
        assistant = state["assistant"]
        calls = assistant.get("tool_calls") or []
        denied = set(state.get("denied") or [])
        ctx = config.get("configurable", {}).get("context")
        messages = list(state["messages"])
        messages.append(
            {
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for call in calls
                ],
            }
        )
        results = await asyncio.gather(
            *(self._run_one(call, ctx, call["id"] in denied) for call in calls)
        )
        messages.extend(results)
        return {"messages": messages, "denied": []}

    async def _run_one(self, call: dict, ctx: Any, denied: bool) -> dict:
        if denied:
            return {"role": "tool", "tool_call_id": call["id"], "content": DENIED_MESSAGE}
        ref = SimpleNamespace(
            id=call["id"],
            function=SimpleNamespace(name=call["name"], arguments=call["arguments"]),
        )
        return await self.run_call(ref, ctx)

    def _route_after_model(self, state: HarnessState) -> str:
        calls = (state.get("assistant") or {}).get("tool_calls")
        if calls and state.get("rounds", 0) <= self.policy.max_tool_rounds:
            return "gate"
        return END
