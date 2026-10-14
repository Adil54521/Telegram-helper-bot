from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models.entities import AIConversation


class AIHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        question: str,
        language: str,
    ) -> AIConversation:
        conversation = AIConversation(
            user_id=user_id,
            question=question,
            language_code=language,
            status="PENDING",
        )

        self.session.add(conversation)
        await self.session.flush()

        return conversation

    async def complete(
        self,
        conversation: AIConversation,
        answer: str | None,
    ) -> None:
        conversation.answer = answer
        conversation.status = (
            "COMPLETED" if answer is not None else "NO_INFO"
        )
        conversation.answered_at = datetime.now(timezone.utc)

    async def fail(
        self,
        conversation: AIConversation,
    ) -> None:
        conversation.status = "FAILED"
        conversation.answered_at = datetime.now(timezone.utc)

    async def list_recent(
        self,
        *,
        limit: int = 8,
        offset: int = 0,
        status: str | None = None,
    ) -> list[AIConversation]:
        stmt = (
            select(AIConversation)
            .order_by(
                AIConversation.created_at.desc(),
                AIConversation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        if status:
            stmt = stmt.where(AIConversation.status == status)

        result = await self.session.scalars(stmt)
        return list(result.all())


    async def count(
        self,
        *,
        status: str | None = None,
    ) -> int:
        stmt = select(func.count(AIConversation.id))

        if status:
            stmt = stmt.where(AIConversation.status == status)

        return await self.session.scalar(stmt) or 0


    async def get_by_id(
        self,
        record_id: int,
    ) -> AIConversation | None:
        return await self.session.get(AIConversation, record_id)

    async def list_for_user(
        self,
        *,
        user_id: int,
        limit: int = 5,
        offset: int = 0,
    ) -> list[AIConversation]:
        stmt = (
            select(AIConversation)
            .where(AIConversation.user_id == user_id)
            .order_by(
                AIConversation.created_at.desc(),
                AIConversation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.scalars(stmt)
        return list(result.all())


    async def count_for_user(self, user_id: int) -> int:
        stmt = (
            select(func.count(AIConversation.id))
            .where(AIConversation.user_id == user_id)
        )

        return await self.session.scalar(stmt) or 0


    async def get_for_user(
        self,
        *,
        record_id: int,
        user_id: int,
    ) -> AIConversation | None:
        return await self.session.scalar(
            select(AIConversation).where(
                AIConversation.id == record_id,
                AIConversation.user_id == user_id,
            )
        )