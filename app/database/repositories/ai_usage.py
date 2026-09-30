from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AIUsage


class AIUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **values: Any) -> AIUsage:
        usage = AIUsage(**values)
        self.session.add(usage)
        await self.session.flush()
        return usage

    async def aggregate_user_requests(self, since: datetime | None) -> dict[str, Any]:
        query = select(
            func.count(AIUsage.id).label("questions"),
            func.count(distinct(AIUsage.user_id)).label("users"),
            func.sum(case((AIUsage.gemini_called.is_(True), 1), else_=0)).label("gemini_calls"),
            func.sum(case((AIUsage.retrieval_count == 0, 1), else_=0)).label("no_results"),
            func.sum(case((AIUsage.success.is_(True), 1), else_=0)).label("successful"),
            func.sum(case((AIUsage.success.is_(False), 1), else_=0)).label("errors"),
            func.avg(AIUsage.duration_ms).label("average_duration_ms"),
            func.avg(AIUsage.retrieval_count).label("average_retrieval"),
            func.sum(AIUsage.input_tokens).label("input_tokens"),
            func.sum(AIUsage.output_tokens).label("output_tokens"),
            func.sum(AIUsage.total_tokens).label("total_tokens"),
            func.sum(AIUsage.input_cost_usd).label("input_cost_usd"),
            func.sum(AIUsage.output_cost_usd).label("output_cost_usd"),
            func.sum(AIUsage.total_cost_usd).label("total_cost_usd"),
        ).where(AIUsage.request_type == "USER")
        if since is not None:
            query = query.where(AIUsage.created_at >= since)
        row = (await self.session.execute(query)).one()
        return {
            "questions": int(row.questions or 0),
            "users": int(row.users or 0),
            "gemini_calls": int(row.gemini_calls or 0),
            "no_results": int(row.no_results or 0),
            "successful": int(row.successful or 0),
            "errors": int(row.errors or 0),
            "average_duration_ms": float(row.average_duration_ms or 0),
            "average_retrieval": float(row.average_retrieval or 0),
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "input_cost_usd": Decimal(row.input_cost_usd or 0),
            "output_cost_usd": Decimal(row.output_cost_usd or 0),
            "total_cost_usd": Decimal(row.total_cost_usd or 0),
        }
