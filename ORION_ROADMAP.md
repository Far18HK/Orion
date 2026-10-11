# Orion: hoja de ruta hacia un asistente altamente autónomo

## Resumen ejecutivo

Orion ya tiene una base bastante más avanzada que un bot de chat convencional:

- Telegram y Discord.
- Function calling con herramientas de web, clima, hora y calculadora.
- Recordatorios con zona horaria.
- Memoria permanente, tareas, proyectos y auditoría en SQLite.
- Notion, GitHub de solo lectura y lectura/publicación controlada en Discord.
- LangGraph con checkpoints, idempotencia y aprobación humana.
- Automatizaciones periódicas y modo multi-cerebro.

La principal limitación no es que falten más modelos. Falta convertir las capacidades existentes en un **ciclo autónomo completo**:

> objetivo → planificación → ejecución → observación → verificación → aprendizaje → reporte

Actualmente Orion puede decidir y llamar herramientas, pero muchas acciones no tienen una representación durable como objetivo, dependencia, resultado esperado, evidencia de éxito o política de permisos suficientemente granular.

---

## Hallazgos del código actual

### Lo que está bien resuelto

1. **Separación razonable de responsabilidades**: handlers, servicios, herramientas y persistencia están separados.
2. **Aprobación fuera del modelo**: el harness usa `interrupt()` y no depende de que el LLM recuerde pedir permiso.
3. **Idempotencia básica**: las llamadas de herramientas se registran en `operations` para evitar repetir una operación al reanudar.
4. **Defensa contra algunos riesgos**: lista blanca para usuarios, GitHub, Discord y canales; comprobación inicial contra SSRF; límites de tamaño y rondas.
5. **Persistencia real**: memoria, tareas, automatizaciones, auditoría y checkpoints sobreviven reinicios si SQLite está en un volumen persistente.
6. **Herramientas paralelas**: el harness ejecuta llamadas independientes con `asyncio.gather()`.

### Brechas que impiden la autonomía real

1. **Las automatizaciones son solo temporizadores**. No hay disparadores por evento, webhook, cambio en GitHub, nuevo mensaje, precio, correo o condición.
2. **`plan_task` es casi solo una plantilla de texto**. No crea pasos persistentes, dependencias, estados, reintentos ni criterios de aceptación.
3. **No existe un verificador de resultados**. El modelo recibe la salida de una herramienta, pero no hay una etapa obligatoria que compruebe que el objetivo se cumplió.
4. **El control de permisos es principalmente por nombre de herramienta** (`APPROVAL_TOOLS`). Eso es menos preciso que aprobar una acción concreta con payload, alcance, caducidad y presupuesto.
5. **La autonomía no tiene presupuesto ni límites por objetivo**: faltan límites de tiempo, número de llamadas, coste estimado, dominios permitidos y acciones por hora.
6. **No hay un lock/lease distribuido para automatizaciones**. Con más de un proceso podrían ejecutarse dos veces; SQLite además obliga a una sola réplica.
7. **El contexto está centrado en conversación**, no en un modelo de usuario y proyecto con preferencias, objetivos, entidades y hechos con fuente/fecha/confianza.
8. **GitHub es solo lectura**. Orion puede analizar repositorios, pero no abrir issues/PRs, proponer parches, ejecutar pruebas ni entregar un diff revisable.
9. **La observabilidad es insuficiente para un agente**. Hay auditoría de acciones, pero faltan trazas por ejecución, latencias, tokens, errores, coste, motivo de decisión y resultado verificado.
10. **La seguridad de herramientas necesita una política uniforme**. Hay acciones sensibles como borrar notas, editar notas, enviar DMs o cancelar recordatorios que deberían pasar por la misma compuerta declarativa.
11. **El scheduler crea un `Bot` nuevo por ejecución** y varias integraciones crean sesiones HTTP por petición; esto aumenta latencia y consumo.
12. **La prueba automatizada está incompleta en el entorno inspeccionado**: `compileall` pasa, pero no se pudo ejecutar pytest porque no está instalado en el sandbox.

---

# Prioridad P0: convertirlo en un agente confiable

## 1. Crear un `AgentRun` persistente

Añadir una entidad de ejecución independiente de la conversación:

```text
agent_runs
- id
- user_id
- project_id
- objective
- status: queued | planning | running | waiting_approval | verifying | done | failed | cancelled
- parent_run_id
- current_step_id
- budget_json
- started_at
- deadline_at
- last_heartbeat
- final_result
- failure_reason
```

Cada petición compleja debería crear una ejecución. El chat solo sería la interfaz para consultar, pausar, aprobar o cancelar esa ejecución.

Comandos sugeridos:

- `/objetivo <texto>`: crea una ejecución autónoma.
- `/ejecuciones`: lista ejecuciones activas y pausadas.
- `/pausar <id>` y `/cancelar <id>`.
- `/reintentar <id>`.
- `/aprobar <id>`.
- `/resultado <id>`.

Esto evita que una tarea larga dependa de que el usuario mantenga una conversación abierta.

## 2. Reemplazar `plan_task` por un plan ejecutable

El plan debe almacenarse como pasos reales:

```text
agent_steps
- id
- run_id
- position
- title
- description
- depends_on
- tool_name
- arguments_json
- status
- attempts
- max_attempts
- expected_result
- evidence_json
- started_at
- finished_at
- error
```

Flujo recomendado:

1. El modelo propone un plan estructurado.
2. Orion valida que las herramientas y argumentos existan.
3. Se calculan riesgos y permisos.
4. Se ejecutan pasos desbloqueados.
5. Se verifica cada paso.
6. Se replanifica solo si falló una precondición o cambió el contexto.
7. Se informa al usuario con progreso y evidencia.

El modelo no debería poder inventar el estado de un paso; solo el runtime puede marcarlo como completado después de una respuesta válida del adaptador o del verificador.

## 3. Añadir una fase explícita de verificación

Para cada acción, definir:

- precondiciones;
- operación;
- resultado esperado;
- comprobación posterior;
- acción compensatoria si es reversible;
- qué evidencia se guarda.

Ejemplos:

- Crear una nota: crear → buscar por id → comprobar título/contenido.
- Enviar Discord: enviar → comprobar HTTP/ID del mensaje → guardar message_id.
- Crear automatización: insertar → volver a leer → verificar `next_run`.
- Cambiar un archivo: escribir → ejecutar pruebas → inspeccionar diff.

La respuesta final debe diferenciar: **hecho**, **parcial**, **bloqueado** y **falló**.

## 4. Unificar la política de permisos

En lugar de una lista plana de nombres, definir metadatos por herramienta:

```python
ToolPolicy(
    risk="medium",
    effect="external_write",
    reversible=True,
    approval="always",  # never | first_time | always | per_payload
    scopes=["discord:channel:123"],
    max_calls=3,
    allowed_in_automation=False,
    expires_in=300,
)
```

Acciones que deberían ser aprobables por defecto:

- publicar o enviar mensajes externos;
- borrar o editar notas;
- cancelar recordatorios;
- crear automatizaciones;
- abrir issues o PRs;
- ejecutar comandos o modificar archivos;
- enviar correo o hacer compras;
- cambiar configuración, credenciales o permisos.

El usuario debería aprobar el **payload exacto**, no solo el nombre de la herramienta.

## 5. Introducir presupuestos y kill switch

Cada ejecución autónoma necesita límites:

- tiempo máximo;
- número máximo de pasos;
- llamadas máximas por herramienta;
- tamaño máximo de contexto;
- dominios permitidos;
- coste o tokens máximos;
- cantidad máxima de mensajes externos;
- horario permitido.

Añadir un interruptor global y por proyecto:

- `/autonomia off` detiene nuevas acciones.
- `/autonomia estado` muestra límites y ejecuciones.
- `/detener todo` cancela ejecuciones no terminales.

---

# Prioridad P1: darle iniciativa útil

## 6. Pasar de cron a automatizaciones por eventos

Conservar `cada 30m`, pero añadir eventos:

- GitHub: nuevo issue, PR, fallo de workflow, release.
- Discord: mensaje que coincide con una regla en canales autorizados.
- Webhook genérico firmado.
- Cambio en una URL o API.
- Nuevo archivo en una carpeta vigilada.
- Hora/fecha y condiciones: “si mañana llueve, avísame”.
- Estado de tareas: “si llevo 3 días sin avanzar, recuérdamelo”.

Modelo:

```text
triggers
- id
- type
- config_json
- secret_hash
- enabled
- last_event_id
- dedupe_key
```

El trigger no debe ejecutar directamente. Debe crear una `AgentRun` con deduplicación y aplicar la política de permisos.

## 7. Memoria semántica y memoria con caducidad

La memoria actual es útil, pero basada en texto y coincidencias simples. Añadir:

- tipo: preferencia, persona, proyecto, objetivo, decisión, hecho;
- fuente: usuario, documento, herramienta;
- confianza;
- fecha de observación;
- fecha de caducidad;
- sensibilidad;
- embeddings para recuperar conceptos relacionados;
- detección de contradicciones;
- pantalla de revisión y borrado.

Reglas importantes:

- No guardar información sensible automáticamente.
- No convertir una inferencia del modelo en un hecho.
- Preguntar cuando dos memorias se contradicen.
- Permitir “olvida todo lo relacionado con X”.

## 8. Contexto de proyecto de verdad

`/proyecto` debería activar un espacio con:

- objetivo y definición de terminado;
- archivos y URLs relevantes;
- tareas abiertas;
- decisiones tomadas;
- restricciones;
- integrantes y permisos;
- memoria propia del proyecto;
- presupuesto y herramientas autorizadas.

Así Orion podría mantener proyectos como “Orion”, “trabajo”, “estudio” o “casa” sin mezclar instrucciones ni datos.

## 9. Bandeja de entrada y gestión de prioridades

Añadir una capa de triage:

- capturar mensajes, enlaces, notas de voz y documentos;
- clasificar por proyecto, prioridad y fecha límite;
- convertir elementos en tareas;
- detectar duplicados;
- proponer el siguiente paso;
- generar un resumen diario y uno semanal;
- detectar tareas atascadas o vencidas.

La autonomía útil no es hacer cosas al azar; es mantener el sistema de trabajo ordenado y pedir intervención solo cuando hace falta.

## 10. Notificaciones inteligentes

En vez de enviar una notificación por cada ejecución:

- agrupar resultados;
- respetar horarios silenciosos;
- usar prioridad crítica/normal/baja;
- deduplicar avisos;
- permitir canal Telegram, Discord, correo o webhook;
- incluir resumen, evidencia y botones de acción.

---

# Prioridad P1/P2: permitir que actúe sobre el mundo

## 11. GitHub como agente de desarrollo, con sandbox

Evolución segura en etapas:

1. Crear branches temporales.
2. Editar archivos en un workspace aislado.
3. Ejecutar tests/lint en contenedor sin secretos.
4. Generar diff y resumen.
5. Pedir aprobación antes de crear PR.
6. Crear issue/PR con etiquetas y descripción.
7. Esperar CI y corregir fallos dentro de un límite.

Nunca dar acceso directo a producción ni permitir comandos arbitrarios sin sandbox, allowlist y límites.

## 12. Adaptadores adicionales de alto valor

Por orden práctico:

- Google Calendar: consultar disponibilidad y crear eventos con aprobación.
- Gmail: clasificar y preparar borradores; envío siempre aprobable.
- Google Drive: buscar y resumir documentos.
- Slack/WhatsApp, si la integración es legal y técnicamente disponible.
- Trello/Linear/Jira: sincronizar tareas y estados.
- RSS y newsletters.
- Home Assistant: leer sensores; controlar dispositivos con políticas estrictas.
- APIs financieras o de compras: solo lectura inicialmente; cualquier transacción requiere aprobación explícita.

Cada integración debe ser un adaptador con permisos y pruebas, no lógica especial dentro de `tools.py`.

## 13. Voz y multimodalidad bidireccional

Orion ya recibe fotos, audio y documentos. El siguiente nivel sería:

- responder por voz cuando se solicite;
- mantener contexto de una imagen/documento dentro de una ejecución;
- OCR con citas de página;
- extracción estructurada de facturas, tablas y formularios;
- generar archivos de salida: CSV, PDF, resúmenes y diffs;
- aceptar mensajes “hazlo con este archivo” y enlazar el artefacto al objetivo.

---

# Prioridad P2: robustez de producción

## 14. Cambiar SQLite directo por una capa asíncrona y migrable

`AssistantStore` abre conexiones síncronas en muchos métodos. Para un usuario funciona, pero puede bloquear el event loop bajo concurrencia.

Plan:

1. Centralizar transacciones y añadir índices.
2. Usar `aiosqlite` con una conexión reutilizable o ejecutar operaciones síncronas con `asyncio.to_thread`.
3. Añadir migraciones versionadas.
4. Preparar PostgreSQL si habrá varias réplicas.
5. Implementar locks/leases para scheduler y ejecuciones.

## 15. Reutilizar sesiones HTTP y añadir resiliencia

- Una `aiohttp.ClientSession` compartida por proceso.
- Pooling y límites de concurrencia.
- Retry con backoff y jitter solo para errores transitorios.
- Circuit breaker por proveedor.
- Cache con TTL según herramienta.
- Idempotency keys también para adaptadores externos.

## 16. Observabilidad de agente

Registrar por ejecución y por paso:

- `run_id`, `step_id`, `request_id`;
- latencia por etapa;
- modelo y proveedor;
- tokens y coste estimado;
- herramienta, argumentos saneados y resultado resumido;
- estado antes/después;
- aprobaciones y usuario que las concedió;
- reintentos;
- evidencia de verificación.

Añadir `/salud` con estado del proveedor, scheduler, base de datos y últimas fallas.

## 17. Evaluaciones automáticas

Crear un conjunto de escenarios reproducibles:

- solicitud simple sin herramienta;
- herramienta correcta;
- dos herramientas en paralelo;
- acción sensible y aprobación;
- rechazo y no reintento;
- reinicio a mitad de ejecución;
- timeout y reintento;
- prompt injection en una página web;
- usuario sin permisos;
- automatización duplicada;
- contradicción en memoria;
- ejecución que supera presupuesto.

Medir precisión de herramienta, tasa de acciones no autorizadas, tiempo p95, porcentaje de tareas verificadas y tasa de recuperación.

---

# Nuevas herramientas recomendadas

## Núcleo de agente

- `create_agent_run`
- `get_agent_run`
- `list_agent_runs`
- `pause_agent_run`
- `cancel_agent_run`
- `resume_agent_run`
- `get_run_progress`
- `replan_run`
- `verify_step`

## Productividad

- `create_project`
- `update_project_goal`
- `list_blocked_tasks`
- `set_task_due_date`
- `set_task_dependency`
- `daily_briefing`
- `weekly_review`
- `triage_inbox`

## Eventos

- `create_trigger`
- `list_triggers`
- `disable_trigger`
- `process_webhook_event`
- `check_url_change`
- `watch_github_workflow`

## Desarrollo

- `create_workspace`
- `apply_patch`
- `run_tests`
- `get_diff`
- `create_branch`
- `create_issue`
- `create_pull_request`

Todas las herramientas de escritura o ejecución deben declarar política de riesgo, permisos, límites, verificación y posibilidad de compensación.

---

# Arquitectura objetivo

```text
Telegram / Discord / Webhook / Scheduler
                  |
            Event Router
                  |
        Auth + Policy + Budget Gate
                  |
          Agent Run Coordinator
                  |
       Planner <-> Executor <-> Verifier
                  |
       Tool Adapters / Sandboxes / APIs
                  |
       Evidence + Audit + Memory + Metrics
                  |
       Notifier: progreso, aprobación, resumen
```

El LLM debe proponer decisiones, planes y argumentos. El runtime debe controlar:

- qué puede ejecutarse;
- con qué permisos;
- cuántas veces;
- durante cuánto tiempo;
- con qué datos;
- cómo se verifica;
- cómo se detiene.

---

# Plan de implementación recomendado

## Fase 1: seguridad y núcleo durable

1. Añadir `agent_runs` y `agent_steps`.
2. Convertir `plan_task` en plan estructurado persistente.
3. Añadir estados, cancelación, deadlines y presupuesto.
4. Unificar la política de aprobación para todas las herramientas con efectos.
5. Añadir verificación posterior a las acciones principales.
6. Escribir pruebas de reinicio, duplicación, rechazo y timeout.

## Fase 2: autonomía útil

1. Añadir triggers por webhook y GitHub.
2. Crear resumen diario/semanal y triage de tareas.
3. Mejorar memoria con tipo, fuente, confianza y caducidad.
4. Añadir prioridades, horarios silenciosos y notificaciones agrupadas.
5. Añadir `/ejecuciones`, `/pausar`, `/cancelar` y `/salud`.

## Fase 3: acciones de alto valor

1. GitHub con branch, sandbox, pruebas y diff.
2. Calendar/Drive/Correo con borradores y aprobación de envío.
3. Artefactos de salida: PDF, CSV, reportes y archivos modificados.
4. Dashboard web para ver ejecuciones, permisos, logs y memoria.

## Fase 4: escala

1. Migrar persistencia a PostgreSQL si hay varias réplicas.
2. Worker separado para ejecuciones largas.
3. Cola de trabajos con leases y reintentos.
4. Métricas y evaluación continua.
5. Vault de secretos y rotación de credenciales.

---

# Mi recomendación concreta

No empezaría añadiendo veinte integraciones. Primero implementaría tres piezas:

1. **AgentRun + pasos persistentes**.
2. **Policy/Budget Gate** para aprobar y limitar acciones.
3. **Verifier** que obligue a demostrar que cada paso funcionó.

Con esas tres piezas Orion dejaría de ser solo “un chat que puede usar herramientas” y se convertiría en un agente que puede recibir un objetivo, trabajar por etapas, recuperarse de reinicios, detenerse ante riesgos y rendir cuentas con evidencia.

Después añadiría eventos de GitHub/webhook y un sandbox de desarrollo. Ese conjunto produciría un salto de capacidad mucho mayor que cambiar de modelo o activar más cerebros.
