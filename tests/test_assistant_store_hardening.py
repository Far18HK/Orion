from services.assistant_store import AssistantStore


def test_checkpoint_operation_and_project_are_persistent(tmp_path):
    store = AssistantStore(str(tmp_path / "orion.db"))
    store.save_checkpoint(7, "telegram", "running", {"step": 1})
    assert store.pending_checkpoint(7, "telegram")[0] == "running"
    assert store.begin_operation(7, "telegram", "op-1", "send") is None
    store.finish_operation(7, "telegram", "op-1", "sent")
    assert store.begin_operation(7, "telegram", "op-1", "send") == "sent"
    store.project(7, "Orion", "bot personal")
    assert store.projects(7)[0][1:] == ("Orion", "bot personal")
    store.clear_checkpoint(7, "telegram")
    assert store.pending_checkpoint(7, "telegram") is None


def test_checkpoint_payload_roundtrip_and_phase(tmp_path):
    store = AssistantStore(str(tmp_path / "orion.db"))
    assert store.get_checkpoint(7, "telegram") is None
    store.save_checkpoint(7, "telegram", "awaiting_approval", {"thread_id": "t1", "calls": [1]})
    phase, payload, _ = store.get_checkpoint(7, "telegram")
    assert phase == "awaiting_approval"
    assert payload == {"thread_id": "t1", "calls": [1]}
    # Otra plataforma o usuario no ve el mismo checkpoint
    assert store.get_checkpoint(7, "discord") is None
    assert store.get_checkpoint(8, "telegram") is None
    store.save_checkpoint(7, "telegram", "running", {"thread_id": "t1"})
    assert store.get_checkpoint(7, "telegram")[0] == "running"
    store.clear_checkpoint(7, "telegram")
    assert store.get_checkpoint(7, "telegram") is None


def test_agent_runs_and_steps_are_persistent(tmp_path):
    store = AssistantStore(str(tmp_path / "orion.db"))
    run_id = store.create_agent_run(
        7, "telegram", "preparar informe", {"max_tool_calls": 12}
    )
    step_id = store.add_agent_step(run_id, 1, "Buscar datos", "debe haber fuentes")
    assert store.agent_runs(7)[0]["status"] == "running"
    assert store.agent_steps(run_id)[0]["title"] == "Buscar datos"
    assert store.update_agent_step(step_id, "done", evidence="3 fuentes")
    assert store.update_agent_run(run_id, "done", result="Informe listo")
    run = store.agent_runs(7)[0]
    assert run["status"] == "done"
    assert run["result"] == "Informe listo"
