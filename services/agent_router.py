"""Dispatcher interno: selecciona el especialista sin crear bots visibles adicionales."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str
    allowed_tools: frozenset[str] | None
    instructions: str


GENERAL = AgentProfile(
    "general",
    "Coordinador general de Orion.",
    None,
    "Coordina la petición y usa especialistas implícitos solo cuando corresponda.",
)

PROFILES = {
    "general": GENERAL,
    "files": AgentProfile(
        "files", "Archivos y documentos.",
        frozenset({"list_files", "read_file", "write_file", "write_binary_file", "zip_files", "plan_task", "verify_step", "calculate", "read_webpage"}),
        "Trabaja con archivos. No ejecutes código ni binarios; describe límites y entrega archivos creados.",
    ),
    "discord": AgentProfile(
        "discord", "Discord y moderación.",
        frozenset({"read_discord_channel", "write_discord_channel", "create_discord_event", "timeout_discord_member", "delete_discord_message", "plan_task", "verify_step"}),
        "Actúa solo sobre Discord autorizado. Nunca afirmes que publicaste o moderaste hasta tener confirmación de la herramienta.",
    ),
    "coding": AgentProfile(
        "coding", "Programación y repositorios.",
        frozenset({"github_list_repos", "github_repo_overview", "github_list_files", "github_read_file", "github_search_code", "github_recent_commits", "github_issues", "list_files", "read_file", "write_file", "zip_files", "openhands_file_editor", "plan_task", "verify_step", "team_reason"}),
        "Analiza código con evidencia. No ejecutes scripts ni cambies repositorios externos sin una herramienta explícita y aprobación.",
    ),
    "research": AgentProfile(
        "research", "Investigación y fuentes.",
        frozenset({"web_search", "read_webpage", "get_current_time", "calculate", "team_reason", "plan_task", "verify_step"}),
        "Investiga, contrasta fuentes y separa hechos de inferencias.",
    ),
    "media": AgentProfile(
        "media", "Multimedia.",
        frozenset({"list_files", "read_file", "write_file", "write_binary_file", "zip_files", "plan_task", "verify_step"}),
        "Trabaja con audio, vídeo e imágenes mediante capacidades disponibles; no inventes contenido binario.",
    ),
}


def route(text: str) -> AgentProfile:
    value = text.casefold()
    scores = {name: 0 for name in PROFILES if name != "general"}
    keywords = {
        "files": ("archivo", "zip", "pdf", "docx", "mp3", "mp4", "crear", "carpeta", "código", "json"),
        "discord": ("discord", "anuncio", "evento", "timeout", "moderador", "canal", "mensaje"),
        "coding": ("python", "programa", "programar", "bug", "error", "repositorio", "github", "código", "script"),
        "research": ("investiga", "investigar", "fuentes", "noticias", "actual", "compara", "documentación"),
        "media": ("audio", "vídeo", "video", "imagen", "transcribe", "subtítulos", "foto"),
    }
    for name, words in keywords.items():
        scores[name] = sum(value.count(word) for word in words)
    best, score = max(scores.items(), key=lambda item: item[1])
    return PROFILES[best] if score else GENERAL


def filter_specs(specs: list[dict], profile: AgentProfile) -> list[dict]:
    if profile.allowed_tools is None:
        return specs
    return [spec for spec in specs if spec.get("function", {}).get("name") in profile.allowed_tools]
