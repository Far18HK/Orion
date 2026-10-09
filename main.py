"""Punto de entrada del bot."""
import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from handlers import chat, media, notes, reminders, start
from services.gemini import GeminiService
from services.notion import NotionService
from services.reminders import ReminderService


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()

    bot = Bot(token=settings.telegram_token)
    dp = Dispatcher()

    # Inyección de dependencias: los handlers reciben estos objetos como parámetros.
    # GeminiService ya incluye búsqueda web de Google como herramienta automática.
    dp["gemini"] = GeminiService(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        max_history=settings.max_history,
    )

    reminder_service = ReminderService(token=settings.telegram_token)
    reminder_service.start()
    dp["reminders"] = reminder_service

    # Notion es opcional: si no hay credenciales, /nota avisa en vez de fallar
    if settings.notion_token and settings.notion_database_id:
        dp["notion"] = NotionService(
            token=settings.notion_token, database_id=settings.notion_database_id
        )
    else:
        dp["notion"] = None
        logging.warning("NOTION_TOKEN/NOTION_DATABASE_ID no configurados: /nota quedará deshabilitado")

    # El orden importa: chat va ÚLTIMO porque atrapa cualquier mensaje de texto restante
    dp.include_router(start.router)
    dp.include_router(reminders.router)
    dp.include_router(notes.router)
    dp.include_router(media.router)
    dp.include_router(chat.router)

    # Ignora mensajes acumulados mientras el bot estuvo apagado
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot iniciado con el modelo %s", settings.gemini_model)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot detenido")
