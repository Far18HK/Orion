"""Punto de entrada del bot."""
import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from handlers import chat, documents, media, model, notes, reminders, start
from handlers.start import BOT_COMMANDS
from middlewares import RateLimitMiddleware
from services.groq_service import GroqService
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

    # Límite de mensajes por usuario: protege tu cuota de Groq de ráfagas o abusos
    if settings.rate_limit_messages > 0:
        dp.message.outer_middleware(
            RateLimitMiddleware(settings.rate_limit_messages, settings.rate_limit_window)
        )

    # Inyección de dependencias: los handlers reciben estos objetos como parámetros
    dp["ai"] = GroqService(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
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
    dp.include_router(documents.router)
    dp.include_router(model.router)
    dp.include_router(chat.router)

    # Menú de comandos (botón "/" en Telegram)
    await bot.set_my_commands(BOT_COMMANDS)

    # Ignora mensajes acumulados mientras el bot estuvo apagado
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot iniciado con el modelo %s (Groq)", settings.groq_model)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot detenido")
