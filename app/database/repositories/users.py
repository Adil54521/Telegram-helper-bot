from datetime import datetime, timezone

from aiogram.types import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TelegramUser


async def upsert_user(session: AsyncSession, source: User) -> TelegramUser:
    user = await session.scalar(select(TelegramUser).where(TelegramUser.telegram_id == source.id))
    now = datetime.now(timezone.utc)
    if user is None:
        user = TelegramUser(telegram_id=source.id, first_name=source.first_name, first_seen_at=now, last_seen_at=now)
        session.add(user)
    user.username = source.username
    user.first_name = source.first_name
    user.last_name = source.last_name
    user.language_code = source.language_code
    user.last_seen_at = now
    user.is_blocked = False
    await session.flush()
    return user

