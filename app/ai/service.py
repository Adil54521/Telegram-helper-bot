import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.knowledge import KnowledgeService
from app.ai.index import KnowledgeIndexService
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


class FAQTranslation(BaseModel):
    index: int = Field(ge=0)
    question_kk: str = Field(min_length=1, max_length=500)
    answer_kk: str = Field(min_length=1, max_length=10_000)


class PageTranslation(BaseModel):
    title_kk: str = Field(min_length=1, max_length=255)
    text_kk: str = Field(min_length=1, max_length=20_000)


class ShortTranslation(BaseModel):
    text_kk: str = Field(min_length=1, max_length=1024)


class ChannelContentPreparation(BaseModel):
    recommended_type: Literal["PAGE", "FAQ", "IGNORE"]
    reason_short: str = Field(min_length=1, max_length=300)
    title_ru: str | None = Field(default=None, max_length=255)
    text_ru: str | None = Field(default=None, max_length=20_000)
    title_kk: str | None = Field(default=None, max_length=255)
    text_kk: str | None = Field(default=None, max_length=20_000)
    question_ru: str | None = Field(default=None, max_length=500)
    answer_ru: str | None = Field(default=None, max_length=10_000)
    question_kk: str | None = Field(default=None, max_length=500)
    answer_kk: str | None = Field(default=None, max_length=10_000)


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
        self.knowledge_index: KnowledgeIndexService | None = None

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
            search_with_mode = getattr(self.knowledge, "search_with_mode", None)
            if search_with_mode:
                items, retrieval_mode = await search_with_mode(session, question, language)
            else:
                items = await self.knowledge.search(session, question, language)
                retrieval_mode = "lexical"
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(session, user_id=user_id, request_type="USER", language=language, retrieval_count=0, gemini_called=False, input_tokens=0, output_tokens=0, total_tokens=0, cost=_zero_cost(), duration_ms=duration_ms, success=False, error_type=type(error).__name__)
            logger.exception("AI retrieval failed user_id=%s language=%s provider=%s model=%s gemini_called=false duration_ms=%d success=false error_type=%s", user_id, language, self.provider_name, self.model, duration_ms, type(error).__name__)
            raise
        logger.info(
            "AI retrieval completed mode=%s language=%s results=%d",
            retrieval_mode, language, len(items),
        )
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

    async def _structured_translation(self, session: AsyncSession, *, user_id: int, request_type: str, user_input: str, schema: object, max_output_tokens: int) -> object:
        started = time.monotonic()
        try:
            parsed, usage = await self.provider.generate_structured(
                instructions="Translate only the supplied Russian university content into natural, accurate Kazakh. Preserve meaning, formatting, names, URLs and numbers. Return only the required structured result.",
                user_input=user_input,
                response_schema=schema,
                max_output_tokens=max_output_tokens,
            )
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(session, user_id=user_id, request_type=request_type, language="kk", retrieval_count=0, gemini_called=True, input_tokens=0, output_tokens=0, total_tokens=0, cost=_zero_cost(), duration_ms=duration_ms, success=False, error_type=type(error).__name__)
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        cost = calculate_cost(usage.input_tokens, usage.output_tokens, self.input_price_per_1m, self.output_price_per_1m)
        await self._record(session, user_id=user_id, request_type=request_type, language="kk", retrieval_count=0, gemini_called=True, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens, cost=cost, duration_ms=duration_ms, success=True)
        logger.info("AI translation user_id=%s type=%s provider=%s model=%s input_tokens=%d output_tokens=%d total_tokens=%d estimated_cost_usd=%s duration_ms=%d success=true", user_id, request_type, self.provider_name, self.model, usage.input_tokens, usage.output_tokens, usage.total_tokens, cost.total_cost, duration_ms)
        return parsed

    async def translate_faq_batch(self, session: AsyncSession, *, user_id: int, rows: Sequence[dict[str, object]]) -> list[FAQTranslation]:
        if not 1 <= len(rows) <= 10:
            raise ValueError("FAQ translation batch must contain 1-10 rows")
        payload = [{"index": index, "question_ru": str(row["question_ru"]), "answer_ru": str(row["answer_ru"])} for index, row in enumerate(rows)]
        input_text = __import__("json").dumps(payload, ensure_ascii=False)
        if len(input_text) > 12_000:
            raise ValueError("FAQ translation batch is too large")
        limit = min(4096, max(512, len(input_text) // 2 + 256))
        result = await self._structured_translation(session, user_id=user_id, request_type="FAQ_TRANSLATION", user_input=input_text, schema=list[FAQTranslation], max_output_tokens=limit)
        values = [value if isinstance(value, FAQTranslation) else FAQTranslation.model_validate(value) for value in result]  # type: ignore[arg-type]
        if len(values) != len(rows) or {value.index for value in values} != set(range(len(rows))):
            raise ValueError("Gemini returned an incomplete FAQ translation batch")
        return sorted(values, key=lambda value: value.index)

    async def translate_page(self, session: AsyncSession, *, user_id: int, title: str, text: str) -> PageTranslation:
        payload = __import__("json").dumps({"title_ru": title, "text_ru": text}, ensure_ascii=False)
        if len(payload) > 12_000:
            raise ValueError("Page is too large for AI translation")
        limit = min(4096, max(512, len(payload) // 2 + 256))
        result = await self._structured_translation(session, user_id=user_id, request_type="PAGE_TRANSLATION", user_input=payload, schema=PageTranslation, max_output_tokens=limit)
        return result if isinstance(result, PageTranslation) else PageTranslation.model_validate(result)

    async def translate_short_text(self, session: AsyncSession, *, user_id: int, text: str, request_type: str = "CMS_TRANSLATION", max_length: int = 1024) -> str:
        if not text.strip() or len(text) > max_length:
            raise ValueError("Text is outside translation limits")
        result = await self._structured_translation(session, user_id=user_id, request_type=request_type, user_input=text, schema=ShortTranslation, max_output_tokens=min(256, max(32, len(text) * 2)))
        value = result if isinstance(result, ShortTranslation) else ShortTranslation.model_validate(result)
        return value.text_kk.strip()

    async def prepare_channel_content(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        source: str,
        media_summary: str = "",
    ) -> ChannelContentPreparation:
        if not source.strip():
            raise ValueError("Channel post has no text")
        truncated = source[:12_000]
        payload = __import__("json").dumps(
            {"post": truncated, "media": media_summary[:1000], "input_truncated": len(source) > len(truncated)},
            ensure_ascii=False,
        )
        instructions = (
            "Transform one official KUAM Telegram post into CMS content. Use only facts in the post; "
            "never invent dates, prices, contacts or requirements. Preserve names, numbers, URLs and codes. "
            "Choose PAGE for durable reference information, FAQ only when naturally question/answer, otherwise IGNORE. "
            "For PAGE fill title_ru/text_ru/title_kk/text_kk. For FAQ fill question_ru/answer_ru/question_kk/answer_kk. "
            "Translate naturally from Russian to Kazakh without adding information. Return the structured schema only."
        )
        started = time.monotonic()
        try:
            parsed, usage = await self.provider.generate_structured(
                instructions=instructions,
                user_input=payload,
                response_schema=ChannelContentPreparation,
                max_output_tokens=1800,
            )
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record(
                session, user_id=user_id, request_type="CHANNEL_CONTENT_PREPARATION", language="ru",
                retrieval_count=0, gemini_called=True, input_tokens=0, output_tokens=0, total_tokens=0,
                cost=_zero_cost(), duration_ms=duration_ms, success=False, error_type=type(error).__name__,
            )
            raise
        value = parsed if isinstance(parsed, ChannelContentPreparation) else ChannelContentPreparation.model_validate(parsed)
        duration_ms = int((time.monotonic() - started) * 1000)
        cost = calculate_cost(usage.input_tokens, usage.output_tokens, self.input_price_per_1m, self.output_price_per_1m)
        await self._record(
            session, user_id=user_id, request_type="CHANNEL_CONTENT_PREPARATION", language="ru",
            retrieval_count=0, gemini_called=True, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens, cost=cost, duration_ms=duration_ms, success=True,
        )
        logger.info(
            "Channel content preparation user_id=%s provider=%s model=%s input_tokens=%d output_tokens=%d duration_ms=%d success=true",
            user_id, self.provider_name, self.model, usage.input_tokens, usage.output_tokens, duration_ms,
        )
        return value
