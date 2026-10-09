from datetime import datetime
from dataclasses import dataclass

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import BotPage, BotPageRevision, TelegramContentPost, TelegramForumTopic, TelegramImportSettings, TelegramTopicPageMapping
from app.database.models.enums import TelegramContentStatus, TelegramContentTarget, TelegramProcessingMode


@dataclass(frozen=True, slots=True)
class TopicMappingListItem:
    mapping_id: int
    chat_id: int
    message_thread_id: int
    topic_name: str | None
    parent_page_id: int | None
    parent_page_key: str | None
    parent_page_title: str | None
    is_active: bool
    auto_draft_enabled: bool
    processing_mode: TelegramProcessingMode
    auto_attach_materials: bool
    content_target: TelegramContentTarget = TelegramContentTarget.PAGE


class TelegramAutoDraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def settings(
        self,
        chat_id: int,
        *,
        create: bool = False,
        notify_admins_default: bool = True,
    ) -> TelegramImportSettings | None:
        value = await self.session.scalar(select(TelegramImportSettings).where(TelegramImportSettings.chat_id == chat_id))
        if value is None and create:
            value = TelegramImportSettings(
                chat_id=chat_id,
                notify_admins=notify_admins_default,
            )
            self.session.add(value)
            await self.session.flush()
        return value

    async def notifications_enabled(self, chat_id: int, env_default: bool) -> bool:
        """PostgreSQL is authoritative once CMS settings exist; env is the legacy fallback."""
        value = await self.settings(chat_id)
        return value.notify_admins if value is not None else env_default

    async def mapping(self, chat_id: int, thread_id: int) -> TelegramTopicPageMapping | None:
        return await self.session.scalar(
            select(TelegramTopicPageMapping).where(
                TelegramTopicPageMapping.chat_id == chat_id,
                TelegramTopicPageMapping.message_thread_id == thread_id,
            )
        )

    async def mappings(self, chat_id: int) -> list[TopicMappingListItem]:
        rows = await self.session.execute(
            select(
                TelegramTopicPageMapping.id,
                TelegramTopicPageMapping.chat_id,
                TelegramTopicPageMapping.message_thread_id,
                TelegramForumTopic.topic_name,
                TelegramTopicPageMapping.parent_page_id,
                BotPage.key,
                func.coalesce(BotPageRevision.title, BotPage.key),
                TelegramTopicPageMapping.is_active,
                TelegramTopicPageMapping.auto_draft_enabled,
                TelegramTopicPageMapping.processing_mode,
                TelegramTopicPageMapping.auto_attach_materials,
                TelegramTopicPageMapping.content_target,
            )
            .outerjoin(BotPage, BotPage.id == TelegramTopicPageMapping.parent_page_id)
            .outerjoin(BotPageRevision, BotPageRevision.id == BotPage.draft_revision_id)
            .outerjoin(
                TelegramForumTopic,
                and_(
                    TelegramForumTopic.chat_id == TelegramTopicPageMapping.chat_id,
                    TelegramForumTopic.message_thread_id == TelegramTopicPageMapping.message_thread_id,
                ),
            )
            .where(TelegramTopicPageMapping.chat_id == chat_id)
            .order_by(TelegramTopicPageMapping.message_thread_id)
        )
        return [TopicMappingListItem(*row) for row in rows.tuples().all()]

    async def cancel_pending_for_topic(self, chat_id: int, thread_id: int) -> int:
        result = await self.session.execute(
            update(TelegramContentPost)
            .where(
                TelegramContentPost.chat_id == chat_id,
                TelegramContentPost.message_thread_id == thread_id,
                TelegramContentPost.status == TelegramContentStatus.NEW,
                TelegramContentPost.auto_process_after.is_not(None),
            )
            .values(auto_process_after=None, auto_processing_attempts=0, auto_last_error=None)
        )
        return int(result.rowcount or 0)

    async def seen_threads(self, chat_id: int) -> list[int]:
        discovered = select(TelegramForumTopic.message_thread_id.label("message_thread_id")).where(
            TelegramForumTopic.chat_id == chat_id
        )
        observed = select(TelegramContentPost.message_thread_id.label("message_thread_id")).where(
            TelegramContentPost.chat_id == chat_id,
            TelegramContentPost.message_thread_id.is_not(None),
        )
        combined = discovered.union(observed).subquery()
        return list((await self.session.scalars(
            select(combined.c.message_thread_id).order_by(combined.c.message_thread_id)
        )).all())

    async def topic_names(self, chat_id: int, thread_ids: list[int] | None = None) -> dict[int, str]:
        query = select(TelegramForumTopic.message_thread_id, TelegramForumTopic.topic_name).where(TelegramForumTopic.chat_id == chat_id)
        if thread_ids is not None:
            if not thread_ids:
                return {}
            query = query.where(TelegramForumTopic.message_thread_id.in_(thread_ids))
        return {int(row.message_thread_id): row.topic_name for row in (await self.session.execute(query)).all()}

    async def upsert_topic_name(self, chat_id: int, thread_id: int, topic_name: str) -> None:
        statement = insert(TelegramForumTopic).values(
            chat_id=chat_id, message_thread_id=thread_id, topic_name=topic_name,
        ).on_conflict_do_update(
            constraint="uq_telegram_forum_topic",
            set_={"topic_name": topic_name, "updated_at": func.now()},
        )
        await self.session.execute(statement)

    async def pending_source(self, chat_id: int, thread_id: int, before_message_id: int) -> TelegramContentPost | None:
        return await self.session.scalar(
            select(TelegramContentPost).where(
                TelegramContentPost.chat_id == chat_id,
                TelegramContentPost.message_thread_id == thread_id,
                TelegramContentPost.message_id < before_message_id,
                TelegramContentPost.status == TelegramContentStatus.NEW,
                TelegramContentPost.auto_process_after.is_not(None),
            ).order_by(TelegramContentPost.message_id.desc()).limit(1)
        )

    async def claim_due(self, now: datetime) -> TelegramContentPost | None:
        return await self.session.scalar(
            select(TelegramContentPost).where(
                TelegramContentPost.status == TelegramContentStatus.NEW,
                TelegramContentPost.auto_process_after.is_not(None),
                TelegramContentPost.auto_process_after <= now,
                TelegramContentPost.auto_processing_attempts < 3,
            ).options(selectinload(TelegramContentPost.media))
            .order_by(TelegramContentPost.auto_process_after, TelegramContentPost.id)
            .with_for_update(skip_locked=True).limit(1)
        )

    async def page_count(self) -> int:
        return int(await self.session.scalar(select(func.count(BotPage.id))) or 0)
