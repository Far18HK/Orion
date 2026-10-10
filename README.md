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
- Lectura bajo demanda de canales autorizados de Discord.
- Modo multi-cerebro: 2 o 3 claves de Groq trabajan en paralelo y una sintetiza.
- Memoria permanente, tareas, planes, automatizaciones registradas y auditoría en SQLite.
- Harness de razonamiento con LangGraph y límites/validación de estado con Pydantic.

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

Para que Orion pueda revisar Discord cuando se lo pidas, configura `DISCORD_MONITOR_CHANNEL_IDS` con los IDs de canales permitidos, `DISCORD_MONITOR_USER_IDS` para usuarios de Discord y/o `DISCORD_MONITOR_TELEGRAM_IDS` para usuarios de Telegram. La lectura es bajo demanda; no se mantiene una vigilancia permanente.

Para permitir que publique mensajes, configura además `DISCORD_WRITE_CHANNEL_IDS`. Es una lista separada y más restrictiva. Desde Telegram debes indicar el ID del canal; desde Discord puede usar el canal actual. El bot necesita también el permiso **Send Messages**.

Para activar el modo multi-cerebro, define `GROQ_API_KEYS` con 2 o 3 claves de cuentas distintas y `MULTI_BRAIN_SIZE=2` o `MULTI_BRAIN_SIZE=3`. El agente usará `team_reason` cuando pidas una segunda opinión, una revisión profunda o "doble/triple cerebro". Esto consume una llamada por analista y una llamada final de síntesis.

Funciones autónomas disponibles: `remember`/`recall`/`forget` para memoria, `create_task`/`list_tasks`/`complete_task` para tareas, `plan_task` para descomponer objetivos y `audit_log` para consultar acciones. `create_automation` registra y ejecuta instrucciones con frecuencias de intervalo como `cada 30m`, `cada 2h` o `cada 1d`; las ejecuciones sobreviven a reinicios porque su estado queda en SQLite.

## Railway

Monta un Volume persistente y define `DB_PATH=/data/reminders.db` (o deja que Railway use `RAILWAY_VOLUME_MOUNT_PATH`). El bot debe ejecutarse como **una sola réplica**, porque SQLite y el scheduler no están preparados para varios procesos.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas actuales cubren el parser de recordatorios y sus errores más importantes.

## Harness de razonamiento

Orion usa LangGraph para orquestar cada turno: modelo, herramientas y respuesta final forman un flujo controlado con un máximo de rondas. Pydantic valida la política del harness para evitar límites inválidos. Telegram, Discord, Groq, Notion, GitHub y las herramientas existentes siguen siendo los adaptadores de la aplicación; el harness no reemplaza esos servicios.

La persistencia de automatizaciones y memoria continúa en SQLite. El grafo actual controla el turno en ejecución; la reanudación de un turno interrumpido entre reinicios sigue dependiendo del estado de SQLite y es una mejora posterior, no una capacidad que se deba asumir automáticamente.

## Seguridad

- Nunca subas `.env` ni tokens al repositorio.
- Usa tokens de GitHub con permisos mínimos y configura `GITHUB_OWNER_IDS`.
- Si el bot será público, usa lista blanca o límite diario.
- Revisa los logs: los errores de herramientas ahora incluyen la traza completa.
