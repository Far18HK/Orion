import json

from config import resolve_checkpoint_path
from services.tools import TOOLS, describe_call


def test_describe_call_is_readable_for_sensitive_tools():
    text = describe_call(
        "write_discord_channel", json.dumps({"channel_id": "123", "text": "hola equipo"})
    )
    assert "123" in text and "hola equipo" in text
    auto = describe_call(
        "create_automation", json.dumps({"schedule": "cada 2h", "instruction": "revisa Discord"})
    )
    assert "cada 2h" in auto and "revisa Discord" in auto


def test_describe_call_survives_bad_arguments():
    assert describe_call("write_discord_channel", "no es json")
    assert describe_call("otra_cosa", "[1, 2]") == "otra_cosa"


def test_default_approval_tools_exist():
    names = {tool.name for tool in TOOLS}
    assert {"write_discord_channel", "create_automation"} <= names
    assert "confirm_approval" not in names  # lo reemplaza interrupt() en el harness


def test_checkpoint_db_lives_next_to_main_db():
    assert resolve_checkpoint_path("/data/reminders.db") == "/data/orion_checkpoints.db"
    assert resolve_checkpoint_path("reminders.db") == "orion_checkpoints.db"
