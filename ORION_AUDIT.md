# Auditoría técnica de Orion

## Estado revisado

La revisión cubrió compilación, 47 pruebas automatizadas, políticas de aprobación, Dispatcher, Discord, espacio de archivos, checkpoints y herramientas. La compilación y la suite terminan correctamente. Ruff también quedó limpio en las categorías de errores, imports y bugs básicos (`F`, `I`, `B`); permanecen algunos avisos heredados de estilo y simplificación (`E501`, `SIM`, `ASYNC`) que no cambian el comportamiento.

## Bugs corregidos en esta auditoría

### 1. Descargas de Discord con riesgo de consumo excesivo de memoria

El frente de Discord leía un adjunto completo antes de comprobar si superaba 50 MB. Ahora comprueba `Content-Length` cuando está disponible y descarga por fragmentos, deteniéndose al superar el límite.

### 2. ZIP que se incluía a sí mismo

Crear `entrega.zip` incluyendo `entrega.zip` podía producir un archivo corrupto o resultados inconsistentes. Ahora el espacio de trabajo rechaza explícitamente esa colisión y tiene una prueba de regresión.

### 3. Hash de archivos con lectura completa en memoria

`read_file` calculaba el SHA-256 leyendo todo el archivo de una vez. Ahora lo calcula en bloques de 1 MiB, reduciendo picos de memoria con archivos grandes.

### 4. Acceso global de Discord sin alcance por servidor

El modo `DISCORD_*_ALL_CHANNELS=true` podía abarcar cualquier servidor donde el bot tuviera acceso. Se añadió `DISCORD_ALL_CHANNEL_GUILD_IDS` para limitar ese modo a servidores concretos. Discord continúa aplicando sus permisos reales de rol y devuelve 403 cuando corresponde.

### 5. Entrega de archivos bloqueando el event loop

La comprobación de existencia y tamaño de archivos creados se movió a `asyncio.to_thread`, para que el acceso al disco no bloquee el bot mientras atiende otros mensajes.

## Configuración nueva

```env
# Opcional: limita ALL_CHANNELS a servidores concretos
DISCORD_ALL_CHANNEL_GUILD_IDS=123456789012345678
```

Si se deja vacío, se conserva el comportamiento global anterior.

## Riesgos que no son bugs actuales, pero conviene vigilar

| Área | Riesgo | Recomendación |
|---|---|---|
| SQLite | Una sola réplica sigue siendo necesaria | Mantener un único proceso o migrar el estado a PostgreSQL/Redis |
| Archivos | El espacio puede crecer indefinidamente | Añadir cuota por usuario, limpieza por antigüedad y `/archivos borrar` |
| Discord | `DISCORD_WRITE_ALL_CHANNELS` puede publicar demasiado | Mantenerlo en `false` y usar aprobación + lista de servidores |
| Automatizaciones | Un flujo puede repetir una acción externa | Usar idempotency keys, cooldown y máximo de ejecuciones |
| Modelos | El proveedor puede devolver tool calls inválidas | Mantener reintentos, límites y telemetría por modelo |
| Multimedia | MP4 y formatos especializados no tienen extractor universal | Añadir FFmpeg, análisis por tipo y jobs en segundo plano |
| Privacidad | Logs/auditoría pueden contener contenido sensible | Redactar tokens, URLs privadas y datos personales |

## Mejoras recomendadas por prioridad

### Prioridad alta

1. Añadir cuotas de almacenamiento por usuario y limpieza automática de archivos antiguos.
2. Implementar un registro de métricas: latencia, tokens, errores, reintentos, herramientas y agente seleccionado.
3. Añadir pruebas de integración con respuestas simuladas de Discord, Telegram y proveedores LLM.
4. Separar trabajos largos en una cola persistente para que transcribir, analizar ZIPs grandes o procesar multimedia no bloquee conversaciones.
5. Añadir rotación y redacción de logs antes de desplegar Orion públicamente.

### Prioridad media

1. Añadir extracción de MP4 mediante FFmpeg: duración, códecs, pistas, miniaturas y audio transcribible.
2. Añadir análisis seguro de proyectos ZIP con detección de archivos peligrosos, symlinks y tamaños comprimidos excesivos.
3. Añadir `/archivos`, `/archivos listar`, `/archivos borrar` y `/archivos descargar`.
4. Crear una tabla de permisos por usuario, servidor y agente en vez de depender solo de variables de entorno.
5. Añadir un modo de previsualización: Orion prepara un diff o plan y no escribe hasta recibir aprobación.

### Prioridad baja

1. Añadir PostgreSQL cuando existan varias réplicas o alta concurrencia.
2. Añadir un panel web de estado y ejecuciones.
3. Añadir exportación/importación de memoria y configuraciones.
4. Añadir agentes especializados adicionales solo cuando una tarea tenga herramientas y límites propios.
5. Migrar avisos heredados de estilo de Ruff a una pasada de formato independiente.

## Recomendación final

El núcleo actual es razonable para un bot personal: un solo proceso, SQLite persistente, LangGraph para checkpoints y aprobaciones, Dispatcher por perfiles y workspace aislado. No conviene añadir más servicios todavía. El siguiente incremento con mejor relación valor/complejidad sería **cuotas de archivos + métricas + cola para trabajos largos**, antes de integrar n8n o separar procesos.
