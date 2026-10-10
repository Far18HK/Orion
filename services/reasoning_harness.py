"""Harness de orquestación: LangGraph controla el bucle y Pydantic valida su estado."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


class HarnessPolicy(BaseModel):
    """Límites que evitan bucles infinitos y herramientas sin control."""

    max_tool_rounds: int = Field(default=8, ge=1, le=20)


class HarnessState(TypedDict, total=False):
    messages: list[dict]
    tools: list[dict] | None
    model: str
    rounds: int
    response: Any
    message: Any


Complete = Callable[..., Awaitable[Any]]
FitBudget = Callable[[list[dict], list[dict] | None], None]
RunCall = Callable[[Any, Any], Awaitable[dict]]


class ReasoningHarness:
    """Ejecuta un turno de agente como un grafo durable y auditable en memoria."""

    def __init__(
        self,
        complete: Complete,
        fit_budget: FitBudget,
        run_call: RunCall,
        policy: HarnessPolicy | None = None,
    ) -> None:
        self.complete = complete
        self.fit_budget = fit_budget
        self.run_call = run_call
        self.policy = policy or HarnessPolicy()
        graph = StateGraph(HarnessState)
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tools_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", self._route_after_model)
        graph.add_edge("tools", "model")
        self._graph = graph.compile()

    async def run(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        ctx: Any,
    ) -> Any:
        state: HarnessState = {
            "messages": messages,
            "tools": tools,
            "model": model,
            "rounds": 0,
        }
        result = await self._graph.ainvoke(state, config={"configurable": {"context": ctx}})
        return result["message"]

    async def _model_node(self, state: HarnessState, config: RunnableConfig) -> HarnessState:
        rounds = state.get("rounds", 0)
        round_tools = state.get("tools") if rounds < self.policy.max_tool_rounds else None
        messages = state["messages"]
        self.fit_budget(messages, round_tools)
        response = await self.complete(state["model"], messages, tools=round_tools)
        return {"response": response, "message": response.choices[0].message, "rounds": rounds + 1}

    async def _tools_node(self, state: HarnessState, config: RunnableConfig) -> HarnessState:
        message = state["message"]
        calls = message.tool_calls or []
        messages = state["messages"]
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in calls
                ],
            }
        )
        ctx = config.get("configurable", {}).get("context")
        messages.extend(await self._run_calls(calls, ctx))
        return {"messages": messages}

    async def _run_calls(self, calls: list[Any], ctx: Any) -> list[dict]:
        import asyncio

        return await asyncio.gather(*(self.run_call(call, ctx) for call in calls))

    def _route_after_model(self, state: HarnessState) -> str:
        message = state.get("message")
        if (
            message is not None
            and message.tool_calls
            and state.get("rounds", 0) <= self.policy.max_tool_rounds
        ):
            return "tools"
        return END
