from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.ai_usage import AIUsageRepository

TOKENS_PER_MILLION = Decimal(1_000_000)
ZERO_COST = Decimal("0")


class AIAnalyticsPeriod(StrEnum):
    TODAY = "TODAY"
    SEVEN_DAYS = "7_DAYS"
    THIRTY_DAYS = "30_DAYS"
    ALL = "ALL"


@dataclass(frozen=True, slots=True)
class AICost:
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal


@dataclass(frozen=True, slots=True)
class AIAnalytics:
    questions: int
    users: int
    gemini_calls: int
    no_results: int
    successful: int
    errors: int
    average_duration_ms: float
    average_retrieval: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_usd: Decimal
    output_cost_usd: Decimal
    total_cost_usd: Decimal

    @property
    def no_results_percent(self) -> float:
        return self.no_results / self.questions * 100 if self.questions else 0.0

    @property
    def average_cost_per_call(self) -> Decimal:
        return self.total_cost_usd / self.gemini_calls if self.gemini_calls else ZERO_COST


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    input_price_per_1m: Decimal,
    output_price_per_1m: Decimal,
) -> AICost:
    input_cost = Decimal(input_tokens) / TOKENS_PER_MILLION * input_price_per_1m
    output_cost = Decimal(output_tokens) / TOKENS_PER_MILLION * output_price_per_1m
    return AICost(input_cost, output_cost, input_cost + output_cost)


def period_start(period: AIAnalyticsPeriod, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(timezone.utc)
    if period == AIAnalyticsPeriod.TODAY:
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == AIAnalyticsPeriod.SEVEN_DAYS:
        return current - timedelta(days=7)
    if period == AIAnalyticsPeriod.THIRTY_DAYS:
        return current - timedelta(days=30)
    return None


class AIAnalyticsService:
    async def summary(self, session: AsyncSession, period: AIAnalyticsPeriod) -> AIAnalytics:
        values = await AIUsageRepository(session).aggregate_user_requests(period_start(period))
        return AIAnalytics(**values)
