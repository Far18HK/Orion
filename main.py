"""Punto de entrada del bot."""
import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from handlers import chat, start
from services.gemini import GeminiService


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()

    bot = Bot(token=settings.telegram_token)
    dp = Dispatcher()

    # Inyección de dependencias: los handlers reciben `gemini` como parámetro
    dp["gemini"] = GeminiService(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        max_history=settings.max_history,
    )

    # El orden importa: chat va ÚLTIMO porque atrapa cualquier mensaje
    dp.include_router(start.router)
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
