"""Servicio que habla con Groq (modelos Llama) y guarda el contexto de cada usuario."""
import base64
import logging
from collections import defaultdict, deque

from groq import AsyncGroq

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres un asistente personal inteligente, amable y cercano. "
    "Respondes en el idioma del usuario, de forma natural, clara y concisa. "
    "Puedes usar algún emoji de vez en cuando, sin pasarte. "
    "Responde en texto plano, sin formato Markdown."
)

# Modelos: uno para texto (configurable), uno fijo para visión y otro para transcribir audio
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
AUDIO_MODEL = "whisper-large-v3-turbo"


class GroqError(Exception):
    """Error amigable para mostrarle al usuario."""


class GroqService:
    def __init__(self, api_key: str, model: str, max_history: int) -> None:
        self.client = AsyncGroq(api_key=api_key)
        self.model = model
        # Historial por usuario: deque descarta solo los mensajes más viejos
        self._history: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=max_history))

    def reset(self, user_id: int) -> None:
        """Borra la memoria de un usuario."""
        self._history.pop(user_id, None)

    def _handle_error(self, e: Exception) -> GroqError:
        status = getattr(e, "status_code", None)
        logger.error("Error de la API de Groq (%s): %s", status, e)
        if status == 429:
            return GroqError(
                "Uf, me están preguntando demasiado rápido 😅 Espera un momento y reintenta."
            )
        if status is not None:
            return GroqError("Groq no me contestó bien 🤕 Inténtalo de nuevo en un rato.")
        return GroqError("Algo se rompió por mi lado 🔧 Intenta otra vez.")

    async def ask(self, user_id: int, text: str) -> str:
        """Envía un mensaje de texto a Groq (con contexto) y devuelve la respuesta."""
        history = self._history[user_id]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": text},
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.8
            )
        except Exception as e:
            raise self._handle_error(e) from e

        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            raise GroqError("No pude generar respuesta para eso 🤔 ¿Lo intentas de otra forma?")

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": answer})
        return answer

    async def ask_with_image(self, user_id: int, image_bytes: bytes, prompt: str) -> str:
        """Envía una imagen (+ un prompt) a Groq usando un modelo con visión."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"

        history = self._history[user_id]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        try:
            response = await self.client.chat.completions.create(
                model=VISION_MODEL, messages=messages, temperature=0.8
            )
        except Exception as e:
            raise self._handle_error(e) from e

        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            raise GroqError("No pude generar respuesta para eso 🤔 ¿Lo intentas de otra forma?")

        # En el historial dejamos un texto representativo, no la imagen en sí
        history.append({"role": "user", "content": f"[Imagen enviada] {prompt}"})
        history.append({"role": "assistant", "content": answer})
        return answer

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
        """Convierte una nota de voz en texto usando Whisper (vía Groq)."""
        try:
            response = await self.client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model=AUDIO_MODEL,
            )
        except Exception as e:
            raise self._handle_error(e) from e

        text = (response.text or "").strip()
        if not text:
            raise GroqError("No logré entender ese audio 🎙️ intenta grabarlo de nuevo.")
        return text
