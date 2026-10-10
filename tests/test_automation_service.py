from datetime import UTC, datetime

from services.assistant_store import AssistantStore
from services.automation_service import next_run_for


def test_natural_intervals_are_supported():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert next_run_for("cada 30 minutos", now) == datetime(2026, 10, 9, 12, 30, tzinfo=UTC)
    assert next_run_for("cada 2 horas", now) == datetime(2026, 10, 9, 14, 0, tzinfo=UTC)


def test_daily_schedule_rolls_to_tomorrow_after_time():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert next_run_for("cada día 08:00", now) == datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


def test_new_automation_waits_for_first_run(tmp_path):
    store = AssistantStore(str(tmp_path / "state.db"))
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    first_run = next_run_for("cada 30m", now)
    item_id = store.add_automation(7, "revisa Discord", "cada 30m", first_run)
    assert store.due_automations(now) == []
    assert store.due_automations(first_run)[0]["id"] == item_id
