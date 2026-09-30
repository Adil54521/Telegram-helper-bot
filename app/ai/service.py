import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.knowledge import KnowledgeService
from app.ai.prompts import SYSTEM_PROMPT, build_input
from app.ai.provider import AIProvider

logger = logging.getLogger(__name__)
MAX_QUESTION_LENGTH = 1000


class AIRateLimitError(RuntimeError):
    pass


@dataclass(slots=True)
class AIAnswer:
    text: str
    result_count: int


class AIService:
    def __init__(self, provider: AIProvider, cooldown_seconds: float, knowledge: KnowledgeService | None = None, *, provider_name: str = "gemini", model: str = "") -> None:
        self.provider = provider
        self.cooldown_seconds = cooldown_seconds
        self.knowledge = knowledge or KnowledgeService()
        self.provider_name = provider_name
        self.model = model
        self._last_requests: dict[int, float] = {}

    async def close(self) -> None:
        await self.provider.close()

    async def health_check(self) -> int:
        started = time.monotonic()
        await self.provider.generate(
            instructions="Return exactly OK.",
            user_input="Reply exactly: OK",
            max_output_tokens=5,
        )
        return int((time.monotonic() - started) * 1000)

    async def answer(self, session: AsyncSession, *, user_id: int, question: str, language: str) -> AIAnswer | None:
        now = time.monotonic()
        previous = self._last_requests.get(user_id)
        if previous is not None and now - previous < self.cooldown_seconds:
            raise AIRateLimitError
        self._last_requests[user_id] = now
        started = time.monotonic()
        items = await self.knowledge.search(session, question, language)
        if not items:
            logger.info("AI request user_id=%s language=%s provider=%s model=%s retrieval=0 duration_ms=%d success=true", user_id, language, self.provider_name, self.model, int((time.monotonic()-started)*1000))
            return None
        context = self.knowledge.build_context(items)
        try:
            text = await self.provider.generate(instructions=SYSTEM_PROMPT, user_input=build_input(question, language, context))
        except Exception:
            logger.exception("AI request failed user_id=%s language=%s provider=%s model=%s retrieval=%s duration_ms=%d", user_id, language, self.provider_name, self.model, len(items), int((time.monotonic()-started)*1000))
            raise
        logger.info("AI request user_id=%s language=%s provider=%s model=%s retrieval=%s duration_ms=%d success=true", user_id, language, self.provider_name, self.model, len(items), int((time.monotonic()-started)*1000))
        return AIAnswer(text=text, result_count=len(items))
