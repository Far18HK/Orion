"""Comandos básicos: /start, /ayuda y /reset."""

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, Message

from services.groq_service import GroqService

router = Router()

# Fuente única: alimenta el menú de Telegram (botón "/") y el texto de /ayuda
COMMANDS = [
    ("ayuda", "Muestra esta ayuda"),
    ("recordar", "Programa un recordatorio: 30m, mañana 8:00, cada lunes 9:00..."),
    ("recordatorios", "Mira y cancela tus recordatorios pendientes"),
    ("zona", "Ve o cambia tu zona horaria"),
    ("nota", "Guarda una nota en Notion. Ej: /nota Comprar leche #casa"),
    ("notas", "Muestra tus últimas notas de Notion"),
    ("buscarnota", "Busca notas por texto o #etiqueta"),
    ("cerrardoc", "Olvida el documento que me enviaste"),
    ("modelo", "Ve o cambia el modelo de IA"),
    ("cerebro", "Cambia entre auto, 1, 2 o 3 cerebros"),
    ("estado", "Muestra la salud y configuración de Orion"),
    ("proyecto", "Activa o cambia el proyecto de trabajo"),
    ("aprobar", "Aprueba la acción que Orion dejó en pausa"),
    ("rechazar", "Rechaza la acción que Orion dejó en pausa"),
    ("reanudar", "Retoma un turno que quedó a medias tras un reinicio"),
    ("reset", "Borra la memoria de esta conversación"),
]
BOT_COMMANDS = [BotCommand(command=name, description=desc) for name, desc in COMMANDS]

HELP_TEXT = (
    "Comandos disponibles:\n"
    + "\n".join(f"/{name} — {desc}" for name, desc in COMMANDS)
    + "\n\nTambién puedes escribirme normal o mandarme fotos, notas de voz y documentos "
    "(PDF, DOCX, texto). Soy un agente: puedes pedirme las cosas en lenguaje normal "
    '("avísame mañana a las 8 de la junta", "anota comprar leche", "qué clima hace en Lima", '
    '"cancela mi recordatorio de la junta") y yo decido qué herramienta usar: buscar en internet, '
    "leer enlaces, recordatorios, notas, calculadora, clima y hora."
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


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    # Sirve para llenar GITHUB_OWNER_IDS en el .env
    if message.from_user is None:
        return
    await message.answer(f"Tu id de Telegram es {message.from_user.id}")


@router.message(Command("estado"))
async def cmd_estado(message: Message, ai: GroqService) -> None:
    if message.from_user is None:
        return
    state = ai.health_snapshot(message.from_user.id)
    brain_mode = state["brain_mode"] or "auto"
    phase = state["checkpoint"][0] if state["checkpoint"] else None
    checkpoint = {
        None: "no",
        "awaiting_approval": "sí, esperando tu aprobación (/aprobar o /rechazar)",
        "running": "sí, turno interrumpido (usa /reanudar)",
    }.get(phase, f"sí ({phase})")
    await message.answer(
        "Estado de Orion:\n"
        f"- Modelo: {state['model']}\n"
        f"- API keys configuradas: {state['configured_keys']}\n"
        f"- Cuenta activa: #{state['active_key']}\n"
        f"- Modo cerebro: {brain_mode}\n"
        f"- Memoria de conversación: {state['memory_items']} mensajes\n"
        f"- Checkpoint pendiente: {checkpoint}\n\n"
        "Nota: las API keys configuradas no equivalen a cuota disponible; Groq solo confirma "
        "el agotamiento al intentar usarlas."
    )


@router.message(Command("proyecto"))
async def cmd_proyecto(message: Message, command: CommandObject, ai: GroqService) -> None:
    if message.from_user is None:
        return
    name = (command.args or "").strip()
    if not name:
        current = ai.get_project(message.from_user.id)
        await message.answer(
            f"Proyecto activo: {current or 'ninguno'}\nUsa /proyecto <nombre> o /proyecto off."
        )
        return
    if name.lower() in {"off", "ninguno", "reset"}:
        ai.set_project(message.from_user.id, None)
        await message.answer("Quité el proyecto activo.")
        return
    selected = ai.set_project(message.from_user.id, name)
    await message.answer(f"Proyecto activo: {selected}.")


@router.message(Command("reset"))
async def cmd_reset(message: Message, ai: GroqService) -> None:
    # `ai` llega automáticamente desde dp["ai"] en main.py
    if message.from_user is None:
        return
    ai.reset(message.from_user.id)
    await message.answer("Listo, memoria borrada ✨ ¿De qué hablamos ahora?")
