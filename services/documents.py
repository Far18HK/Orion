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

router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB: también es el límite de descarga de la API de Telegram


@router.message(F.document)
async def handle_document(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    doc = message.document
    name = doc.file_name or "documento"

    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        await message.answer(
            "Ese formato no lo puedo leer todavía 📄 Acepto PDF, DOCX y archivos de texto "
            "(txt, md, csv, json, código...)."
        )
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.answer("Ese archivo pesa demasiado 😅 mándame uno de menos de 20 MB.")
        return

    file = await message.bot.get_file(doc.file_id)
    buffer = await message.bot.download_file(file.file_path)
    # Si mandaste el archivo con texto (caption), lo usamos como pregunta
    question = message.caption or "Resume este documento de forma clara y breve."

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            text, truncated = await extract_text(buffer.read(), name)
            answer = await ai.ask_about_document(
                message.from_user.id, name, text, question, chat_id=message.chat.id
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
