from pathlib import Path

import pytest

from services.workspace import WorkspaceError, describe_file, list_files, make_zip, save_attachment, safe_path, write_text


def test_workspace_isolates_users_and_blocks_traversal(tmp_path: Path):
    saved = save_attachment(tmp_path, 10, "demo.py", b"print('ok')")
    assert saved.read_bytes() == b"print('ok')"
    assert "demo.py" in list_files(tmp_path, 10)
    assert "demo.py" not in list_files(tmp_path, 11)
    with pytest.raises(WorkspaceError):
        safe_path(tmp_path, 10, "../../secreto.txt")


def test_workspace_reads_text_and_creates_zip(tmp_path: Path):
    write_text(tmp_path, 10, "src/a.py", "print('hola')")
    write_text(tmp_path, 10, "README.md", "# Orion")
    assert "print('hola')" in describe_file(safe_path(tmp_path, 10, "src/a.py"))
    archive = make_zip(tmp_path, 10, "entrega.zip", ["src/a.py", "README.md"])
    assert archive.exists() and archive.stat().st_size > 0
