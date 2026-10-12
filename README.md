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
- OpenRouter como proveedor principal opcional para texto, con fallback automático a Groq.
- Memoria permanente, tareas, planes, automatizaciones registradas y auditoría en SQLite.
- Harness de razonamiento con LangGraph y límites/validación de estado con Pydantic.
- Arquitectura híbrida: LangGraph conserva el flujo durable, las interrupciones y la reanudación; PydanticAI aporta planes y verificaciones tipadas mediante `plan_task` y `verify_step`.
- Estado operativo con `/estado`, proyectos aislados con `/proyecto` y checkpoints SQLite.
- Ejecuciones autónomas persistentes consultables con `/ejecuciones` y cancelables con `/cancelar_ejecucion <id>`, con presupuesto de herramientas por turno.
- Espacio de archivos privado por usuario: recibe adjuntos de cualquier extensión, conserva binarios, inspecciona texto y ZIP, crea archivos de texto o binarios Base64 y comprime archivos.
- Aprobación humana real (`/aprobar`, `/rechazar` o botones) para acciones con efectos, y `/reanudar` para retomar un turno interrumpido.
- Por seguridad, Orion también pide aprobación para DMs de Discord, editar/borrar notas y cancelar recordatorios.

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

Si defines `OPENROUTER_API_KEY`, Orion usa OpenRouter para chat y herramientas de texto. Groq se
mantiene para visión, transcripción de audio y como fallback si OpenRouter falla. El modelo
predeterminado es `nvidia/nemotron-3-super-120b-a12b:free`; puedes cambiarlo con `OPENROUTER_MODEL`.

Orión usa un tono informal, sarcástico y realista, y conserva en SQLite los turnos finales de
conversación, las memorias que el usuario le pide guardar y el proyecto activo. Para que esa
memoria sobreviva despliegues en Railway, usa un Volume persistente.

Para un bot privado, configura `ALLOWED_USER_IDS`. Si es público, configura también `DAILY_MESSAGE_LIMIT` para proteger la cuota de Groq.

`DISCORD_DM_IDS` debe contener los IDs de Discord autorizados a recibir mensajes directos. En Discord, escribe `id` al bot para obtener tu ID.

## Archivos y espacio de trabajo

Cada usuario tiene un directorio aislado dentro de `WORKSPACE_DIR` (por defecto `orion_workspace`). Los adjuntos se guardan en `inbox/` aunque Orion todavía no tenga un extractor semántico específico para su formato. El agente puede usar `list_files` y `read_file` para texto y ZIP; puede crear archivos con `write_file`, crear binarios a partir de Base64 con `write_binary_file` y empaquetar con `zip_files`. Los archivos creados se devuelven automáticamente por Telegram.

Por seguridad, las rutas no pueden salir del espacio del usuario, los archivos están limitados a 50 MB, crear/sobrescribir, generar binarios y comprimir requieren aprobación humana, y Orion nunca ejecuta automáticamente código, macros, ejecutables o scripts recibidos. Para conservar archivos en Railway, configura `WORKSPACE_DIR=/data/orion_workspace` en el mismo Volume persistente de la base de datos.

Para que Orion pueda revisar Discord cuando se lo pidas, configura `DISCORD_MONITOR_CHANNEL_IDS` con los IDs de canales permitidos, `DISCORD_MONITOR_USER_IDS` para usuarios de Discord y/o `DISCORD_MONITOR_TELEGRAM_IDS` para usuarios de Telegram. La lectura es bajo demanda; no se mantiene una vigilancia permanente.

Para permitir que publique mensajes, configura además `DISCORD_WRITE_CHANNEL_IDS`. Es una lista separada y más restrictiva. Desde Telegram debes indicar el ID del canal; desde Discord puede usar el canal actual. El bot necesita también el permiso **Send Messages**.

Orion puede actuar como moderador controlado. Configura `DISCORD_MODERATOR_USER_IDS` para moderadores que escriben desde Discord, `DISCORD_MODERATOR_TELEGRAM_IDS` para administradores que lo controlan desde Telegram y `DISCORD_MODERATOR_GUILD_IDS` con los servidores permitidos. Necesita los permisos de Discord **Manage Events**, **Moderate Members** y **Manage Messages** según las funciones que uses. Puede crear eventos programados, publicar anuncios, aplicar timeouts y borrar mensajes concretos. Todas estas acciones pasan por aprobación humana y quedan auditadas; no hay borrado masivo, baneos ni cambios de roles automáticos.

Para activar el modo multi-cerebro, define `GROQ_API_KEYS` con 2 o 3 claves de cuentas distintas y `MULTI_BRAIN_SIZE=2` o `MULTI_BRAIN_SIZE=3`. El agente usará `team_reason` cuando pidas una segunda opinión, una revisión profunda o "doble/triple cerebro". Esto consume una llamada por analista y una llamada final de síntesis.

Funciones autónomas disponibles: `remember`/`recall`/`forget` para memoria, `create_task`/`list_tasks`/`complete_task` para tareas, `plan_task` para descomponer objetivos y `audit_log` para consultar acciones. Cada turno queda registrado en `agent_runs` con estado y presupuesto; `/ejecuciones` muestra el historial. `create_automation` registra y ejecuta instrucciones con frecuencias de intervalo como `cada 30m`, `cada 2h` o `cada 1d`; las ejecuciones sobreviven a reinicios porque su estado queda en SQLite.

PydanticAI se activa por defecto para estructurar planes y validar evidencia. Puedes desactivarlo con `PYDANTICAI_ENABLED=false`; LangGraph seguirá funcionando como orquestador durable.

## Railway

Monta un Volume persistente y define `DB_PATH=/data/reminders.db` (o deja que Railway use `RAILWAY_VOLUME_MOUNT_PATH`). El bot debe ejecutarse como **una sola réplica**, porque SQLite y el scheduler no están preparados para varios procesos.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas cubren el parser de recordatorios, el almacén SQLite y el harness (aprobación, rechazo, reanudación tras reinicio y turnos sin supervisión).

## Harness de razonamiento

Orion usa LangGraph para orquestar cada turno: modelo, compuerta de aprobación, herramientas y respuesta final forman un flujo controlado con un máximo de rondas. PydanticAI se usa como capa especializada y opcional: `plan_task` produce un `AgentPlan` tipado y `verify_step` produce un `VerificationResult` tipado. PydanticAI no ejecuta acciones ni puede saltarse el checkpointer o las aprobaciones de LangGraph.

La persistencia de automatizaciones y memoria continúa en SQLite.

### Checkpoints y aprobación humana

Cada paso de un turno (modelo → compuerta → herramientas) se guarda con el checkpointer nativo de LangGraph (`AsyncSqliteSaver`) en `orion_checkpoints.db`, junto a la base principal, así que vive en el mismo Volume de Railway.

- **Aprobación (`interrupt()`).** Las herramientas de `APPROVAL_TOOLS` (por defecto `write_discord_channel` y `create_automation`) pausan el turno antes de ejecutarse. Orion te muestra qué quiere hacer y esperas tu `/aprobar` o `/rechazar` (en Discord, escribe `aprobar` o `rechazar`). La aprobación la da una persona, no el modelo: ya no depende de que el LLM se acuerde de pedirla. Si sigues con otro tema, la acción pendiente se descarta sin ejecutarse. Las automatizaciones corren sin nadie delante, así que ahí esas acciones se rechazan solas.
- **Reanudar (`/reanudar`).** Si el bot se reinicia o cae a mitad de un turno, el último paso completado sigue guardado y `/reanudar` continúa desde ahí. No se reanuda solo, para que nada se ejecute sin que lo decidas. `/estado` muestra si hay algo pendiente.
- **Idempotencia.** Los ids de las llamadas a herramientas ahora se guardan en el checkpoint, así que al reanudar una operación ya terminada devuelve su resultado guardado en vez de repetirse.

`/estado` muestra el modelo, las claves configuradas, el modo de cerebro, la memoria reciente y si quedó un turno pendiente. `/proyecto Orion` activa un contexto de proyecto y `/proyecto off` lo desactiva.

## Seguridad

- Nunca subas `.env` ni tokens al repositorio.
- Usa tokens de GitHub con permisos mínimos y configura `GITHUB_OWNER_IDS`.
- Si el bot será público, usa lista blanca o límite diario.
- Revisa los logs: los errores de herramientas ahora incluyen la traza completa.
