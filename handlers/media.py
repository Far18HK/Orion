"""Handlers para fotos y notas de voz: se mandan a Gemini como contenido multimodal."""
from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from handlers.chat import split_text
from services.gemini import GeminiError, GeminiService

router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB: límite prudente para no descargar archivos enormes


@router.message(F.photo)
async def handle_photo(message: Message, gemini: GeminiService) -> None:
    photo = message.photo[-1]  # Telegram manda varias resoluciones; tomamos la más grande
    file = await message.bot.get_file(photo.file_id)

    if file.file_size and file.file_size > MAX_FILE_SIZE:
        await message.answer("Esa imagen pesa demasiado 😅 mándame una más ligera.")
        return

    buffer = await message.bot.download_file(file.file_path)
    # Si mandaste la foto con texto (caption), lo usamos como pregunta
    prompt = message.caption or "Describe esta imagen y comenta lo que veas."

    async with ChatActionSender.upload_photo(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await gemini.ask_with_media(
                user_id=message.from_user.id,
                data=buffer.read(),
                mime_type="image/jpeg",
                prompt=prompt,
            )
        except GeminiError as e:
            await message.answer(str(e))
            return

    for chunk in split_text(answer):
        await message.answer(chunk, parse_mode=None)


@router.message(F.voice)
async def handle_voice(message: Message, gemini: GeminiService) -> None:
    voice = message.voice

    if voice.file_size and voice.file_size > MAX_FILE_SIZE:
        await message.answer("Ese audio pesa demasiado 😅 mándame uno más corto.")
        return

    file = await message.bot.get_file(voice.file_id)
    buffer = await message.bot.download_file(file.file_path)
    prompt = "Escucha este audio y responde de forma natural a lo que se dice en él."

    async with ChatActionSender.record_voice(bot=message.bot, chat_id=message.chat.id):
        try:
            answer = await gemini.ask_with_media(
                user_id=message.from_user.id,
                data=buffer.read(),
                mime_type="audio/ogg",
                prompt=prompt,
            )
        except GeminiError as e:
            await message.answer(str(e))
            return

    for chunk in split_text(answer):
        await message.answer(chunk, parse_mode=None)
