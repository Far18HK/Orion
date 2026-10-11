"""Capas tipadas opcionales de PydanticAI para Orion.

LangGraph sigue siendo el orquestador durable. Este módulo solo aporta salidas
estructuradas para planificación y verificación; no ejecuta herramientas ni
puede saltarse la política de aprobación.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)


class PlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    expected_result: str = Field(min_length=1, max_length=1000)
    tool_hint: str | None = Field(default=None, max_length=100)


class AgentPlan(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    definition_of_done: str = Field(min_length=1, max_length=1000)
    risk_notes: list[str] = Field(default_factory=list, max_length=8)


class VerificationResult(BaseModel):
    status: Literal["verified", "partial", "failed"]
    evidence: str = Field(min_length=1, max_length=2000)
    next_action: str | None = Field(default=None, max_length=500)


class PydanticHarness:
    """Genera y valida estructuras; la ejecución permanece en LangGraph."""

    def __init__(self, model: str, api_key: str, base_url: str) -> None:
        provider = OpenAIProvider(api_key=api_key, base_url=base_url)
        typed_model = OpenAIChatModel(model, provider=provider)
        self.planner = Agent(
            typed_model,
            name="orion-planner",
            output_type=AgentPlan,
            retries=2,
            system_prompt=(
                "Eres el planificador de Orion. Devuelve un plan corto, concreto y verificable. "
                "No ejecutes herramientas, no inventes resultados y no incluyas pasos innecesarios."
            ),
        )
        self.verifier = Agent(
            typed_model,
            name="orion-verifier",
            output_type=VerificationResult,
            retries=2,
            system_prompt=(
                "Eres el verificador de Orion. Evalúa solo la evidencia proporcionada. "
                "Si no demuestra el resultado, marca partial o failed; nunca supongas que algo funcionó."
            ),
        )

    async def plan(self, objective: str) -> AgentPlan:
        result = await self.planner.run(objective[:4000])
        return result.output

    async def verify(self, objective: str, expected: str, evidence: str) -> VerificationResult:
        prompt = (
            f"Objetivo: {objective[:2000]}\n"
            f"Resultado esperado: {expected[:1000]}\n"
            f"Evidencia observada:\n{evidence[:6000]}"
        )
        result = await self.verifier.run(prompt)
        return result.output
