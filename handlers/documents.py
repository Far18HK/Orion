"""Documentos (PDF, DOCX, texto): se leen y se pueden resumir o preguntar sobre ellos."""
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from handlers.chat import split_text
from services.documents import (
    MAX_DOC_CHARS,
    SUPPORTED_EXTENSIONS,
    DocumentError,
    extract_text,
)
from services.groq_service import GroqError, GroqService
from services.workspace import WorkspaceError, save_attachment

router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB: también es el límite de descarga de la API de Telegram
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@router.message(F.document)
async def handle_document(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    doc = message.document
    name = doc.file_name or "documento"

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.answer("Ese archivo pesa demasiado 😅 mándame uno de menos de 20 MB.")
        return

    file = await message.bot.get_file(doc.file_id)
    buffer = await message.bot.download_file(file.file_path)
    data = buffer.read()
    try:
        saved_path = save_attachment(ai.workspace_root, message.from_user.id, name, data)
    except WorkspaceError as exc:
        await message.answer(f"No pude guardar el archivo: {exc}")
        return
    # Si mandaste el archivo con texto (caption), lo usamos como pregunta
    question = message.caption or "Resume este documento de forma clara y breve."

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            extension = Path(name).suffix.lower()
            if extension in AUDIO_EXTENSIONS:
                transcript = await ai.transcribe(data, filename=name)
                answer = await ai.ask(
                    message.from_user.id,
                    f"Transcripción del archivo {name}:\n{transcript}\n\nPetición: {question}",
                    chat_id=message.chat.id,
                )
                truncated = False
            elif extension in IMAGE_EXTENSIONS:
                answer = await ai.ask_with_image(
                    user_id=message.from_user.id, image_bytes=data, prompt=question
                )
                truncated = False
            elif extension in SUPPORTED_EXTENSIONS:
                text, truncated = await extract_text(data, name)
                answer = await ai.ask_about_document(
                    message.from_user.id, name, text, question, chat_id=message.chat.id
                )
            else:
                truncated = False
                answer = await ai.ask(
                    message.from_user.id,
                    f"El usuario acaba de enviarme el archivo {name}, guardado como "
                    f"{saved_path.relative_to(ai.workspace_root)}. Petición: {question} "
                    "Usa list_files/read_file si necesitas inspeccionarlo. Si es binario, "
                    "explica sus metadatos y qué operación puedes hacer sin inventar contenido.",
                    chat_id=message.chat.id,
                )
        except (DocumentError, GroqError) as e:
            await message.answer(str(e))
            return

    for chunk in split_text(answer):
        await message.answer(chunk, parse_mode=None)

    note = "Ya tengo el documento en mente: pregúntame lo que quieras sobre él. /cerrardoc para olvidarlo 📎"
    if truncated:
        note = (
            f"⚠️ Era largo: solo leí los primeros {MAX_DOC_CHARS:,} caracteres.\n".replace(",", ".")
            + note
        )
    await message.answer(note)


@router.message(Command("cerrardoc"))
async def cmd_cerrardoc(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    if ai.clear_document(message.from_user.id):
        await message.answer("Listo, olvidé el documento 📎 ¿Qué más?")
    else:
        await message.answer("No tengo ningún documento cargado ahora mismo.")
