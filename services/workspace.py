"""Espacio de archivos por usuario: sin traversal, sin ejecución implícita y con límites."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import zipfile
from pathlib import Path

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_READ_CHARS = 40_000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".yaml", ".yml", ".ini",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".sql", ".toml", ".css", ".sh",
}


class WorkspaceError(Exception):
    """Error de acceso o formato del espacio de trabajo."""


def user_root(root: str | Path, user_id: int, platform: str = "telegram") -> Path:
    safe_platform = "discord" if platform == "discord" else "telegram"
    path = Path(root).resolve() / safe_platform / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_path(root: str | Path, user_id: int, requested: str, platform: str = "telegram") -> Path:
    base = user_root(root, user_id, platform)
    raw = str(requested or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "\x00" in raw:
        raise WorkspaceError("La ruta debe ser relativa al espacio de trabajo.")
    path = (base / raw).resolve()
    if path != base and base not in path.parents:
        raise WorkspaceError("La ruta sale del espacio de trabajo y fue bloqueada.")
    return path


def save_attachment(root: str | Path, user_id: int, filename: str, data: bytes, platform: str = "telegram") -> Path:
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError("El archivo supera el límite de 50 MB.")
    name = Path(filename or "archivo").name or "archivo"
    target = safe_path(root, user_id, f"inbox/{name}", platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _describe_zip(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            rows = [f"ZIP con {len(archive.infolist())} entradas:"]
            for item in archive.infolist()[:100]:
                rows.append(f"- {item.filename} ({item.file_size} bytes)")
            return "\n".join(rows)
    except zipfile.BadZipFile as exc:
        raise WorkspaceError("El archivo .zip está dañado o no es un ZIP válido.") from exc


def describe_file(path: Path) -> str:
    stat = path.stat()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    lines = [f"Archivo: {path.name}", f"Tamaño: {stat.st_size} bytes", f"Tipo estimado: {mime}", f"SHA-256: {digest}"]
    if path.suffix.lower() == ".zip":
        lines.append(_describe_zip(path))
    elif path.suffix.lower() in TEXT_EXTENSIONS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines.append("Contenido:\n" + text[:MAX_READ_CHARS])
            if len(text) > MAX_READ_CHARS:
                lines.append("[contenido recortado]")
        except OSError as exc:
            raise WorkspaceError(f"No pude leer el archivo: {exc}") from exc
    else:
        lines.append("Es un archivo binario; puedo conservarlo, copiarlo, empaquetarlo o transformarlo con una herramienta especializada.")
    return "\n".join(lines)


def list_files(root: str | Path, user_id: int, platform: str = "telegram") -> str:
    base = user_root(root, user_id, platform)
    files = sorted(p for p in base.rglob("*") if p.is_file())
    if not files:
        return "El espacio de trabajo está vacío."
    lines = [f"Archivos disponibles ({len(files)}):"]
    for path in files[:200]:
        lines.append(f"- {path.relative_to(base)} ({path.stat().st_size} bytes)")
    return "\n".join(lines)


def write_text(root: str | Path, user_id: int, filename: str, content: str, platform: str = "telegram") -> Path:
    data = str(content).encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError("El archivo generado supera el límite de 50 MB.")
    target = safe_path(root, user_id, filename, platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def write_base64(root: str | Path, user_id: int, filename: str, encoded: str, platform: str = "telegram") -> Path:
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise WorkspaceError("El contenido no es Base64 válido.") from exc
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError("El archivo generado supera el límite de 50 MB.")
    target = safe_path(root, user_id, filename, platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def make_zip(root: str | Path, user_id: int, output: str, files: list[str], platform: str = "telegram") -> Path:
    target = safe_path(root, user_id, output, platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    base = user_root(root, user_id, platform)
    unique: list[Path] = []
    for requested in files[:100]:
        path = safe_path(root, user_id, requested, platform)
        if not path.is_file():
            raise WorkspaceError(f"No existe el archivo: {requested}")
        unique.append(path)
    if not unique:
        raise WorkspaceError("Indica al menos un archivo para comprimir.")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in unique:
            archive.write(path, path.relative_to(base).as_posix())
    return target
