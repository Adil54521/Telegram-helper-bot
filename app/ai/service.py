import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.knowledge import KnowledgeService
from app.ai.prompts import SYSTEM_PROMPT, build_input
from app.ai.provider import AIProvider
from app.database.repositories.ai_usage import AIUsageRepository
from app.services.ai_analytics import AICost, ZERO_COST, calculate_cost

logger = logging.getLogger(__name__)
MAX_QUESTION_LENGTH = 1000


class AIRateLimitError(RuntimeError):
    pass


@dataclass(slots=True)
class AIAnswer:
    text: str
    result_count: int


def _zero_cost() -> AICost:
    return AICost(ZERO_COST, ZERO_COST, ZERO_COST)


class AIService:
    def __init__(self, provider: AIProvider, cooldown_seconds: float, knowledge: KnowledgeService | None = None, *, provider_name: str = "gemini", model: str = "", input_price_per_1m: Decimal = ZERO_COST, output_price_per_1m: Decimal = ZERO_COST) -> None:
        self.provider = provider
        self.cooldown_seconds = cooldown_seconds
        self.knowledge = knowledge or KnowledgeService()
        self.provider_name = provider_name
        self.model = model
        self.input_price_per_1m = input_price_per_1m
        self.output_price_per_1m = output_price_per_1m
        self._last_requests: dict[int, float] = {}

    async def close(self) -> None:
        await self.provider.close()

    async def _record(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        request_type: str,
        language: str,
        retrieval_count: int,
        gemini_called: bool,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost: AICost,
        duration_ms: int,
        success: bool,
        error_type: str | None = None,
    ) -> None:
        await AIUsageRepository(session).add(
            user_id=user_id,
            request_type=request_type,
            language=language,
            provider=self.provider_name,
            model=self.model,
            retrieval_count=retrieval_count,
            gemini_called=gemini_called,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            input_price_per_1m=self.input_price_per_1m,
            output_price_per_1m=self.output_price_per_1m,
            input_cost_usd=cost.input_cost,
            output_cost_usd=cost.output_cost,
            total_cost_usd=cost.total_cost,
            duration_ms=duration_ms,
            success=success,
            error_type=error_type,
        )

    async def health_check(self, session: AsyncSession, *, user_id: int) -> int:
        started = time.monotonic()
        try:
            response = await self.provider.generate(
                instructions="Return exactly OK.",
                user_input="Reply exactly: OK",
                max_output_tokens=5,
            )
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(
                session,
                user_id=user_id,
                request_type="HEALTH_CHECK",
                language="ru",
                retrieval_count=0,
                gemini_called=True,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost=_zero_cost(),
                duration_ms=duration_ms,
                success=False,
                error_type=type(error).__name__,
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage
        cost = calculate_cost(usage.input_tokens, usage.output_tokens, self.input_price_per_1m, self.output_price_per_1m)
        await self._record(
            session,
            user_id=user_id,
            request_type="HEALTH_CHECK",
            language="ru",
            retrieval_count=0,
            gemini_called=True,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cost=cost,
            duration_ms=duration_ms,
            success=True,
        )
        return duration_ms

    async def answer(self, session: AsyncSession, *, user_id: int, question: str, language: str) -> AIAnswer | None:
        now = time.monotonic()
        previous = self._last_requests.get(user_id)
        if previous is not None and now - previous < self.cooldown_seconds:
            raise AIRateLimitError
        self._last_requests[user_id] = now
        started = time.monotonic()
        try:
            items = await self.knowledge.search(session, question, language)
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(session, user_id=user_id, request_type="USER", language=language, retrieval_count=0, gemini_called=False, input_tokens=0, output_tokens=0, total_tokens=0, cost=_zero_cost(), duration_ms=duration_ms, success=False, error_type=type(error).__name__)
            logger.exception("AI retrieval failed user_id=%s language=%s provider=%s model=%s gemini_called=false duration_ms=%d success=false error_type=%s", user_id, language, self.provider_name, self.model, duration_ms, type(error).__name__)
            raise
        if not items:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(session, user_id=user_id, request_type="USER", language=language, retrieval_count=0, gemini_called=False, input_tokens=0, output_tokens=0, total_tokens=0, cost=_zero_cost(), duration_ms=duration_ms, success=True)
            logger.info("AI request user_id=%s language=%s provider=%s model=%s retrieval=0 gemini_called=false input_tokens=0 output_tokens=0 total_tokens=0 estimated_cost_usd=0 duration_ms=%d success=true", user_id, language, self.provider_name, self.model, duration_ms)
            return None
        context = self.knowledge.build_context(items)
        try:
            response = await self.provider.generate(instructions=SYSTEM_PROMPT, user_input=build_input(question, language, context))
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(session, user_id=user_id, request_type="USER", language=language, retrieval_count=len(items), gemini_called=True, input_tokens=0, output_tokens=0, total_tokens=0, cost=_zero_cost(), duration_ms=duration_ms, success=False, error_type=type(error).__name__)
            logger.exception("AI request failed user_id=%s language=%s provider=%s model=%s retrieval=%s gemini_called=true duration_ms=%d success=false error_type=%s", user_id, language, self.provider_name, self.model, len(items), duration_ms, type(error).__name__)
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage
        cost = calculate_cost(usage.input_tokens, usage.output_tokens, self.input_price_per_1m, self.output_price_per_1m)
        await self._record(session, user_id=user_id, request_type="USER", language=language, retrieval_count=len(items), gemini_called=True, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens, cost=cost, duration_ms=duration_ms, success=True)
        logger.info("AI request user_id=%s language=%s provider=%s model=%s retrieval=%s gemini_called=true input_tokens=%d output_tokens=%d total_tokens=%d estimated_cost_usd=%s duration_ms=%d success=true", user_id, language, self.provider_name, self.model, len(items), usage.input_tokens, usage.output_tokens, usage.total_tokens, cost.total_cost, duration_ms)
        return AIAnswer(text=response.text, result_count=len(items))
