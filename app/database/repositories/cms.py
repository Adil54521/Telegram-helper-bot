from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Admin, BotButton, Broadcast, FAQItem, TelegramUser


class CMSRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def buttons(self, page_id: int) -> list[BotButton]:
        return list((await self.session.scalars(select(BotButton).where(BotButton.page_id == page_id).order_by(BotButton.row, BotButton.position, BotButton.id))).all())

    async def button(self, item_id: int, page_id: int | None = None) -> BotButton | None:
        query = select(BotButton).where(BotButton.id == item_id)
        if page_id is not None:
            query = query.where(BotButton.page_id == page_id)
        return await self.session.scalar(query)

    async def faqs(self, active_only: bool = False) -> list[FAQItem]:
        query = select(FAQItem).order_by(FAQItem.position, FAQItem.id)
        if active_only:
            query = query.where(FAQItem.is_active.is_(True))
        return list((await self.session.scalars(query)).all())

    async def faq(self, item_id: int) -> FAQItem | None:
        return await self.session.get(FAQItem, item_id)

    async def faq_questions(self) -> set[str]:
        return set((await self.session.scalars(select(FAQItem.question))).all())

    async def admins(self) -> list[Admin]:
        return list((await self.session.scalars(select(Admin).order_by(Admin.id))).all())

    async def admin(self, item_id: int) -> Admin | None:
        return await self.session.get(Admin, item_id)

    async def admin_by_telegram(self, telegram_id: int) -> Admin | None:
        return await self.session.scalar(select(Admin).where(Admin.telegram_id == telegram_id))

    async def broadcasts(self, limit: int | None = None) -> list[Broadcast]:
        query = select(Broadcast).order_by(Broadcast.id.desc())
        if limit is not None:
            query = query.limit(limit)
        return list((await self.session.scalars(query)).all())

    async def broadcast(self, item_id: int, *, lock: bool = False) -> Broadcast | None:
        query = select(Broadcast).where(Broadcast.id == item_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def recipients(self) -> list[TelegramUser]:
        return list((await self.session.scalars(select(TelegramUser).where(TelegramUser.is_blocked.is_(False)).order_by(TelegramUser.id))).all())

    async def recipient_count(self) -> int:
        return await self.session.scalar(select(func.count(TelegramUser.id)).where(TelegramUser.is_blocked.is_(False))) or 0
