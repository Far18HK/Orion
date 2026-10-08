"""Servicio que habla con Gemini y guarda el contexto de cada usuario."""
import logging
from collections import defaultdict, deque

from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

# Personalidad del asistente
SYSTEM_PROMPT = (
    "Eres un asistente personal inteligente, amable y cercano. "
    "Respondes en el idioma del usuario, de forma natural, clara y concisa. "
    "Puedes usar algún emoji de vez en cuando, sin pasarte. "
    "Responde en texto plano, sin formato Markdown."
)


class GeminiError(Exception):
    """Error amigable para mostrarle al usuario."""


class GeminiService:
    def __init__(self, api_key: str, model: str, max_history: int) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        # Historial por usuario: deque descarta solo los mensajes más viejos
        self._history: dict[int, deque[types.Content]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )

    def reset(self, user_id: int) -> None:
        """Borra la memoria de un usuario."""
        self._history.pop(user_id, None)

    async def ask(self, user_id: int, text: str) -> str:
        """Envía el mensaje a Gemini (con contexto) y devuelve la respuesta."""
        history = self._history[user_id]
        user_msg = types.Content(role="user", parts=[types.Part(text=text)])

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=[*history, user_msg],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.8,
                ),
            )
        except errors.APIError as e:
            logger.error("Error de la API de Gemini (%s): %s", e.code, e)
            if e.code == 429:
                raise GeminiError(
                    "Uf, me están preguntando demasiado rápido 😅 Espera un momento y reintenta."
                ) from e
            raise GeminiError(
                "Gemini no me contestó bien 🤕 Inténtalo de nuevo en un rato."
            ) from e
        except Exception as e:  # Red caída, timeout, etc.
            logger.exception("Error inesperado llamando a Gemini")
            raise GeminiError("Algo se rompió por mi lado 🔧 Intenta otra vez.") from e

        answer = (response.text or "").strip()
        if not answer:
            # Pasa cuando los filtros de seguridad bloquean la respuesta
            raise GeminiError("No pude generar respuesta para eso 🤔 ¿Lo intentas de otra forma?")

        # Solo guardamos en memoria si todo salió bien
        history.append(user_msg)
        history.append(types.Content(role="model", parts=[types.Part(text=answer)]))
        return answer
