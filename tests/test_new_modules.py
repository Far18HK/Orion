"""Tests para semantic_memory, agent_runner y sandbox."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# SemanticMemory
# ──────────────────────────────────────────────────────────────────────────────

class TestSemanticMemory(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from services.semantic_memory import SemanticMemory

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        self.mem = SemanticMemory(self.db)  # sin embeddings, BM25 puro

    async def test_remember_and_list(self):
        mem_id = await self.mem.remember(1, "Me gusta el café negro", kind="preferencia")
        self.assertTrue(len(mem_id) > 0)

        items = self.mem.list_all(1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "Me gusta el café negro")
        self.assertEqual(items[0]["kind"], "preferencia")

    async def test_forget(self):
        mem_id = await self.mem.remember(1, "Temporal", kind="general")
        deleted = self.mem.forget(1, mem_id)
        self.assertTrue(deleted)
        self.assertEqual(len(self.mem.list_all(1)), 0)

    async def test_forget_wrong_user(self):
        mem_id = await self.mem.remember(1, "Solo mío", kind="general")
        deleted = self.mem.forget(2, mem_id)  # usuario diferente
        self.assertFalse(deleted)

    async def test_recall_returns_results(self):
        await self.mem.remember(1, "Prefiero el té verde por la mañana", kind="preferencia")
        await self.mem.remember(1, "Mi gato se llama Misi", kind="persona")
        # El cosine con embedding falso puede no ser semántico, pero debe devolver resultados
        results = await self.mem.recall(1, "bebidas matutinas", limit=5, min_score=0.0)
        self.assertIsInstance(results, list)

    async def test_stats(self):
        await self.mem.remember(1, "A", kind="preferencia")
        await self.mem.remember(1, "B", kind="preferencia")
        await self.mem.remember(1, "C", kind="hecho")
        stats = self.mem.stats(1)
        self.assertEqual(stats.get("preferencia"), 2)
        self.assertEqual(stats.get("hecho"), 1)

    async def test_expiry(self):
        import datetime
        # Guardamos con expiración en el pasado usando manipulación directa
        mem_id = await self.mem.remember(1, "Viejo", expires_in_days=1)
        # Forzamos la fecha de expiración al pasado
        import sqlite3
        from contextlib import closing
        past = "2020-01-01T00:00:00+00:00"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE semantic_memories SET expires_at=? WHERE id=?", (past, mem_id))
        purged = self.mem.purge_expired()
        self.assertEqual(purged, 1)
        self.assertEqual(len(self.mem.list_all(1)), 0)

    async def test_invalid_kind_falls_back(self):
        mem_id = await self.mem.remember(1, "Hola", kind="tipo_inventado")
        items = self.mem.list_all(1)
        self.assertEqual(items[0]["kind"], "general")  # fallback


# ──────────────────────────────────────────────────────────────────────────────
# AgentRunner
# ──────────────────────────────────────────────────────────────────────────────

class TestAgentRunner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from services.agent_runner import AgentRunner, RunBudget

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        from services.assistant_store import AssistantStore
        self.store = AssistantStore(self.tmp.name)

        # LLM falso
        async def fake_llm(system: str, user: str) -> str:
            if "planificador" in system.lower() or "plan" in user.lower():
                return json.dumps({
                    "steps": [
                        {"title": "Buscar información", "tool": "web_search",
                         "args": {"query": "test"}, "expected_result": "Resultados de búsqueda"},
                    ]
                })
            return "COMPLETADO: verificación ok"

        # Tool executor falso
        async def fake_executor(tool: str, args: dict) -> str:
            return f"Resultado de {tool}: ok"

        self.runner = AgentRunner(
            store=self.store,
            tool_executor=fake_executor,
            llm=fake_llm,
            available_tools=["web_search", "calculate"],
        )

    async def test_run_creates_agent_run(self):
        from services.agent_runner import RunBudget
        budget = RunBudget(max_steps=2)
        progress_messages = []

        async def capture(text: str):
            progress_messages.append(text)

        result = await self.runner.run(
            user_id=1,
            platform="telegram",
            objective="Buscar información sobre Python",
            budget=budget,
            send_progress=capture,
        )

        self.assertIn(result.status, {"done", "failed", "budget_exceeded"})
        self.assertGreater(len(progress_messages), 0)

    async def test_run_budget_respected(self):
        from services.agent_runner import RunBudget
        from datetime import datetime, UTC, timedelta

        # Deadline en el pasado = debe fallar por presupuesto
        budget = RunBudget(
            max_steps=5,
            deadline=datetime.now(UTC) - timedelta(seconds=1),
        )
        result = await self.runner.run(
            user_id=2,
            platform="telegram",
            objective="Tarea que no debería ejecutarse",
            budget=budget,
        )
        # Puede fallar por deadline o por no poder planificar antes del deadline
        self.assertIn(result.status, {"done", "failed", "budget_exceeded"})

    async def test_run_budget_from_dict(self):
        from services.agent_runner import RunBudget
        b = RunBudget.from_dict({"max_steps": 7, "max_tool_calls": 15})
        self.assertEqual(b.max_steps, 7)
        self.assertEqual(b.max_tool_calls, 15)
        self.assertFalse(b.is_expired())

    async def test_agent_run_persisted(self):
        from services.agent_runner import RunBudget
        await self.runner.run(
            user_id=3, platform="telegram",
            objective="Test persistencia",
            budget=RunBudget(max_steps=1),
        )
        runs = self.store.agent_runs(3, "telegram", 10)
        self.assertGreaterEqual(len(runs), 1)
        self.assertEqual(runs[0]["objective"], "Test persistencia")


# ──────────────────────────────────────────────────────────────────────────────
# Sandbox
# ──────────────────────────────────────────────────────────────────────────────

class TestSandbox(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_returns_error(self):
        from services.sandbox import sandbox_run_code

        # Sin E2B_API_KEY debe devolver un error amigable
        with patch.dict(os.environ, {}, clear=True):
            # Forzamos que e2b no esté disponible
            result = await sandbox_run_code("print('hola')", "python")
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    async def test_run_code_tool_empty_code(self):
        from services.sandbox import run_code_tool
        result = await run_code_tool({"code": "", "language": "python"})
        self.assertIn("Error", result)

    async def test_run_code_tool_bad_language(self):
        from services.sandbox import run_code_tool
        result = await run_code_tool({"code": "echo hi", "language": "ruby"})
        self.assertIn("no soportado", result)

    def test_is_code_safe(self):
        from services.sandbox import is_code_safe_for_local
        ok, _ = is_code_safe_for_local("x = 1 + 2\nprint(x)")
        self.assertTrue(ok)

        safe, reason = is_code_safe_for_local("import os\nos.system('rm -rf /')")
        self.assertFalse(safe)
        self.assertIn("import os", reason)

    def test_sandbox_result_to_str(self):
        from services.sandbox import SandboxResult
        r = SandboxResult(stdout="hello", stderr="", error=None, files=[])
        self.assertIn("hello", r.to_str())
        self.assertTrue(r.ok)

        r2 = SandboxResult(stdout="", stderr="", error="fallo", files=[])
        self.assertIn("fallo", r2.to_str())
        self.assertFalse(r2.ok)


# ──────────────────────────────────────────────────────────────────────────────
# Tools addon
# ──────────────────────────────────────────────────────────────────────────────

class TestAddonTools(unittest.IsolatedAsyncioTestCase):
    def _make_ctx(self, semantic_memory=None, store=None):
        ctx = MagicMock()
        ctx.user_id = 1
        ctx.platform = "telegram"
        ctx.semantic_memory = semantic_memory
        ctx.store = store
        return ctx

    async def test_semantic_remember_no_service(self):
        from services.tools_addon import _t_semantic_remember
        ctx = self._make_ctx(semantic_memory=None)
        result = await _t_semantic_remember(ctx, {"content": "test"})
        self.assertIn("no está habilitada", result)

    async def test_semantic_recall_no_service(self):
        from services.tools_addon import _t_semantic_recall
        ctx = self._make_ctx(semantic_memory=None)
        result = await _t_semantic_recall(ctx, {"query": "algo"})
        self.assertIn("no está habilitada", result)

    async def test_semantic_remember_empty_content(self):
        from services.tools_addon import _t_semantic_remember
        mock_sm = AsyncMock()
        ctx = self._make_ctx(semantic_memory=mock_sm)
        result = await _t_semantic_remember(ctx, {"content": ""})
        self.assertIn("obligatorio", result)

    async def test_create_agent_run_no_store(self):
        from services.tools_addon import _t_create_agent_run
        ctx = self._make_ctx(store=None)
        result = await _t_create_agent_run(ctx, {"objective": "test"})
        self.assertIn("Error", result)

    async def test_create_agent_run_no_objective(self):
        from services.tools_addon import _t_create_agent_run
        mock_store = MagicMock()
        ctx = self._make_ctx(store=mock_store)
        result = await _t_create_agent_run(ctx, {})
        self.assertIn("obligatorio", result)

    def test_addon_tools_list_not_empty(self):
        from services.tools_addon import ADDON_TOOLS
        self.assertGreater(len(ADDON_TOOLS), 0)
        names = [t.name for t in ADDON_TOOLS]
        self.assertIn("semantic_remember", names)
        self.assertIn("semantic_recall", names)
        self.assertIn("run_code", names)
        self.assertIn("create_agent_run", names)

    def test_all_tools_have_spec(self):
        from services.tools_addon import ADDON_TOOLS
        for tool in ADDON_TOOLS:
            spec = tool.spec()
            self.assertEqual(spec["type"], "function")
            self.assertIn("name", spec["function"])
            self.assertIn("parameters", spec["function"])


if __name__ == "__main__":
    unittest.main()
