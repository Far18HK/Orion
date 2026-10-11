"""Utilidades compartidas para respuestas seguras en Telegram."""

from collections.abc import Awaitable, Callable

TELEGRAM_LIMIT = 4096


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Parte texto sin superar el límite de Telegram y prioriza saltos de línea."""
    text = text or "(sin respuesta)"
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < max(1, limit // 2):
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


async def send_long_answer(
    send: Callable[..., Awaitable[object]], text: str, **kwargs
) -> None:
    """Envía una respuesta en varios mensajes, conservando kwargs en el último."""
    chunks = split_text(text)
    for index, chunk in enumerate(chunks):
        await send(chunk, **(kwargs if index == len(chunks) - 1 else {}))
