"""Coordinador de ejecuciones autónomas persistentes (AgentRun).

Convierte "un chat que usa herramientas" en un agente que puede:
- Recibir un objetivo y planificarlo en pasos reales almacenados en SQLite.
- Ejecutar cada paso con las tools del turno actual.
- Verificar el resultado antes de avanzar.
- Sobrevivir a reinicios: reanuda desde el último paso pendiente.
- Respetar un presupuesto (max_steps, deadline).
- Informar al usuario del progreso en cada paso.

Integración:
    En groq_service.py o en el handler de chat, llama:
        runner = AgentRunner(store, tool_executor, llm_fn)
        result = await runner.run(ctx, objective, send_progress=message.answer)

    El AgentRunner NO llama a Telegram directamente: recibe un callable
    `send_progress` para aislar la lógica de negocio del transporte.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from services.assistant_store import AssistantStore

logger = logging.getLogger(__name__)

# Firma del callable que ejecuta UNA herramienta y devuelve el resultado
ToolExecutorFn = Callable[[str, dict], Awaitable[str]]

# Firma del LLM: recibe un prompt de sistema + mensaje usuario, devuelve texto
LLMFn = Callable[[str, str], Awaitable[str]]

# Firma del progreso: envía texto al usuario (puede ser message.answer en Telegram)
ProgressFn = Callable[[str], Awaitable[Any]]

MAX_STEPS_DEFAULT = 12
STEP_TIMEOUT_S = 120
VERIFY_PROMPT = """Actúas como verificador de pasos de agente.
Objetivo del paso: {objective}
Resultado esperado: {expected}
Evidencia obtenida: {evidence}

¿El paso se completó exitosamente? Responde SOLO con uno de:
- COMPLETADO: <razón breve>
- PARCIAL: <razón breve>
- FALLIDO: <razón breve>
"""

PLAN_PROMPT = """Eres el planificador de Orion. Diseña un plan ejecutable para el siguiente objetivo.

Objetivo: {objective}

Reglas:
- Máximo {max_steps} pasos.
- Cada paso debe usar UNA herramienta concreta (no inventes herramientas).
- Herramientas disponibles: {tools}
- El primer paso debe ser el más seguro y reversible.
- Devuelve SOLO un JSON con esta estructura (sin markdown):
{{
  "steps": [
    {{"title": "...", "tool": "...", "args": {{...}}, "expected_result": "..."}}
  ]
}}
"""


@dataclass
class StepPlan:
    title: str
    tool: str
    args: dict
    expected_result: str


@dataclass
class RunBudget:
    max_steps: int = MAX_STEPS_DEFAULT
    deadline: datetime | None = None
    max_tool_calls: int = 30

    @classmethod
    def from_dict(cls, d: dict) -> "RunBudget":
        deadline = None
        if d.get("deadline_iso"):
            try:
                deadline = datetime.fromisoformat(d["deadline_iso"])
            except ValueError:
                pass
        return cls(
            max_steps=int(d.get("max_steps", MAX_STEPS_DEFAULT)),
            deadline=deadline,
            max_tool_calls=int(d.get("max_tool_calls", 30)),
        )

    def to_dict(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "deadline_iso": self.deadline.isoformat() if self.deadline else None,
            "max_tool_calls": self.max_tool_calls,
        }

    def is_expired(self) -> bool:
        return self.deadline is not None and datetime.now(UTC) > self.deadline


@dataclass
class RunResult:
    run_id: str
    status: str          # "done" | "failed" | "cancelled" | "budget_exceeded"
    steps_done: int
    steps_total: int
    summary: str
    errors: list[str] = field(default_factory=list)


class AgentRunner:
    """Orquesta la ejecución de un AgentRun completo."""

    def __init__(
        self,
        store: AssistantStore,
        tool_executor: ToolExecutorFn,
        llm: LLMFn,
        available_tools: list[str] | None = None,
    ) -> None:
        self._store = store
        self._execute_tool = tool_executor
        self._llm = llm
        self._available_tools = available_tools or []

    # ---------------------------------------------------------------- API pública

    async def run(
        self,
        user_id: int,
        platform: str,
        objective: str,
        budget: RunBudget | None = None,
        send_progress: ProgressFn | None = None,
    ) -> RunResult:
        """Planifica y ejecuta un objetivo completo."""
        budget = budget or RunBudget()
        run_id = self._store.create_agent_run(
            user_id, platform, objective, budget.to_dict()
        )
        logger.info("AgentRun %s iniciado: %s", run_id, objective[:80])

        await self._notify(send_progress, f"🚀 Iniciando ejecución `{run_id}`\n📋 {objective[:200]}")

        try:
            return await self._execute_run(run_id, user_id, objective, budget, send_progress)
        except asyncio.CancelledError:
            self._store.update_agent_run(run_id, "cancelled", failure_reason="Tarea cancelada")
            raise
        except Exception as exc:  # noqa: BLE001
            reason = f"Error inesperado: {exc}"
            self._store.update_agent_run(run_id, "failed", failure_reason=reason)
            logger.exception("AgentRun %s falló", run_id)
            return RunResult(run_id, "failed", 0, 0, reason, errors=[reason])

    async def resume(
        self,
        run_id: str,
        send_progress: ProgressFn | None = None,
    ) -> RunResult:
        """Reanuda una ejecución que quedó en estado 'running' tras un reinicio."""
        runs = self._store.agent_runs(0, limit=200)  # busca por id
        run = next((r for r in runs if r["id"] == run_id), None)
        if not run:
            return RunResult(run_id, "failed", 0, 0, "Ejecución no encontrada")

        budget = RunBudget.from_dict({})
        if run.get("budget"):
            try:
                import json
                budget = RunBudget.from_dict(json.loads(run["budget"]))
            except Exception:  # noqa: BLE001
                pass

        await self._notify(send_progress, f"▶️ Reanudando ejecución `{run_id}`")
        return await self._execute_run(
            run_id, run["user_id"], run["objective"], budget, send_progress
        )

    # ---------------------------------------------------------------- lógica interna

    async def _execute_run(
        self,
        run_id: str,
        user_id: int,
        objective: str,
        budget: RunBudget,
        send_progress: ProgressFn | None,
    ) -> RunResult:
        self._store.update_agent_run(run_id, "planning")

        # 1. Obtener pasos ya planificados o planificar desde cero
        existing_steps = self._store.agent_steps(run_id)
        if not existing_steps:
            steps = await self._plan(objective, budget.max_steps)
            if not steps:
                self._store.update_agent_run(run_id, "failed", failure_reason="No se pudo planificar")
                return RunResult(run_id, "failed", 0, 0, "El planificador no devolvió pasos.")

            for i, step in enumerate(steps):
                self._store.add_agent_step(run_id, i, step.title, step.expected_result)
                # Guardamos tool+args en el título para recuperarlos
                # (en una versión futura: columnas tool/args_json en agent_steps)
            existing_steps = self._store.agent_steps(run_id)

        plan_text = "\n".join(
            f"  {i+1}. {s['title']}" for i, s in enumerate(existing_steps)
        )
        await self._notify(send_progress, f"📝 Plan ({len(existing_steps)} pasos):\n{plan_text}")

        # 2. Ejecutar pasos pendientes
        self._store.update_agent_run(run_id, "running")
        errors: list[str] = []
        done_count = 0
        tool_calls = 0

        # Re-planificamos para tener los objetos StepPlan con tool+args
        step_plans = await self._plan(objective, budget.max_steps)
        # Emparejar por posición con los steps de la BD
        plan_map: dict[int, StepPlan] = {i: p for i, p in enumerate(step_plans)}

        for db_step in existing_steps:
            if db_step["status"] in {"done", "skipped"}:
                done_count += 1
                continue
            if db_step["status"] == "failed":
                continue
            if budget.is_expired():
                self._store.update_agent_run(run_id, "failed", failure_reason="Deadline superado")
                return RunResult(run_id, "budget_exceeded", done_count, len(existing_steps),
                                 "Tiempo límite superado.", errors)
            if tool_calls >= budget.max_tool_calls:
                self._store.update_agent_run(run_id, "failed", failure_reason="Max tool calls")
                return RunResult(run_id, "budget_exceeded", done_count, len(existing_steps),
                                 "Presupuesto de llamadas agotado.", errors)

            position = db_step["position"]
            step_plan = plan_map.get(position)
            step_id = db_step["id"]

            await self._notify(
                send_progress,
                f"⚙️ Paso {position + 1}/{len(existing_steps)}: {db_step['title']}"
            )
            self._store.update_agent_step(step_id, "running")

            # Ejecutar la herramienta del paso
            evidence = ""
            error_msg = ""
            if step_plan and step_plan.tool:
                try:
                    async with asyncio.timeout(STEP_TIMEOUT_S):
                        evidence = await self._execute_tool(step_plan.tool, step_plan.args)
                    tool_calls += 1
                except TimeoutError:
                    error_msg = f"Timeout tras {STEP_TIMEOUT_S}s"
                except Exception as exc:  # noqa: BLE001
                    error_msg = str(exc)
            else:
                evidence = "(paso de razonamiento sin herramienta)"

            if error_msg:
                self._store.update_agent_step(
                    step_id, "failed", evidence=evidence, error=error_msg,
                    attempts=db_step["attempts"] + 1,
                )
                errors.append(f"Paso {position + 1}: {error_msg}")
                await self._notify(send_progress, f"❌ Paso {position + 1} falló: {error_msg}")
                continue

            # Verificar resultado
            self._store.update_agent_run(run_id, "verifying")
            verdict = await self._verify(db_step["title"], db_step["expected_result"], evidence)
            step_ok = verdict.startswith("COMPLETADO")

            self._store.update_agent_step(
                step_id,
                "done" if step_ok else "failed",
                evidence=f"{evidence[:1000]}\n\nVeredicto: {verdict}",
                error="" if step_ok else verdict,
                attempts=db_step["attempts"] + 1,
            )
            self._store.update_agent_run(run_id, "running")

            if step_ok:
                done_count += 1
                await self._notify(send_progress, f"✅ Paso {position + 1} completado")
            else:
                errors.append(f"Paso {position + 1}: {verdict}")
                await self._notify(send_progress, f"⚠️ Paso {position + 1} parcial/fallido: {verdict}")

        # 3. Resultado final
        total = len(existing_steps)
        all_done = done_count == total and not errors
        final_status = "done" if all_done else ("failed" if errors else "done")
        summary = (
            f"Completado {done_count}/{total} pasos."
            if not errors
            else f"Completado {done_count}/{total} pasos. Errores: {'; '.join(errors[:3])}"
        )
        self._store.update_agent_run(run_id, final_status, result=summary)

        icon = "🎉" if all_done else "⚠️"
        await self._notify(send_progress, f"{icon} Ejecución `{run_id}` finalizada.\n{summary}")

        return RunResult(run_id, final_status, done_count, total, summary, errors)

    # ---------------------------------------------------------------- planificador

    async def _plan(self, objective: str, max_steps: int) -> list[StepPlan]:
        import json as _json

        tools_str = ", ".join(self._available_tools) if self._available_tools else "web_search, calculate, read_file, write_file"
        prompt = PLAN_PROMPT.format(
            objective=objective, max_steps=max_steps, tools=tools_str
        )
        try:
            raw = await self._llm("Eres un planificador de tareas de IA. Responde solo con JSON.", prompt)
            # Limpiar markdown si el modelo lo añade
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = _json.loads(raw)
            steps = []
            for item in data.get("steps", [])[:max_steps]:
                steps.append(StepPlan(
                    title=str(item.get("title", "Paso sin título"))[:200],
                    tool=str(item.get("tool", "")),
                    args=item.get("args", {}),
                    expected_result=str(item.get("expected_result", ""))[:500],
                ))
            return steps
        except Exception as exc:  # noqa: BLE001
            logger.warning("Planificador falló: %s", exc)
            return []

    # ---------------------------------------------------------------- verificador

    async def _verify(self, objective: str, expected: str, evidence: str) -> str:
        prompt = VERIFY_PROMPT.format(
            objective=objective, expected=expected, evidence=evidence[:2000]
        )
        try:
            result = await self._llm("Eres un verificador estricto.", prompt)
            result = result.strip()
            if any(result.startswith(k) for k in ("COMPLETADO", "PARCIAL", "FALLIDO")):
                return result
            # Si el modelo no siguió el formato, inferimos
            upper = result.upper()
            if "COMPLETADO" in upper or "ÉXITO" in upper or "EXITOSO" in upper:
                return f"COMPLETADO: {result[:100]}"
            if "FALLIDO" in upper or "ERROR" in upper or "FALLO" in upper:
                return f"FALLIDO: {result[:100]}"
            return f"PARCIAL: {result[:100]}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Verificador falló: %s", exc)
            return f"PARCIAL: No se pudo verificar ({exc})"

    # ---------------------------------------------------------------- utilidad

    @staticmethod
    async def _notify(fn: ProgressFn | None, text: str) -> None:
        if fn is not None:
            try:
                await fn(text)
            except Exception as exc:  # noqa: BLE001
                logger.debug("send_progress falló: %s", exc)
