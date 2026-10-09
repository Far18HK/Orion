"""Lectura de GitHub (solo lectura) para que el agente explore y analice tu código."""
import json
import logging
import re
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

API = "https://api.github.com"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", "vendor"}
MAX_TREE = 300  # Archivos máximos al listar un repo
MAX_FILE_BYTES = 500_000
CHUNK_CHARS = 5000  # Texto por lectura: el agente pagina con start_line si el archivo es largo


class GitHubError(Exception):
    """Error legible para el modelo y el usuario."""


class GitHubService:
    def __init__(self, token: str) -> None:
        self.token = token
        self._login: str | None = None

    async def _request(self, path: str, params: dict | None = None, raw: bool = False):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "User-Agent": "AsistenteBot",
        }
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
            async with session.get(API + path, params=params) as resp:
                if resp.status == 404:
                    raise GitHubError("No existe o mi token no tiene acceso.")
                if resp.status == 401:
                    raise GitHubError("El token de GitHub no es válido o expiró.")
                if resp.status in (403, 429):
                    raise GitHubError("GitHub me limitó o el token no tiene ese permiso. Reintenta en un rato.")
                if resp.status >= 400:
                    raise GitHubError(f"GitHub respondió con el código {resp.status}.")
                if raw:
                    data = await resp.read()
                    if len(data) > MAX_FILE_BYTES:
                        raise GitHubError("El archivo es demasiado grande para leerlo.")
                    try:
                        return data.decode("utf-8")
                    except UnicodeDecodeError:
                        raise GitHubError("Es un archivo binario; no lo puedo leer como texto.") from None
                return await resp.json()

    async def login(self) -> str:
        if not self._login:
            self._login = (await self._request("/user"))["login"]
        return self._login

    async def _repo(self, repo: str) -> str:
        """'miRepo' -> 'tuUsuario/miRepo'. Valida el formato para no armar rutas raras."""
        repo = repo.strip()
        if not REPO_PATTERN.match(repo):
            raise GitHubError("Nombre de repo inválido: usa 'repo' o 'usuario/repo'.")
        return repo if "/" in repo else f"{await self.login()}/{repo}"

    @staticmethod
    def _check_path(path: str) -> str:
        path = path.strip().strip("/")
        if ".." in path.split("/"):
            raise GitHubError("Ruta inválida.")
        return quote(path)

    # ---- consultas ----

    async def list_repos(self, limit: int = 20) -> str:
        data = await self._request(
            "/user/repos", {"sort": "pushed", "per_page": min(max(limit, 1), 50), "affiliation": "owner"}
        )
        if not data:
            return "No hay repositorios."
        return "\n".join(
            f"- {r['full_name']} ({'privado' if r['private'] else 'público'}"
            f"{', ' + r['language'] if r.get('language') else ''}) "
            f"último push {r['pushed_at'][:10]}" + (f" — {r['description']}" if r.get("description") else "")
            for r in data
        )

    async def overview(self, repo: str) -> str:
        full = await self._repo(repo)
        info = await self._request(f"/repos/{full}")
        langs = await self._request(f"/repos/{full}/languages")
        total = sum(langs.values()) or 1
        lang_text = ", ".join(f"{k} {v * 100 // total}%" for k, v in list(langs.items())[:6])
        top = await self._request(f"/repos/{full}/contents")
        entries = ", ".join(f"{e['name']}{'/' if e['type'] == 'dir' else ''}" for e in top)
        lines = [
            f"{info['full_name']} ({'privado' if info['private'] else 'público'})",
            f"Descripción: {info.get('description') or '(sin descripción)'}",
            f"Rama principal: {info['default_branch']} · último push {info['pushed_at'][:10]} · "
            f"issues abiertos: {info['open_issues_count']}",
            f"Lenguajes: {lang_text or 'n/d'}",
            f"Raíz: {entries}",
        ]
        try:
            readme = await self._request(f"/repos/{full}/readme", raw=True)
            lines.append("README (inicio):\n" + readme[:1500])
        except GitHubError:
            lines.append("No tiene README.")
        return "\n".join(lines)

    async def list_files(self, repo: str, path: str = "", ref: str | None = None) -> str:
        full = await self._repo(repo)
        branch = ref or (await self._request(f"/repos/{full}"))["default_branch"]
        data = await self._request(f"/repos/{full}/git/trees/{quote(branch)}", {"recursive": "1"})
        prefix = path.strip().strip("/")
        files = [
            e for e in data.get("tree", [])
            if e["type"] == "blob"
            and not SKIP_DIRS.intersection(e["path"].split("/"))
            and (not prefix or e["path"].startswith(prefix + "/"))
        ]
        if not files:
            return "No encontré archivos ahí."
        shown = files[:MAX_TREE]
        lines = [f"{e['path']} ({e.get('size', 0)} B)" for e in shown]
        if len(files) > MAX_TREE:
            lines.append(f"... y {len(files) - MAX_TREE} más (usa 'path' para acotar a una carpeta)")
        return f"Archivos de {full}@{branch}:\n" + "\n".join(lines)

    async def read_file(self, repo: str, path: str, ref: str | None = None, start_line: int = 1) -> str:
        full = await self._repo(repo)
        if not path.strip():
            raise GitHubError("Falta la ruta del archivo.")
        text = await self._request(
            f"/repos/{full}/contents/{self._check_path(path)}", {"ref": ref} if ref else None, raw=True
        )
        if text.lstrip().startswith("[") and '"type"' in text[:300]:
            try:
                json.loads(text)
                raise GitHubError("Eso es una carpeta; usa github_list_files.")
            except json.JSONDecodeError:
                pass  # Era un archivo de texto que empieza con "[": seguimos
        lines = text.splitlines()
        start = max(int(start_line), 1)
        out: list[str] = []
        size = 0
        n = start
        while n <= len(lines) and size < CHUNK_CHARS:
            row = f"{n}: {lines[n - 1]}"
            out.append(row)
            size += len(row) + 1
            n += 1
        if not out:
            return f"El archivo tiene solo {len(lines)} líneas."
        footer = (
            f"\n[... sigue: llama de nuevo con start_line={n}; el archivo tiene {len(lines)} líneas]"
            if n <= len(lines)
            else f"\n[fin del archivo, {len(lines)} líneas]"
        )
        return f"{full}/{path}\n" + "\n".join(out) + footer

    async def search_code(self, query: str, repo: str | None = None) -> str:
        scope = f"repo:{await self._repo(repo)}" if repo else f"user:{await self.login()}"
        data = await self._request("/search/code", {"q": f"{query} {scope}", "per_page": 10})
        items = data.get("items", [])
        if not items:
            return "Sin resultados (la búsqueda solo indexa la rama principal)."
        return "\n".join(f"- {i['repository']['full_name']}: {i['path']}" for i in items)

    async def recent_commits(self, repo: str, limit: int = 10, path: str | None = None) -> str:
        full = await self._repo(repo)
        params: dict = {"per_page": min(max(limit, 1), 20)}
        if path:
            params["path"] = path.strip("/")
        data = await self._request(f"/repos/{full}/commits", params)
        if not data:
            return "Sin commits."
        return "\n".join(
            f"- {c['commit']['author']['date'][:10]} {c['sha'][:7]} "
            f"{c['commit']['author']['name']}: {c['commit']['message'].splitlines()[0][:100]}"
            for c in data
        )

    async def issues(self, repo: str, state: str = "open", limit: int = 10) -> str:
        full = await self._repo(repo)
        state = state if state in ("open", "closed", "all") else "open"
        data = await self._request(
            f"/repos/{full}/issues", {"state": state, "per_page": min(max(limit, 1), 20)}
        )
        if not data:
            return "No hay issues ni PRs con ese filtro."
        return "\n".join(
            f"- #{i['number']} [{'PR' if 'pull_request' in i else 'issue'}, {i['state']}] {i['title']}"
            for i in data
        )
