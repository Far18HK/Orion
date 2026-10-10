from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from services.reasoning_harness import HarnessPolicy, ReasoningHarness


@pytest.mark.asyncio
async def test_harness_returns_direct_response():
    message = SimpleNamespace(content="hola", tool_calls=[])

    async def complete(model, messages, tools=None):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    harness = ReasoningHarness(complete, lambda messages, tools: None, lambda call, ctx: None)
    result = await harness.run([{"role": "user", "content": "hola"}], [], "test", object())
    assert result.content == "hola"


@pytest.mark.asyncio
async def test_harness_executes_tool_then_finishes():
    calls = [SimpleNamespace(id="1", function=SimpleNamespace(name="ping", arguments="{}"))]
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=calls))]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="listo", tool_calls=[]))]
        ),
    ]
    executed = []

    async def complete(model, messages, tools=None):
        return responses.pop(0)

    async def run_call(call, ctx):
        executed.append(call.function.name)
        return {"role": "tool", "tool_call_id": call.id, "content": "pong"}

    harness = ReasoningHarness(
        complete, lambda messages, tools: None, run_call, HarnessPolicy(max_tool_rounds=2)
    )
    result = await harness.run(
        [{"role": "user", "content": "ping"}], [{"name": "ping"}], "test", object()
    )
    assert result.content == "listo"
    assert executed == ["ping"]


# ---------------------------------------------------------------- aprobación y reanudación

def _tool_response(name: str, call_id: str = "c1"):
    calls = [SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments="{}"))]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=calls))]
    )


def _final_response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=[]))]
    )


def _harness(responses, executed, saver, seen=None, fail_once=None):
    async def complete(model, messages, tools=None):
        if seen is not None:
            seen.append([dict(m) for m in messages])
        return responses.pop(0)

    async def run_call(call, ctx):
        if fail_once is not None and fail_once.pop(call.id, False):
            raise RuntimeError("caída simulada")
        executed.append(call.function.name)
        return {"role": "tool", "tool_call_id": call.id, "content": "ok"}

    return ReasoningHarness(
        complete,
        lambda messages, tools: None,
        run_call,
        HarnessPolicy(max_tool_rounds=3),
        checkpointer=saver,
        needs_approval=lambda name, ctx: name == "peligro",
    )


USER = [{"role": "user", "content": "haz algo"}]


@pytest.mark.asyncio
async def test_sensitive_tool_pauses_until_approved():
    executed: list[str] = []
    harness = _harness([_tool_response("peligro"), _final_response("hecho")], executed, InMemorySaver())
    paused = await harness.run(list(USER), [{"name": "x"}], "m", object(), thread_id="t1")
    assert paused.interrupted
    assert paused.pending[0]["name"] == "peligro"
    assert executed == []  # nada se ejecutó mientras esperaba

    done = await harness.resume("t1", [{"name": "x"}], object(), approved=True)
    assert not done.interrupted
    assert done.content == "hecho"
    assert executed == ["peligro"]


@pytest.mark.asyncio
async def test_rejection_skips_tool_and_tells_the_model():
    executed: list[str] = []
    seen: list[list[dict]] = []
    harness = _harness(
        [_tool_response("peligro"), _final_response("no lo hice")], executed, InMemorySaver(), seen
    )
    await harness.run(list(USER), [{"name": "x"}], "m", object(), thread_id="t2")
    done = await harness.resume("t2", [{"name": "x"}], object(), approved=False)
    assert executed == []
    assert done.content == "no lo hice"
    tool_message = seen[-1][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert "no aprobó" in tool_message["content"]


@pytest.mark.asyncio
async def test_unsupervised_turn_denies_sensitive_tools_without_pausing():
    executed: list[str] = []
    harness = _harness(
        [_tool_response("peligro"), _final_response("sin permiso")], executed, InMemorySaver()
    )
    result = await harness.run(
        list(USER), [{"name": "x"}], "m", object(), thread_id="t3", interactive=False
    )
    assert not result.interrupted
    assert result.content == "sin permiso"
    assert executed == []


@pytest.mark.asyncio
async def test_non_sensitive_tool_runs_without_pausing():
    executed: list[str] = []
    harness = _harness([_tool_response("ping"), _final_response("pong")], executed, InMemorySaver())
    result = await harness.run(list(USER), [{"name": "x"}], "m", object(), thread_id="t4")
    assert not result.interrupted
    assert executed == ["ping"]


@pytest.mark.asyncio
async def test_pending_approval_survives_a_restart():
    saver = InMemorySaver()  # mismo almacén, instancia de harness NUEVA = proceso reiniciado
    executed: list[str] = []
    first = _harness([_tool_response("peligro")], executed, saver)
    paused = await first.run(list(USER), [{"name": "x"}], "m", object(), thread_id="t5")
    assert paused.interrupted

    second = _harness([_final_response("listo tras reinicio")], executed, saver)
    done = await second.resume("t5", [{"name": "x"}], object(), approved=True)
    assert done.content == "listo tras reinicio"
    assert executed == ["peligro"]


@pytest.mark.asyncio
async def test_crashed_turn_resumes_from_last_completed_step():
    saver = InMemorySaver()
    executed: list[str] = []
    # La primera vez la herramienta "se cae"; el checkpoint conserva el paso del modelo.
    harness = _harness(
        [_tool_response("ping"), _final_response("terminé")],
        executed,
        saver,
        fail_once={"c1": True},
    )
    with pytest.raises(RuntimeError):
        await harness.run(list(USER), [{"name": "x"}], "m", object(), thread_id="t6")
    assert executed == []

    done = await harness.resume("t6", [{"name": "x"}], object(), approved=None)
    assert done.content == "terminé"
    assert executed == ["ping"]  # la llamada conserva su id: la idempotencia sí aplica
