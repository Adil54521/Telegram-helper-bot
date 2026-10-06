from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import TelegramContentMedia, TelegramContentPost
from app.database.models.enums import TelegramContentStatus


class TelegramContentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _loaded(self):
        return selectinload(TelegramContentPost.media)

    async def by_id(self, item_id: int, chat_id: int, *, lock: bool = False) -> TelegramContentPost | None:
        query = select(TelegramContentPost).where(TelegramContentPost.id == item_id, TelegramContentPost.chat_id == chat_id).options(self._loaded())
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def by_source(self, chat_id: int, message_id: int) -> TelegramContentPost | None:
        return await self.session.scalar(
            select(TelegramContentPost)
            .outerjoin(TelegramContentMedia)
            .where(
                TelegramContentPost.chat_id == chat_id,
                or_(TelegramContentPost.message_id == message_id, TelegramContentMedia.source_message_id == message_id),
            )
            .options(self._loaded())
            .limit(1)
        )

    async def by_group(self, chat_id: int, media_group_id: str, message_thread_id: int | None) -> TelegramContentPost | None:
        return await self.session.scalar(
            select(TelegramContentPost)
            .where(
                TelegramContentPost.chat_id == chat_id,
                TelegramContentPost.media_group_id == media_group_id,
                TelegramContentPost.message_thread_id.is_(message_thread_id) if message_thread_id is None else TelegramContentPost.message_thread_id == message_thread_id,
            )
            .options(self._loaded())
            .order_by(TelegramContentPost.id)
            .limit(1)
        )

    async def listing(self, chat_id: int, status: TelegramContentStatus, offset: int, limit: int) -> list[TelegramContentPost]:
        return list((await self.session.scalars(
            select(TelegramContentPost)
            .where(TelegramContentPost.chat_id == chat_id, TelegramContentPost.status == status)
            .options(self._loaded())
            .order_by(TelegramContentPost.created_at.desc(), TelegramContentPost.id.desc())
            .offset(offset).limit(limit)
        )).all())

    async def count(self, chat_id: int, status: TelegramContentStatus) -> int:
        return int(await self.session.scalar(
            select(func.count(TelegramContentPost.id)).where(TelegramContentPost.chat_id == chat_id, TelegramContentPost.status == status)
        ) or 0)

    async def last_received(self, chat_id: int) -> TelegramContentPost | None:
        return await self.session.scalar(
            select(TelegramContentPost).where(TelegramContentPost.chat_id == chat_id).order_by(TelegramContentPost.created_at.desc()).limit(1)
        )
