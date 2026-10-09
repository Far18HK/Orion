"""Comandos básicos: /start, /ayuda y /reset."""
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, Message

from services.groq_service import GroqService

router = Router()

# Fuente única: alimenta el menú de Telegram (botón "/") y el texto de /ayuda
COMMANDS = [
    ("ayuda", "Muestra esta ayuda"),
    ("recordar", "Programa un recordatorio. Ej: /recordar 30m Tomar agua"),
    ("nota", "Guarda una nota en Notion. Ej: /nota Comprar leche #casa"),
    ("notas", "Muestra tus últimas notas de Notion"),
    ("buscarnota", "Busca notas por texto o #etiqueta"),
    ("cerrardoc", "Olvida el documento que me enviaste"),
    ("modelo", "Ve o cambia el modelo de IA"),
    ("reset", "Borra la memoria de esta conversación"),
]
BOT_COMMANDS = [BotCommand(command=name, description=desc) for name, desc in COMMANDS]

HELP_TEXT = (
    "Comandos disponibles:\n"
    + "\n".join(f"/{name} — {desc}" for name, desc in COMMANDS)
    + "\n\nTambién puedes escribirme normal o mandarme fotos, notas de voz y documentos "
    "(PDF, DOCX, texto). Busco en internet y te digo el clima cuando haga falta."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    nombre = message.from_user.first_name if message.from_user else "amigo"
    await message.answer(
        f"¡Hola, {nombre}! 👋\n\n"
        "Soy tu asistente personal con IA. Recuerdo nuestra conversación reciente "
        "mientras hablamos 🧠\n\n" + HELP_TEXT
    )


@router.message(Command("ayuda", "help"))
async def cmd_ayuda(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("reset"))
async def cmd_reset(message: Message, ai: GroqService) -> None:
    # `ai` llega automáticamente desde dp["ai"] en main.py
    ai.reset(message.from_user.id)
    await message.answer("Listo, memoria borrada ✨ ¿De qué hablamos ahora?")
