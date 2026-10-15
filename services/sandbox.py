"""Sandbox de código seguro para Orion via E2B.

Permite al agente ejecutar código Python (y bash) en un entorno aislado
sin acceso a secretos, filesystem del servidor ni red interna.

Documentación E2B: https://e2b.dev/docs

Instalación:
    pip install e2b-code-interpreter

Variable de entorno requerida:
    E2B_API_KEY=tu_clave

Uso como herramienta del agente:
    result = await sandbox_run_code(code="print(1+1)", language="python")
    result = await sandbox_run_code(code="ls -la", language="bash")

Uso en el AgentRunner para la herramienta "run_code":
    evidence = await tool_executor("run_code", {"code": "...", "language": "python"})
"""
from __future__ import annotations

import asyncio
import logging
import os
import textwrap
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tiempo máximo de ejecución por fragmento de código (segundos)
SANDBOX_TIMEOUT = 30

# Límite de salida que devolvemos al modelo (caracteres)
MAX_OUTPUT = 4000


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    error: str | None
    files: list[str]        # rutas de archivos generados dentro del sandbox
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out

    def to_str(self) -> str:
        """Texto compacto listo para devolverle al modelo."""
        parts: list[str] = []
        if self.timed_out:
            parts.append(f"⏱ Timeout tras {SANDBOX_TIMEOUT}s")
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout[:MAX_OUTPUT]}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr[:1000]}")
        if self.error:
            parts.append(f"error: {self.error[:500]}")
        if self.files:
            parts.append(f"archivos generados: {', '.join(self.files)}")
        return "\n".join(parts) or "(sin salida)"


def _e2b_available() -> bool:
    """True si E2B está instalado y hay API key configurada."""
    try:
        import e2b_code_interpreter  # noqa: F401
        return bool(os.getenv("E2B_API_KEY"))
    except ImportError:
        return False


async def sandbox_run_code(
    code: str,
    language: str = "python",
    install_packages: list[str] | None = None,
    timeout: int = SANDBOX_TIMEOUT,
) -> SandboxResult:
    """Ejecuta código en un sandbox E2B aislado.

    Args:
        code: Código fuente a ejecutar.
        language: 'python' o 'bash'.
        install_packages: Paquetes pip a instalar antes de ejecutar.
        timeout: Tiempo máximo en segundos.

    Returns:
        SandboxResult con stdout, stderr y archivos generados.
    """
    if not _e2b_available():
        return SandboxResult(
            stdout="",
            stderr="",
            error=(
                "E2B no está disponible. Instala `e2b-code-interpreter` y "
                "configura E2B_API_KEY para habilitar la ejecución de código."
            ),
            files=[],
        )

    try:
        return await asyncio.wait_for(
            _run_in_e2b(code, language, install_packages or []),
            timeout=timeout,
        )
    except TimeoutError:
        return SandboxResult(stdout="", stderr="", error=None, files=[], timed_out=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("Sandbox error: %s", exc)
        return SandboxResult(stdout="", stderr="", error=str(exc), files=[])


async def _run_in_e2b(
    code: str,
    language: str,
    packages: list[str],
) -> SandboxResult:
    """Lógica real de E2B (ejecutada en un thread para no bloquear el event loop)."""

    def _sync() -> SandboxResult:
        from e2b_code_interpreter import Sandbox  # type: ignore[import]

        with Sandbox(api_key=os.environ["E2B_API_KEY"]) as sbx:
            # Instalar dependencias si se pidieron
            if packages:
                pkg_str = " ".join(packages)
                sbx.commands.run(f"pip install -q {pkg_str}", timeout=60)

            if language == "bash":
                result = sbx.commands.run(code, timeout=SANDBOX_TIMEOUT)
                return SandboxResult(
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    error=None if result.exit_code == 0 else f"Exit code {result.exit_code}",
                    files=[],
                )
            else:
                # Python: usamos el kernel de IPython para mejor introspección
                execution = sbx.run_code(code)

                stdout_parts = []
                stderr_parts = []
                files = []

                for output in execution.logs.stdout:
                    stdout_parts.append(str(output))
                for output in execution.logs.stderr:
                    stderr_parts.append(str(output))

                # Archivos guardados en /home/user o /tmp dentro del sandbox
                # E2B permite descargarlos con sbx.files.read()
                error = None
                if execution.error:
                    error = f"{execution.error.name}: {execution.error.value}"

                return SandboxResult(
                    stdout="\n".join(stdout_parts),
                    stderr="\n".join(stderr_parts),
                    error=error,
                    files=files,
                )

    return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------- herramienta de alto nivel

async def run_code_tool(args: dict) -> str:
    """Handler listo para registrar como Tool en tools.py.

    Args esperados:
        code (str): Código a ejecutar.
        language (str): 'python' o 'bash'. Default: 'python'.
        packages (list[str]): Paquetes pip a instalar. Default: [].

    Returns:
        Texto con el resultado, listo para el modelo.
    """
    code = str(args.get("code", "")).strip()
    if not code:
        return "Error: no se proporcionó código."

    language = str(args.get("language", "python")).lower()
    if language not in {"python", "bash"}:
        return f"Error: lenguaje '{language}' no soportado. Usa 'python' o 'bash'."

    packages = args.get("packages", [])
    if isinstance(packages, str):
        packages = [p.strip() for p in packages.split(",") if p.strip()]

    result = await sandbox_run_code(code, language, packages)
    return result.to_str()


# ---------------------------------------------------------------- validación de código (sin E2B)

DANGEROUS_PATTERNS = [
    "import os", "import sys", "import subprocess", "import shutil",
    "__import__", "eval(", "exec(", "open(", "os.system", "os.popen",
    "subprocess.run", "subprocess.call", "socket.", "urllib", "requests",
]


def is_code_safe_for_local(code: str) -> tuple[bool, str]:
    """Revisión rápida de seguridad para código que se ejecutaría localmente (sin sandbox).

    No es un filtro perfecto. Para producción usa siempre el sandbox E2B.
    """
    code_lower = code.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in code_lower:
            return False, f"Patrón peligroso detectado: '{pattern}'"
    return True, "ok"


def format_code_for_display(code: str, language: str = "python", max_lines: int = 30) -> str:
    """Recorta y formatea código para mostrar en Telegram."""
    lines = code.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} líneas más)"]
    body = "\n".join(lines)
    return f"```{language}\n{textwrap.dedent(body)}\n```"
