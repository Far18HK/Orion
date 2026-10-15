"""Adaptador seguro para el editor de archivos de OpenHands ACI.

OpenHands ACI expone un editor con comandos ``view``, ``create``,
``str_replace``, ``insert`` y ``undo_edit``. Orion conserva el control de
rutas, cuotas y aprobaciones en su propia capa antes de delegar la edición.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from services.workspace import (
    MAX_FILE_BYTES,
    WorkspaceError,
    check_quota,
    safe_path,
    user_root,
    workspace_usage,
    WORKSPACE_QUOTA_BYTES,
)

COMMANDS = frozenset({"view", "create", "str_replace", "insert", "undo_edit"})
EDITOR_MAX_FILE_BYTES = 10 * 1024 * 1024  # Límite del editor de OpenHands ACI.


class OpenHandsEditorUnavailable(RuntimeError):
    """El paquete opcional no está instalado o no puede importarse."""


def _load_editor_class():
    try:
        from openhands_aci.editor import OHEditor
    except Exception as exc:  # noqa: BLE001 - dependencia opcional
        raise OpenHandsEditorUnavailable(
            "openhands-aci no está disponible. Orion requiere Python 3.12+ "
            "y la dependencia openhands-aci instalada."
        ) from exc
    return OHEditor


@lru_cache(maxsize=128)
def _editor_for_workspace(workspace: str):
    """Conserva la instancia para que ``undo_edit`` mantenga su historial."""
    editor_class = _load_editor_class()
    return editor_class(max_file_size_mb=10, workspace_root=workspace)


def _requested_path(root: str, user_id: int, requested: str, platform: str) -> Path:
    path = safe_path(root, user_id, requested, platform)
    base = user_root(root, user_id, platform)
    # Defensa adicional: el editor recibe rutas absolutas ya confinadas.
    if path != base and base not in path.parents:
        raise WorkspaceError("La ruta sale del espacio de trabajo y fue bloqueada.")
    return path


def _content_size(args: dict, path: Path, command: str) -> int | None:
    """Calcula el tamaño final antes de una operación que modifica el archivo."""
    if command == "create":
        return len(str(args.get("file_text", "")).encode("utf-8"))
    if command == "str_replace" and path.is_file():
        old = str(args.get("old_str", ""))
        new = str(args.get("new_str", ""))
        current = path.read_text(encoding="utf-8")
        occurrences = current.count(old)
        if occurrences != 1:
            return None  # El editor devolverá el error exacto de unicidad.
        return len((current.replace(old, new, 1)).encode("utf-8"))
    if command == "insert" and path.is_file():
        current = path.read_text(encoding="utf-8")
        new = str(args.get("new_str", ""))
        return len((current + new + "\n").encode("utf-8"))
    return None


def _check_mutation_limits(root: str, user_id: int, platform: str, path: Path, command: str, args: dict) -> None:
    final_size = _content_size(args, path, command)
    if final_size is None:
        return
    if final_size > EDITOR_MAX_FILE_BYTES:
        raise WorkspaceError("OpenHands ACI solo permite editar archivos de hasta 10 MB.")
    if final_size > MAX_FILE_BYTES:
        raise WorkspaceError("El archivo supera el límite de 50 MB.")
    current_size = path.stat().st_size if path.is_file() else 0
    check_quota(root, user_id, max(0, final_size - current_size), platform)


def run_openhands_editor(
    root: str,
    user_id: int,
    platform: str,
    args: dict,
) -> str:
    """Ejecuta una operación ACI dentro del workspace privado del usuario."""
    command = str(args.get("command", "")).strip()
    if command not in COMMANDS:
        return "Error: command debe ser uno de: view, create, str_replace, insert, undo_edit."
    try:
        path = _requested_path(root, user_id, str(args.get("path", "")), platform)
        if command != "view" and command != "create" and not path.is_file():
            return "Error: ese archivo no existe. Usa command=view para inspeccionarlo."
        if command == "create" and path.exists() and path.is_dir():
            return "Error: la ruta indicada es un directorio."
        if command == "create":
            path.parent.mkdir(parents=True, exist_ok=True)
        if command in {"create", "str_replace", "insert"}:
            _check_mutation_limits(root, user_id, platform, path, command, args)
        editor = _editor_for_workspace(str(user_root(root, user_id, platform)))
        result = editor(
            command=command,
            path=str(path),
            file_text=args.get("file_text"),
            view_range=args.get("view_range"),
            old_str=args.get("old_str"),
            new_str=args.get("new_str"),
            insert_line=args.get("insert_line"),
            enable_linting=bool(args.get("enable_linting", False)),
        )
        output = getattr(result, "output", None) or getattr(result, "error", None) or str(result)
        if command == "create" and path.is_file():
            output = f"ARCHIVO_CREADO:{path}\n{output}"
        if command in {"create", "str_replace", "insert", "undo_edit"} and path.exists():
            # El editor escribe directamente; esta comprobación evita superar la cuota
            # incluso si su operación concreta no pudo calcularse por adelantado.
            used = workspace_usage(root, user_id, platform)
            if used > WORKSPACE_QUOTA_BYTES:
                raise WorkspaceError("La operación superó la cuota de 200 MB del workspace.")
        return output
    except OpenHandsEditorUnavailable as exc:
        return f"Error: {exc}"
    except (WorkspaceError, OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"
