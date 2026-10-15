# Integración de los tres módulos nuevos en Orion

## Entrega de archivos creados

El harness conserva las rutas `ARCHIVO_CREADO:` emitidas por las herramientas y las añade a la respuesta final si el modelo las omitió. El handler de Telegram las usa para enviar el archivo real mediante `answer_document`, incluyendo rutas con espacios.

## Ejecución autónoma y riesgos

Orion ya no pausa las acciones ni solicita aprobación. Antes de ejecutar una acción con efectos (por ejemplo, escribir archivos, crear recordatorios, publicar en Discord o enviar mensajes), envía un aviso con la acción y sus riesgos. Después continúa automáticamente y registra el resultado en la auditoría.

## Archivos añadidos

| Archivo | Qué hace |
|---|---|
| `services/semantic_memory.py` | Memoria con embeddings (cosine similarity) sobre SQLite |
| `services/agent_runner.py` | Orquestador de AgentRun: planifica → ejecuta → verifica |
| `services/sandbox.py` | Sandbox E2B para ejecutar código Python/bash de forma segura |
| `services/tools_addon.py` | 7 tools nuevas listas para añadir a `TOOLS` |
| `handlers/memory.py` | Comandos `/memoria` para Telegram |

---

## 1. requirements.txt

Añade estas líneas:

```
e2b-code-interpreter>=1.0; python_version >= "3.11"
```

> `openai>=1.0` ya lo tienes — lo usamos para embeddings.  
> E2B es opcional: si no hay `E2B_API_KEY`, `run_code` devuelve un aviso amigable.

---

## 2. .env / Railway Variables

```
E2B_API_KEY=tu_clave_de_e2b.dev     # https://e2b.dev → crea cuenta gratis
```

La memoria semántica usa **BM25 puro** — no necesita ninguna clave de API adicional.

---

## 3. services/tools.py

### 3a. Imports (al final del bloque de imports)
```python
from services.tools_addon import ADDON_TOOLS
```

### 3b. ToolContext — añade dos campos
```python
@dataclass(frozen=True)
class ToolContext:
    ...  # campos existentes sin cambiar
    semantic_memory: "SemanticMemory | None" = None   # ← nuevo
    agent_runner: "AgentRunner | None" = None          # ← nuevo
```

### 3c. Al final de la lista TOOLS
```python
TOOLS: list[Tool] = [
    ...  # todas las tools existentes
    *ADDON_TOOLS,   # ← añade esta línea al final
]
```

---

## 4. services/groq_service.py

### 4a. Imports
```python
from services.semantic_memory import SemanticMemory
from services.agent_runner import AgentRunner
```

### 4b. Inicialización en GroqService.__init__
```python
class GroqService:
    def __init__(self, settings: Settings, store: AssistantStore) -> None:
        ...  # código existente

        # Memoria semántica BM25 — sin API externa
        self.semantic_memory = SemanticMemory(settings.db_path)

        # AgentRunner
        self.agent_runner = AgentRunner(
            store=store,
            tool_executor=self._tool_executor,   # ver 4c
            llm=self._llm_fn,                    # ver 4c
            available_tools=[t.name for t in TOOLS],
        )
```

### 4c. Funciones auxiliares para AgentRunner
```python
async def _tool_executor(self, tool_name: str, args: dict) -> str:
    """Ejecuta una tool por nombre — para que AgentRunner pueda llamar tools."""
    tool = next((t for t in TOOLS if t.name == tool_name), None)
    if tool is None:
        return f"Herramienta '{tool_name}' no encontrada."
    ctx = self._build_ctx(self._default_user_id)  # o el user_id del run
    return await tool.handler(ctx, args)

async def _llm_fn(self, system: str, user: str) -> str:
    """Llamada mínima al LLM para planificador y verificador."""
    resp = await self._client.chat.completions.create(
        model=self._model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=1000,
        temperature=0,
    )
    return resp.choices[0].message.content or ""
```

### 4d. En el método que construye ToolContext, añade los nuevos campos
```python
ctx = ToolContext(
    chat_id=chat_id,
    user_id=user_id,
    ...  # campos existentes
    semantic_memory=self.semantic_memory,   # ← nuevo
    agent_runner=self.agent_runner,         # ← nuevo
)
```

---

## 5. handlers/start.py — añade /memoria al menú

```python
COMMANDS = [
    ...  # comandos existentes
    ("memoria", "Gestiona tu memoria semántica: listar, buscar, borrar"),
]
```

---

## 6. main.py — registra el router y pasa el SemanticMemory

```python
from handlers.memory import router as memory_router

# Donde incluyes routers:
dp.include_router(memory_router)

# Donde defines dp["..."] para inyección:
dp["semantic_memory"] = ai.semantic_memory   # ai = tu instancia de GroqService
```

---

## 7. Limpieza periódica de recuerdos expirados

En `main.py` o en el scheduler de automatizaciones, añade:

```python
async def _purge_expired_memories():
    if ai.semantic_memory:
        n = ai.semantic_memory.purge_expired()
        if n:
            logger.info("Purgados %d recuerdos semánticos expirados", n)

# En el scheduler (APScheduler):
scheduler.add_job(_purge_expired_memories, "interval", hours=24)
```

---

## Resumen de tools nuevas

| Tool | Cuándo la usa el agente |
|---|---|
| `semantic_remember` | Para guardar preferencias, datos de personas, objetivos |
| `semantic_recall` | Antes de responder sobre temas personales del usuario |
| `semantic_forget` | Para eliminar información obsoleta o incorrecta |
| `semantic_list` | Para mostrar al usuario qué recuerda Orion |
| `run_code` | Para ejecutar cálculos, scripts o transformaciones de datos |
| `create_agent_run` | Para tareas complejas de múltiples pasos |
| `get_agent_run` | Para consultar el progreso de una ejecución |

---

## Flujo de uso típico

```
Usuario: "Recuerda que prefiero respuestas cortas y en español"
  → semantic_remember(content="Prefiere respuestas cortas en español", kind="preferencia")

Usuario: "¿Cómo me gusta que respondas?"
  → semantic_recall(query="preferencias de respuesta")
  → Orion responde usando los recuerdos relevantes

Usuario: "Analiza estas ventas y dame un resumen"
  → create_agent_run(objective="Analizar ventas y generar resumen")
  → El agente planifica: [leer archivo, calcular totales, escribir resumen]
  → Ejecuta cada paso con run_code + write_file
  → Verifica y reporta

Usuario: "Calcula la distribución de mis gastos del mes"
  → run_code(code="gastos = {...}; print(sum(gastos.values()))", language="python")
```
