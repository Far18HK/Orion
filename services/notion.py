"""Servicio de notas: crea, lista, busca, edita y archiva páginas de Notion."""
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from notion_client import AsyncClient

logger = logging.getLogger(__name__)

TITLE_MAX = 200
BLOCK_MAX = 2000
MAX_TAGS = 5
MAX_ALL_NOTES = 200
TAG_PATTERN = re.compile(r"(?<!\S)#(\w+)")


class NotionError(Exception):
    """Error amigable para mostrar al usuario."""


def extract_tags(text: str) -> tuple[str, list[str]]:
    """Separa etiquetas: 'Comprar #urgente leche' -> ('Comprar leche', ['urgente'])."""
    tags: list[str] = []
    for tag in TAG_PATTERN.findall(text):
        if tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    clean = " ".join(TAG_PATTERN.sub("", text).split())
    return clean, tags[:MAX_TAGS]


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    url: str
    created: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class _Schema:
    title: str
    date: str | None
    tags: str | None


class NotionService:
    def __init__(self, token: str, database_id: str) -> None:
        self.client = AsyncClient(auth=token)
        self.database_id = database_id
        self._schema: _Schema | None = None

    async def _get_schema(self) -> _Schema:
        if self._schema:
            return self._schema
        try:
            db = await self.client.databases.retrieve(database_id=self.database_id)
        except Exception as e:
            raise NotionError(
                "No pude leer tu base de datos de Notion 😕 revisa el token y el ID, "
                "y que la base esté compartida con tu integración."
            ) from e
        props = db.get("properties", {})

        def first(kind: str) -> str | None:
            return next((name for name, prop in props.items() if prop.get("type") == kind), None)

        title_col = first("title")
        if not title_col:
            raise NotionError("No encontré ninguna columna de tipo Título en Notion 😕")
        self._schema = _Schema(title=title_col, date=first("date"), tags=first("multi_select"))
        logger.info("Esquema de Notion detectado: %s", self._schema)
        return self._schema

    async def add_note(self, text: str, tags: list[str] | None = None) -> str:
        schema = await self._get_schema()
        text = text.strip()
        tags = list(tags or [])[:MAX_TAGS]
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
                        "rich_text": [{"type": "text", "text": {"content": text[i:i + BLOCK_MAX]}}]
                    },
                }
                for i in range(0, len(text), BLOCK_MAX)
            ]
        try:
            page = await self.client.pages.create(
                parent={"database_id": self.database_id}, properties=properties, **kwargs
            )
        except Exception as e:
            detail = getattr(e, "body", None) or getattr(e, "message", None) or str(e)
            raise NotionError(f"No pude guardar la nota en Notion 😕\nError: {detail}") from e
        return page.get("url", "")

    async def recent(self, limit: int = 5) -> list[Note]:
        return await self._query(min(max(limit, 1), 10))

    async def search(self, text: str, limit: int = 10) -> list[Note]:
        schema = await self._get_schema()
        text = text.strip()
        if text.startswith("#") and len(text) > 1 and schema.tags:
            flt = {"property": schema.tags, "multi_select": {"contains": text[1:]}}
        else:
            flt = {"property": schema.title, "title": {"contains": text}}
        return await self._query(min(max(limit, 1), 10), flt)

    async def all_notes(self, limit: int = MAX_ALL_NOTES) -> list[Note]:
        """Obtiene notas con paginación, limitado para evitar recorrer una base enorme."""
        schema = await self._get_schema()
        notes: list[Note] = []
        cursor: str | None = None
        limit = min(max(limit, 1), MAX_ALL_NOTES)
        while len(notes) < limit:
            kwargs: dict = {
                "database_id": self.database_id,
                "page_size": min(100, limit - len(notes)),
                "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            try:
                data = await self.client.databases.query(**kwargs)
            except Exception as e:
                raise NotionError("No pude consultar tus notas en Notion 😕 inténtalo de nuevo.") from e
            notes.extend(self._to_note(page, schema) for page in data.get("results", []))
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                break
        return notes[:limit]

    async def delete_notes(self, note_ids: Sequence[str]) -> int:
        """Archiva páginas de Notion; el usuario puede recuperarlas desde la papelera."""
        deleted = 0
        for note_id in list(note_ids)[:50]:
            try:
                await self.client.pages.update(page_id=note_id, archived=True)
                deleted += 1
            except Exception as e:
                logger.warning("No pude archivar nota %s: %s", note_id, e)
        if not deleted and note_ids:
            raise NotionError("No pude borrar ninguna nota 😕 revisa el acceso de la integración.")
        return deleted

    async def edit_note(
        self,
        note_id: str,
        text: str | None = None,
        add_tags: Sequence[str] | None = None,
        remove_tags: Sequence[str] | None = None,
    ) -> Note:
        schema = await self._get_schema()
        properties: dict = {}
        if text:
            properties[schema.title] = {"title": [{"text": {"content": text[:TITLE_MAX]}}]}
        if schema.tags and (add_tags or remove_tags):
            try:
                current = await self.client.pages.retrieve(page_id=note_id)
            except Exception as e:
                raise NotionError("No encontré esa nota 😕 búscala de nuevo.") from e
            existing = [
                option["name"]
                for option in current.get("properties", {}).get(schema.tags, {}).get("multi_select", [])
            ]
            removed = {str(tag).lstrip("#").lower() for tag in (remove_tags or [])}
            merged = [tag for tag in existing if tag.lower() not in removed]
            seen = {tag.lower() for tag in merged}
            for tag in list(add_tags or [])[:MAX_TAGS]:
                tag = str(tag).lstrip("#").strip()
                if tag and tag.lower() not in seen:
                    merged.append(tag)
                    seen.add(tag.lower())
            properties[schema.tags] = {"multi_select": [{"name": tag} for tag in merged[:MAX_TAGS]]}
        if not properties:
            raise NotionError("Indica un texto nuevo o etiquetas para editar la nota.")
        try:
            page = await self.client.pages.update(page_id=note_id, properties=properties)
        except Exception as e:
            detail = getattr(e, "body", None) or getattr(e, "message", None) or str(e)
            raise NotionError(f"No pude editar la nota 😕\nError: {detail}") from e
        return self._to_note(page, schema)

    async def _query(self, limit: int, flt: dict | None = None) -> list[Note]:
        schema = await self._get_schema()
        kwargs: dict = {
            "database_id": self.database_id,
            "page_size": limit,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        }
        if flt:
            kwargs["filter"] = flt
        try:
            data = await self.client.databases.query(**kwargs)
        except Exception as e:
            raise NotionError("No pude consultar tus notas en Notion 😕 inténtalo de nuevo.") from e
        return [self._to_note(page, schema) for page in data.get("results", [])]

    @staticmethod
    def _to_note(page: dict, schema: _Schema) -> Note:
        props = page.get("properties", {})
        title = "".join(
            part.get("plain_text", "")
            for part in props.get(schema.title, {}).get("title", [])
        ).strip()
        tags: tuple[str, ...] = ()
        if schema.tags:
            tags = tuple(
                option.get("name", "")
                for option in props.get(schema.tags, {}).get("multi_select", [])
            )
        created = ""
        if page.get("created_time"):
            created = datetime.fromisoformat(
                page["created_time"].replace("Z", "+00:00")
            ).strftime("%d/%m/%Y")
        return Note(
            id=page.get("id", ""),
            title=title or "(sin título)",
            url=page.get("url", ""),
            created=created,
            tags=tags,
        )
