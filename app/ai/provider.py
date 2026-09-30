from typing import Protocol

from google import genai
from google.genai import types


class AIProviderError(RuntimeError):
    pass


class AIProvider(Protocol):
    async def generate(self, *, instructions: str, user_input: str, max_output_tokens: int = 400) -> str: ...
    async def close(self) -> None: ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self.model = model
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    async def generate(self, *, instructions: str, user_input: str, max_output_tokens: int = 400) -> str:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=instructions,
                    max_output_tokens=max_output_tokens,
                    temperature=0.2,
                ),
            )
        except Exception as error:
            raise AIProviderError("AI provider request failed") from error
        text = (response.text or "").strip()
        if not text:
            raise AIProviderError("AI provider returned an empty response")
        return text

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()
