"""Punto de entrada del bot."""
import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from handlers import chat, documents, media, model, notes, reminders, start
from handlers.start import BOT_COMMANDS
from middlewares import RateLimitMiddleware, UserGuardMiddleware
from services.github import GitHubService
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

    dp.message.outer_middleware(
        UserGuardMiddleware(settings.allowed_user_ids, settings.daily_message_limit)
    )

    # Límite de mensajes por usuario: protege tu cuota de Groq de ráfagas o abusos
    if settings.rate_limit_messages > 0:
        dp.message.outer_middleware(
            RateLimitMiddleware(settings.rate_limit_messages, settings.rate_limit_window)
        )

    # Inyección de dependencias: los handlers reciben estos objetos como parámetros
    logging.info("Base de datos de recordatorios: %s", settings.db_path)
    reminder_service = ReminderService(
        token=settings.telegram_token,
        db_path=settings.db_path,
        default_timezone=settings.timezone,
        discord_token=settings.discord_token,
    )
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

    # GitHub es opcional y de solo lectura. Como da acceso a repos privados y el bot es público,
    # exigimos GITHUB_OWNER_IDS: sin ids no se activa
    github = None
    if settings.github_token and settings.github_owner_ids:
        github = GitHubService(settings.github_token)
    elif settings.github_token:
        logging.warning("GITHUB_TOKEN sin GITHUB_OWNER_IDS: GitHub deshabilitado (usa /id para ver tu id)")

    # El agente se crea al final porque sus herramientas usan recordatorios y Notion
    ai = GroqService(
        api_keys=settings.groq_api_keys,
        model=settings.groq_model,
        max_history=settings.max_history,
        reminders=reminder_service,
        notion=dp["notion"],
        github=github,
        github_user_ids=settings.github_owner_ids,
        discord_token=settings.discord_token,
        discord_dm_ids=settings.discord_dm_ids,
        discord_monitor_user_ids=settings.discord_monitor_user_ids,
        discord_monitor_telegram_ids=settings.discord_monitor_telegram_ids,
        discord_monitor_channel_ids=settings.discord_monitor_channel_ids,
        multi_brain_size=settings.multi_brain_size,
    )
    dp["ai"] = ai

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

    tasks = [dp.start_polling(bot)]
    if settings.discord_token:
        tasks.append(_run_discord(ai, settings))
    await asyncio.gather(*tasks)


async def _run_discord(ai: GroqService, settings) -> None:
    """Corre el frente de Discord sin tumbar a Telegram si falla (token malo, intent apagado...)."""
    try:
        import discord_bot  # Import tardío: sin DISCORD_TOKEN no hace falta tener discord.py
    except ImportError:
        logging.error("Hay DISCORD_TOKEN pero falta discord.py: agrégalo a requirements.txt")
        return
    client = discord_bot.DiscordFront(ai, settings.rate_limit_messages, settings.rate_limit_window)
    try:
        await client.start(settings.discord_token)
    except Exception:  # noqa: BLE001 - Telegram debe seguir vivo pase lo que pase
        logging.exception("Discord se detuvo")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot detenido")
