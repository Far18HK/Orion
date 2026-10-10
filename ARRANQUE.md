# Arranque de Orion con OpenRouter + Groq

## 1. Requisitos

- Python 3.11 o superior.
- Un token de Telegram creado con BotFather.
- Una clave de OpenRouter.
- Una clave de Groq.

OpenRouter será el proveedor principal para chat y herramientas de texto. Groq quedará como respaldo automático y seguirá procesando imágenes y audios.

## 2. Instalar

Desde la carpeta del proyecto:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## 3. Crear el archivo `.env`

```bash
cp .env.example .env
```

Edita `.env` y completa al menos:

```env
TELEGRAM_TOKEN=pega_aqui_el_token_de_BotFather
GROQ_API_KEY=pega_aqui_tu_clave_de_Groq
OPENROUTER_API_KEY=pega_aqui_tu_clave_de_OpenRouter
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
GROQ_MODEL=openai/gpt-oss-120b
MAX_HISTORY=8
MULTI_BRAIN_SIZE=0
```

El límite de rondas de herramientas está fijado en 3 para reducir la latencia.

Para probarlo en privado, recomienda completar también tu ID de Telegram:

```env
ALLOWED_USER_IDS=123456789
```

Puedes obtenerlo enviando `/id` al bot después de arrancarlo. Si todavía no sabes tu ID, deja esa variable vacía temporalmente y configura la lista después.

## 4. Arrancar

```bash
. .venv/bin/activate
python main.py
```

Si todo está bien, verás un log parecido a:

```text
Bot iniciado con el modelo meta-llama/llama-3.3-70b-instruct:free (OpenRouter)
```

La integración selecciona OpenRouter automáticamente cuando `OPENROUTER_API_KEY` está presente.

## 5. Probar el fallback

1. Envía una pregunta sencilla al bot.
2. Revisa los logs: las respuestas normales salen por OpenRouter.
3. Si OpenRouter falla o devuelve un error transitorio, Orion registra el fallback y repite la petición en Groq.
4. Envía una imagen o una nota de voz: esas funciones siguen usando Groq.

No pongas ambas claves en un mensaje ni las subas a GitHub. El archivo `.env` está ignorado por Git en el proyecto.

## 6. Railway

Configura estas variables en el servicio:

```text
TELEGRAM_TOKEN
GROQ_API_KEY
OPENROUTER_API_KEY
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
GROQ_MODEL=openai/gpt-oss-120b
MAX_HISTORY=8
MULTI_BRAIN_SIZE=0
```

Mantén una sola réplica y adjunta un Volume persistente para `DB_PATH`, tal como se explica en el README. Después de guardar las variables, haz un nuevo deploy.

## 7. Si no tienes OpenRouter todavía

Puedes arrancar solo con Groq dejando `OPENROUTER_API_KEY` vacío. Orion conserva el comportamiento anterior y no intenta conectarse a OpenRouter.
