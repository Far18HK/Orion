"""Servicio de notas: crea, lista y busca páginas en una base de datos de Notion."""
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from datetime import date, datetime

from notion_client import AsyncClient

TITLE_MAX = 200  # Lo que cabe en el título; si la nota es más larga, el texto completo va al cuerpo
BLOCK_MAX = 2000  # Límite de Notion por bloque de texto
MAX_TAGS = 5
TAG_PATTERN = re.compile(r"(?<!\S)#(\w+)")


class NotionError(Exception):
    """Error amigable para el usuario."""


def extract_tags(text: str) -> tuple[str, list[str]]:
    """Separa las #etiquetas del texto: 'Comprar #urgente leche' -> ('Comprar leche', ['urgente'])."""
    tags: list[str] = []
    for tag in TAG_PATTERN.findall(text):
        if tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    clean = " ".join(TAG_PATTERN.sub("", text).split())
    return clean, tags[:MAX_TAGS]


@dataclass(frozen=True)
class Note:
    title: str
    url: str
    created: str  # dd/mm/aaaa
    tags: tuple[str, ...]


@dataclass(frozen=True)
class _Schema:
    title: str  # Nombre de la propiedad de título
    date: str | None  # Primera propiedad de tipo fecha, si existe
    tags: str | None  # Primera propiedad de tipo multi-select, si existe


class NotionService:
    def __init__(self, token: str, database_id: str) -> None:
        # 2025-09-03: las columnas viven en el "data source", no en la base
        self.client = AsyncClient(auth=token, notion_version="2025-09-03")
        self.database_id = database_id
        self._data_source_id: str | None = None
        self._schema: _Schema | None = None

    async def _get_schema(self) -> _Schema:
        """Detecta por tipo (no por nombre) las columnas de la base de datos."""
        if self._schema:
            return self._schema
        try:
            db = await self.client.databases.retrieve(database_id=self.database_id)
        except Exception as e:
            raise NotionError(
                "No pude leer tu base de datos de Notion 😕 revisa el token y el ID, "
                "y que la base esté compartida con tu integración."
            ) from e

        props = db.get("properties") or {}
        if not props:
            # API nueva: GET /databases solo trae la lista de data sources;
            # el esquema (propiedades) se pide al data source.
            sources = db.get("data_sources") or []
            if not sources:
                raise NotionError(
                    "Tu base de Notion no devolvió columnas ni data sources 😕 "
                    "revisa que esté compartida con la integración."
                )
            self._data_source_id = sources[0]["id"]
            try:
                ds = await self.client.request(
                    path=f"data_sources/{self._data_source_id}", method="GET"
                )
            except Exception as e:
                raise NotionError("No pude leer las columnas de tu base de Notion 😕") from e
            props = ds.get("properties") or {}
        logger.info("Notion props: %s", {k: v.get("type") for k, v in props.items()})

        def first(kind: str) -> str | None:
            return next((name for name, p in props.items() if p.get("type") == kind), None)

        title_col = first("title")
        if not title_col:
            raise NotionError(
                "No encontré ninguna columna de título en tu base de datos de Notion 😕 "
                "Asegúrate de que tenga al menos una columna de tipo Título."
            )
        self._schema = _Schema(
            title=title_col, date=first("date"), tags=first("multi_select")
        )
        return self._schema

    async def add_note(self, text: str, tags: list[str] | None = None) -> str:
        """Crea una nota y devuelve su URL.

        Rellena la columna de fecha (con hoy) y la de etiquetas si la base las tiene.
        Si no hay columna de etiquetas, las #etiquetas quedan escritas en el texto.
        """
        schema = await self._get_schema()
        tags = list(tags or [])
        if tags and not schema.tags:
            text = f"{text} " + " ".join(f"#{t}" for t in tags)
            tags = []

        properties: dict = {
            schema.title: {"title": [{"text": {"content": text[:TITLE_MAX]}}]},
        }
        if schema.date:
            properties[schema.date] = {"date": {"start": date.today().isoformat()}}
        if schema.tags and tags:
            properties[schema.tags] = {"multi_select": [{"name": t} for t in tags]}

        kwargs: dict = {}
        if len(text) > TITLE_MAX:
            kwargs["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": text[i : i + BLOCK_MAX]}}]
                    },
                }
                for i in range(0, len(text), BLOCK_MAX)
            ]

        try:
            parent = (
                {"type": "data_source_id", "data_source_id": self._data_source_id}
                if self._data_source_id
                else {"database_id": self.database_id}
            )
            page = await self.client.pages.create(parent=parent, properties=properties, **kwargs)
        except Exception as e:
            # Extraemos el mensaje real de Notion para diagnosticar el problema
            detail = getattr(e, "body", None) or getattr(e, "message", None) or str(e)
            raise NotionError(
                f"No pude guardar la nota en Notion 😕\nError: {detail}"
            ) from e
        return page.get("url", "")

    async def recent(self, limit: int = 5) -> list[Note]:
        """Las últimas notas creadas."""
        return await self._query(limit)

    async def search(self, text: str, limit: int = 10) -> list[Note]:
        """Busca por título. Si empieza con # y hay columna de etiquetas, busca por etiqueta.

        La API de Notion no permite buscar dentro del cuerpo de las páginas.
        """
        schema = await self._get_schema()
        if text.startswith("#") and len(text) > 1 and schema.tags:
            flt = {"property": schema.tags, "multi_select": {"contains": text[1:]}}
        else:
            flt = {"property": schema.title, "title": {"contains": text}}
        return await self._query(limit, flt)

    async def _query(self, limit: int, flt: dict | None = None) -> list[Note]:
        schema = await self._get_schema()
        kwargs: dict = {
            "page_size": limit,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        }
        if flt:
            kwargs["filter"] = flt
        try:
            if self._data_source_id:
                data = await self.client.request(
                    path=f"data_sources/{self._data_source_id}/query", method="POST", body=kwargs
                )
            else:
                data = await self.client.databases.query(database_id=self.database_id, **kwargs)
        except Exception as e:
            raise NotionError("No pude consultar tus notas en Notion 😕 inténtalo de nuevo.") from e
        return [self._to_note(page, schema) for page in data.get("results", [])]

    @staticmethod
    def _to_note(page: dict, schema: _Schema) -> Note:
        props = page.get("properties", {})
        title = "".join(
            part.get("plain_text", "") for part in props.get(schema.title, {}).get("title", [])
        ).strip()
        tags: tuple[str, ...] = ()
        if schema.tags:
            tags = tuple(o["name"] for o in props.get(schema.tags, {}).get("multi_select", []))
        created = ""
        if page.get("created_time"):
            created = datetime.fromisoformat(page["created_time"].replace("Z", "+00:00")).strftime(
                "%d/%m/%Y"
            )
        return Note(title=title or "(sin título)", url=page.get("url", ""), created=created, tags=tags)
