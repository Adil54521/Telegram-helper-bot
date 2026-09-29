from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TelegramUser


async def user_statistics(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    total = await session.scalar(select(func.count(TelegramUser.id))) or 0
    new_today = await session.scalar(select(func.count(TelegramUser.id)).where(TelegramUser.first_seen_at >= today)) or 0
    new_week = await session.scalar(select(func.count(TelegramUser.id)).where(TelegramUser.first_seen_at >= week)) or 0
    active_week = await session.scalar(select(func.count(TelegramUser.id)).where(TelegramUser.last_seen_at >= week)) or 0
    return {"total": total, "new_today": new_today, "new_week": new_week, "active_week": active_week}

