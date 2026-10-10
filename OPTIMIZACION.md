# Diagnóstico de rendimiento de Orion

## Resumen

La lentitud principal está en la ruta de cada mensaje, no en Telegram o Discord:

1. **Una llamada remota a Groq** siempre es necesaria para decidir si responder o usar una herramienta.
2. Cada turno entra en **LangGraph + SQLite checkpointer**, incluso cuando termina con una respuesta sencilla.
3. El modelo puede hacer hasta **8 rondas de herramientas** (`MAX_TOOL_ROUNDS = 8`), multiplicando la latencia.
4. El historial predeterminado es de **20 mensajes** y el prompt del sistema es largo; esto aumenta el tiempo de procesamiento y los tokens.
5. GitHub, clima, búsqueda web y Discord crean un `aiohttp.ClientSession` nuevo por petición.
6. `AssistantStore` abre una conexión SQLite síncrona nueva para muchas operaciones. Bajo concurrencia puede bloquear el event loop.

No se pudo hacer un benchmark real contra Groq porque el ZIP no contiene credenciales ni un entorno de producción. Las pruebas tampoco se ejecutaron porque este sandbox no tiene `pytest` instalado; el código sí incluye pruebas.

## Qué cambiar primero

### 1. Usar un modelo rápido para conversación

El valor predeterminado actual es `openai/gpt-oss-120b` (`config.py:186`). Para chat cotidiano, configura en `.env` un modelo rápido disponible en tu cuenta de Groq. Mantén el modelo grande solo para tareas complejas o crea un comando/modo explícito para cambiarlo.

Ejemplo conceptual:

```env
GROQ_MODEL=<modelo-de-chat-rápido-disponible-en-tu-cuenta>
MULTI_BRAIN_SIZE=0
```

El modo multi-cerebro hace 2 o 3 llamadas de análisis en paralelo **más una llamada de síntesis** (`groq_service.py:447-500`). Es útil, pero por definición será bastante más lento.

### 2. Bajar el máximo de rondas de herramientas

En `services/groq_service.py:90-92`:

```python
MAX_TOOL_ROUNDS = 3
```

O usa 4 si haces investigaciones largas. Ocho rondas es un límite de seguridad, pero permite conversaciones donde el modelo encadena demasiadas llamadas. Para una respuesta simple, normalmente debería ser una ronda como máximo.

### 3. Reducir el historial enviado

El valor predeterminado es `MAX_HISTORY=20` (`config.py:109-111`). Empieza con:

```env
MAX_HISTORY=8
```

Cada turno guarda solo pregunta y respuesta (`groq_service.py:774-778`), lo cual es bueno, pero esas 20 entradas siguen viajando al modelo en cada llamada.

### 4. Desactivar checkpoints para turnos que no necesitan reanudación

El checkpointer de LangGraph se usa para aprobación humana y recuperación tras reinicios, pero añade lecturas/escrituras por nodo. Actualmente se inicializa siempre (`main.py:79-102`) y el grafo se compila siempre con él (`reasoning_harness.py:91-99`).

La optimización de mayor impacto arquitectónico es tener dos rutas:

- **Ruta rápida:** chat normal sin checkpointer; conserva memoria final en `AssistantStore`/memoria en proceso.
- **Ruta durable:** LangGraph + checkpoint solo cuando haya una herramienta que requiera aprobación o cuando el usuario use `/reanudar`.

No recomiendo eliminarlo sin implementar primero esa separación, porque rompería aprobaciones y reanudaciones.

### 5. Reutilizar sesiones HTTP

`services/github.py:36`, `services/discord_monitor.py` y varias herramientas en `services/tools.py` crean y cierran un `ClientSession` en cada llamada. Eso repite conexión TCP/TLS y DNS.

Crea una sesión compartida al arrancar y ciérrala en el `finally` de `main()`. En el caso de GitHub, además puedes paralelizar las tres consultas independientes de `overview()` (`info`, `languages`, `contents`) con `asyncio.gather()`; actualmente se ejecutan en serie (`github.py:90-107`).

### 6. Evitar SQLite síncrono en el event loop

`AssistantStore` usa `sqlite3.connect()` en cada método (`services/assistant_store.py:71-312`). Con un solo usuario probablemente no sea el problema principal, pero con varios mensajes concurrentes sí puede congelar el bot brevemente.

Opciones, en orden de esfuerzo:

1. Envolver operaciones del store con `await asyncio.to_thread(...)`.
2. Migrar `AssistantStore` a `aiosqlite` y mantener una conexión reutilizable.
3. A medio plazo, usar PostgreSQL si habrá varias réplicas; el README correctamente advierte que SQLite requiere una sola réplica.

## Ajuste inicial recomendado

Aplica primero estos cambios de configuración, sin modificar la arquitectura:

```env
MAX_HISTORY=8
MULTI_BRAIN_SIZE=0
# Usa aquí el modelo rápido que tengas habilitado en Groq
GROQ_MODEL=<modelo-rápido>
```

Y cambia:

```python
# services/groq_service.py
MAX_TOOL_ROUNDS = 3
```

Esto debería reducir tokens y evitar cadenas largas de herramientas. No reduce el tiempo de una única llamada de Groq tanto como cambiar a un modelo más rápido, pero sí mejora bastante los turnos con herramientas.

## Cómo medir antes y después

Añade medición alrededor de estas etapas:

- recepción del mensaje;
- construcción del contexto;
- cada llamada a `GroqService._complete()`;
- cada herramienta (`run_tool()`);
- cada ronda del harness;
- envío de la respuesta.

Registra `time.perf_counter()` y un `request_id`. Mide al menos 30 mensajes de cada tipo:

| Caso | Qué medir |
|---|---|
| Chat simple | tiempo total y duración de Groq |
| Clima/hora | Groq + herramienta + Groq final |
| Búsqueda web | número de rondas y tiempo de red |
| GitHub | número de peticiones HTTP y tiempo total |
| Documento | extracción local + Groq |
| Aprobación | tiempo hasta la pausa y hasta la reanudación |

La métrica que más importa es p95, no solo el promedio.

## Cosas que no son el problema principal

- `split_text()` y el envío en trozos son operaciones locales pequeñas.
- El middleware de rate limit solo hace operaciones en memoria.
- El caché del esquema de Notion ya evita repetir la consulta inicial.
- Las herramientas del harness sí se ejecutan en paralelo con `asyncio.gather()` cuando el modelo solicita varias (`reasoning_harness.py:245-247`).

## Orden recomendado de trabajo

1. Cambiar modelo a uno rápido y `MAX_TOOL_ROUNDS=3`.
2. Bajar `MAX_HISTORY` a 8 y mantener multi-cerebro apagado por defecto.
3. Añadir métricas de latencia por etapa.
4. Reutilizar `aiohttp.ClientSession` y paralelizar `GitHubService.overview()`.
5. Separar ruta rápida sin checkpoint de la ruta durable con aprobación/reanudación.
6. Migrar el store síncrono a operaciones asíncronas si hay concurrencia real.

Con solo los pasos 1–2 deberías notar una mejora inmediata; los pasos 4–6 son los que mejoran la latencia bajo carga y la consistencia del bot.
