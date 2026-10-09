"""Servicio de notas: crea páginas en una base de datos de Notion."""
from notion_client import AsyncClient


class NotionError(Exception):
    """Error amigable para el usuario."""


class NotionService:
    def __init__(self, token: str, database_id: str) -> None:
        self.client = AsyncClient(auth=token)
        self.database_id = database_id

    async def add_note(self, text: str) -> str:
        """Crea una nota en la base de datos de Notion y devuelve su URL."""
        try:
            page = await self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    # Asume que la base de datos tiene una propiedad de título llamada "Name"
                    "Name": {"title": [{"text": {"content": text[:200]}}]},
                },
            )
        except Exception as e:
            raise NotionError(
                "No pude guardar la nota en Notion 😕 revisa el token y el ID de la base de datos."
            ) from e
        return page.get("url", "")
