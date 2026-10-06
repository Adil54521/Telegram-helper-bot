from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any, Protocol

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
    async def generate_structured(self, *, instructions: str, user_input: str, response_schema: Any, max_output_tokens: int) -> tuple[Any, TokenUsage]: ...
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def close(self) -> None: ...


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        embedding_model: str = "gemini-embedding-2",
        embedding_dimension: int = 768,
    ) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
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

    async def generate_structured(self, *, instructions: str, user_input: str, response_schema: Any, max_output_tokens: int) -> tuple[Any, TokenUsage]:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=instructions,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    max_output_tokens=max_output_tokens,
                    temperature=0.1,
                ),
            )
            parsed = response.parsed
            metadata = response.usage_metadata
        except Exception as error:
            raise AIProviderError("AI provider structured request failed") from error
        if parsed is None:
            raise AIProviderError("AI provider returned malformed structured output")
        prompt_tokens = int(getattr(metadata, "prompt_token_count", None) or 0)
        tool_tokens = int(getattr(metadata, "tool_use_prompt_token_count", None) or 0)
        candidate_tokens = int(getattr(metadata, "candidates_token_count", None) or 0)
        thinking_tokens = int(getattr(metadata, "thoughts_token_count", None) or 0)
        total_tokens = int(getattr(metadata, "total_token_count", None) or 0)
        usage = TokenUsage(prompt_tokens + tool_tokens, candidate_tokens + thinking_tokens, total_tokens or prompt_tokens + tool_tokens + candidate_tokens + thinking_tokens)
        return parsed, usage

    async def embed_query(self, text: str) -> list[float]:
        values = await self._embed([f"Task: retrieve relevant university information.\nQuery: {text}"])
        return values[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        prepared = [f"Task: represent university knowledge for retrieval.\n{value}" for value in texts]
        return await self._embed(prepared)

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        contents = [types.Content(parts=[types.Part(text=value)]) for value in texts]
        try:
            response = await self.client.aio.models.embed_content(
                model=self.embedding_model,
                contents=contents,
                config=types.EmbedContentConfig(output_dimensionality=self.embedding_dimension),
            )
            embeddings = response.embeddings or []
            values = [list(item.values or []) for item in embeddings]
        except Exception as error:
            raise AIProviderError("AI embedding request failed") from error
        if len(values) != len(texts) or any(len(vector) != self.embedding_dimension for vector in values):
            raise AIProviderError("AI provider returned invalid embeddings")
        return values

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()
