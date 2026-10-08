from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import BotButton, BotPage, PageAttachment
from app.database.models.enums import ButtonAction


class PageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_key(self, key: str) -> BotPage | None:
        result = await self.session.execute(
            select(BotPage).where(BotPage.key == key).options(selectinload(BotPage.published_revision), selectinload(BotPage.draft_revision))
        )
        return result.scalar_one_or_none()

    async def by_id(self, page_id: int, *, lock: bool = False) -> BotPage | None:
        query = select(BotPage).where(BotPage.id == page_id).options(
            selectinload(BotPage.published_revision), selectinload(BotPage.draft_revision)
        )
        if lock:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def all(self) -> list[BotPage]:
        result = await self.session.execute(select(BotPage).order_by(BotPage.position, BotPage.id).options(selectinload(BotPage.draft_revision)))
        return list(result.scalars().all())

    async def buttons(self, page_id: int) -> list[BotButton]:
        result = await self.session.execute(select(BotButton).where(BotButton.page_id == page_id, BotButton.is_active.is_(True)).order_by(BotButton.row, BotButton.position))
        return list(result.scalars().all())

    async def attachments(self, revision_id: int) -> list[PageAttachment]:
        result = await self.session.execute(
            select(PageAttachment)
            .where(PageAttachment.revision_id == revision_id)
            .order_by(PageAttachment.position, PageAttachment.id)
        )
        return list(result.scalars().all())

    async def attachment(self, revision_id: int, item_id: int) -> PageAttachment | None:
        return await self.session.scalar(
            select(PageAttachment).where(
                PageAttachment.id == item_id,
                PageAttachment.revision_id == revision_id,
            )
        )

    async def key_exists(self, key: str) -> bool:
        return (await self.session.scalar(select(BotPage.id).where(BotPage.key == key).limit(1))) is not None

    async def incoming_page_buttons(self, key: str) -> list[tuple[BotButton, BotPage]]:
        result = await self.session.execute(
            select(BotButton, BotPage)
            .join(BotPage, BotPage.id == BotButton.page_id)
            .where(BotButton.action_type == ButtonAction.PAGE, BotButton.target == key)
            .options(selectinload(BotPage.draft_revision))
            .order_by(BotPage.position, BotButton.position)
        )
        return list(result.tuples().all())
