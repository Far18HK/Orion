# Orion

Asistente personal con IA para **Telegram** y opcionalmente **Discord**, construido con Python, aiogram y Groq.

## Funciones

- Chat con modelos de Groq y memoria reciente.
- Visión para fotos y transcripción de notas de voz.
- Lectura y preguntas sobre PDF, DOCX y archivos de texto/código.
- Recordatorios únicos y repetidos con zona horaria, persistidos en SQLite.
- Notas en Notion: crear, listar, buscar, editar y archivar.
- Búsqueda web, clima, hora y calculadora.
- Lectura segura de enlaces con protección básica contra SSRF.
- Análisis de repositorios GitHub en modo solo lectura y con lista blanca.

## Instalación local

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edita .env y completa TELEGRAM_TOKEN y GROQ_API_KEY
python main.py
```

## Configuración importante

Consulta `.env.example`. Las variables mínimas son:

- `TELEGRAM_TOKEN`: token creado con BotFather.
- `GROQ_API_KEY` o `GROQ_API_KEYS`: una o varias claves de Groq.

Para un bot privado, configura `ALLOWED_USER_IDS`. Si es público, configura también `DAILY_MESSAGE_LIMIT` para proteger la cuota de Groq.

`DISCORD_DM_IDS` debe contener los IDs de Discord autorizados a recibir mensajes directos. En Discord, escribe `id` al bot para obtener tu ID.

## Railway

Monta un Volume persistente y define `DB_PATH=/data/reminders.db` (o deja que Railway use `RAILWAY_VOLUME_MOUNT_PATH`). El bot debe ejecutarse como **una sola réplica**, porque SQLite y el scheduler no están preparados para varios procesos.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas actuales cubren el parser de recordatorios y sus errores más importantes.

## Seguridad

- Nunca subas `.env` ni tokens al repositorio.
- Usa tokens de GitHub con permisos mínimos y configura `GITHUB_OWNER_IDS`.
- Si el bot será público, usa lista blanca o límite diario.
- Revisa los logs: los errores de herramientas ahora incluyen la traza completa.
