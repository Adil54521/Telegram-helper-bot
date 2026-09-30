from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types


class AIProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AIProviderResponse:
    text: str
    usage: TokenUsage


class AIProvider(Protocol):
    async def generate(self, *, instructions: str, user_input: str, max_output_tokens: int = 400) -> AIProviderResponse: ...
    async def close(self) -> None: ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self.model = model
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    async def generate(self, *, instructions: str, user_input: str, max_output_tokens: int = 400) -> AIProviderResponse:
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
            text = (response.text or "").strip()
            metadata = response.usage_metadata
        except Exception as error:
            raise AIProviderError("AI provider request failed") from error
        if not text:
            raise AIProviderError("AI provider returned an empty response")
        prompt_tokens = int(getattr(metadata, "prompt_token_count", None) or 0)
        tool_tokens = int(getattr(metadata, "tool_use_prompt_token_count", None) or 0)
        candidate_tokens = int(getattr(metadata, "candidates_token_count", None) or 0)
        thinking_tokens = int(getattr(metadata, "thoughts_token_count", None) or 0)
        input_tokens = prompt_tokens + tool_tokens
        output_tokens = candidate_tokens + thinking_tokens
        total_tokens = int(getattr(metadata, "total_token_count", None) or 0)
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        return AIProviderResponse(
            text=text,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
        )

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()
