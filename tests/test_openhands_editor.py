from pathlib import Path

import pytest

from services.agent_router import PROFILES
from services.openhands_editor import run_openhands_editor
from services.workspace import WorkspaceError, safe_path


def test_openhands_editor_rejects_invalid_commands_and_traversal(tmp_path: Path):
    assert "command debe ser uno" in run_openhands_editor(
        str(tmp_path), 10, "telegram", {"command": "delete", "path": "a.py"}
    )
    with pytest.raises(WorkspaceError):
        safe_path(tmp_path, 10, "../../secreto.py")


def test_openhands_editor_reports_optional_dependency_when_unavailable(tmp_path: Path):
    result = run_openhands_editor(
        str(tmp_path), 10, "telegram", {"command": "view", "path": "README.md"}
    )
    # En Python 3.11 del entorno de pruebas la dependencia no se instala porque
    # openhands-aci publica como requisito Python >=3.12.
    assert "openhands-aci" in result or "README.md" in result


def test_coding_profile_exposes_openhands_editor():
    assert "openhands_file_editor" in PROFILES["coding"].allowed_tools
