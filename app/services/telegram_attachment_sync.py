from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditLog,
    BotPage,
    PageAttachment,
    TelegramAttachmentReplacement,
    TelegramAutoDraftLink,
    TelegramContentMedia,
    TelegramContentPost,
)
from app.database.models.enums import MediaType, PageStatus, TelegramContentStatus, TelegramFileSyncMode
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.pages import PageService


@dataclass(frozen=True, slots=True)
class AttachmentSyncResult:
    status: str
    page_id: int | None = None
    pending_id: int | None = None


class TelegramAttachmentSyncService:
    """Synchronize Telegram file identities without mutating published revisions."""

    def __init__(self, session: AsyncSession, actor: int) -> None:
        self.session = session
        self.actor = actor

    async def process_edit(
        self,
        post_id: int,
        media: tuple[MediaType, str, str | None, str | None, int | None] | None,
        source_message_id: int,
    ) -> AttachmentSyncResult:
        if media is None:
            return AttachmentSyncResult("NO_MEDIA")
        post = await self.session.scalar(
            select(TelegramContentPost).where(TelegramContentPost.id == post_id).with_for_update()
        )
        if not post or post.status == TelegramContentStatus.NEW or not post.created_page_id or post.message_thread_id is None:
            return AttachmentSyncResult("IGNORED")
        mapping = await TelegramAutoDraftRepository(self.session).mapping(post.chat_id, post.message_thread_id)
        if not mapping or not mapping.is_active or mapping.file_sync_mode == TelegramFileSyncMode.OFF:
            return AttachmentSyncResult("OFF", post.created_page_id)

        type_, file_id, unique_id, file_name, _ = media
        page = await self.session.get(BotPage, post.created_page_id, with_for_update=True)
        if not page or not page.published_revision_id:
            return await self._pending(post, page, None, media, "CONFLICT", "page is not published")
        matches = list((await self.session.scalars(
            select(PageAttachment).where(
                PageAttachment.revision_id == page.published_revision_id,
                PageAttachment.telegram_source_chat_id == post.chat_id,
                PageAttachment.telegram_source_message_id == source_message_id,
                PageAttachment.telegram_source_post_id == post.id,
            ).with_for_update()
        )).all())
        if len(matches) != 1:
            reason = "source attachment is missing" if not matches else "source attachment is ambiguous"
            return await self._pending(post, page, None, media, "CONFLICT", reason, source_message_id)
        attachment = matches[0]
        if attachment.type == type_ and (
            (unique_id and attachment.telegram_source_file_unique_id == unique_id)
            or (not unique_id and attachment.telegram_file_id == file_id)
        ):
            return AttachmentSyncResult("UNCHANGED", page.id)
        conflict = await self._conflict_reason(page)
        if mapping.file_sync_mode == TelegramFileSyncMode.REVIEW or conflict:
            return await self._pending(
                post, page, attachment, media,
                "CONFLICT" if conflict else "REVIEW", conflict, source_message_id,
            )
        await self._apply(post, page, attachment, media, source_message_id)
        return AttachmentSyncResult("APPLIED", page.id)

    async def prepare_manual(self, source_post_id: int, attachment_id: int) -> AttachmentSyncResult:
        """Prepare an explicit new-message replacement selected by an administrator."""
        post = await self.session.scalar(
            select(TelegramContentPost).where(TelegramContentPost.id == source_post_id).with_for_update()
        )
        media_items = list((await self.session.scalars(
            select(TelegramContentMedia).where(TelegramContentMedia.content_post_id == source_post_id)
            .order_by(TelegramContentMedia.position, TelegramContentMedia.id)
        )).all())
        attachment = await self.session.get(PageAttachment, attachment_id)
        if not post or post.status != TelegramContentStatus.NEW or not media_items or not attachment:
            raise ValueError("Материал больше недоступен")
        if len(media_items) != 1:
            raise ValueError("Для альбома требуется выбор каждого файла отдельно")
        media = media_items[0]
        page = await self.session.scalar(
            select(BotPage).where(BotPage.published_revision_id == attachment.revision_id).with_for_update()
        )
        if not page:
            raise ValueError("Выберите вложение опубликованной страницы")
        payload = (media.type, media.telegram_file_id, media.file_unique_id, media.file_name, media.file_size)
        return await self._pending(post, page, attachment, payload, "REVIEW", "manual new-message replacement", post.message_id)

    async def apply_pending(self, pending_id: int) -> int:
        pending = await self.session.scalar(
            select(TelegramAttachmentReplacement)
            .where(TelegramAttachmentReplacement.id == pending_id)
            .with_for_update()
        )
        if not pending:
            raise ValueError("Замена файла не найдена")
        if pending.status == "APPLIED":
            raise ValueError("Замена файла уже применена")
        if pending.status == "REJECTED":
            raise ValueError("Замена файла уже отклонена")
        if pending.status != "REVIEW":
            raise ValueError("Замена заблокирована из-за конфликта; создайте новое изменение")
        if not pending.attachment_id:
            raise ValueError("Исходное CMS-вложение больше недоступно")
        page = await self.session.get(BotPage, pending.page_id, with_for_update=True)
        post = await self.session.get(TelegramContentPost, pending.source_post_id, with_for_update=True)
        attachment = await self.session.get(PageAttachment, pending.attachment_id)
        if not page:
            raise ValueError("CMS-страница больше недоступна")
        if not post:
            raise ValueError("Исходная Telegram-публикация больше недоступна")
        if not attachment:
            raise ValueError("Исходное CMS-вложение больше недоступно")
        if page.published_revision_id != pending.base_revision_id:
            pending.status = "CONFLICT"
            pending.conflict_reason = "published revision changed after review was created"
            raise ValueError("Опубликованная ревизия страницы изменилась; создайте проверку заново")
        if page.draft_revision_id != page.published_revision_id:
            pending.status = "CONFLICT"
            pending.conflict_reason = "CMS page has unpublished changes"
            raise ValueError("У страницы есть неопубликованные ручные изменения")
        if attachment.revision_id != page.published_revision_id:
            pending.status = "CONFLICT"
            pending.conflict_reason = "attachment is not in current published revision"
            raise ValueError("Исходное вложение больше не относится к опубликованной ревизии")
        is_manual_new_message = pending.conflict_reason == "manual new-message replacement"
        if not is_manual_new_message and (
            attachment.telegram_source_post_id != post.id
            or attachment.telegram_source_chat_id != post.chat_id
            or attachment.telegram_source_message_id != pending.source_message_id
        ):
            pending.status = "CONFLICT"
            pending.conflict_reason = "Telegram source identity changed"
            raise ValueError("Source identity вложения изменилась; замена отменена")
        media = (pending.media_type, pending.new_file_id, pending.new_file_unique_id, pending.new_file_name, None)
        async with self.session.begin_nested():
            await self._apply(post, page, attachment, media, pending.source_message_id)
        pending.status = "APPLIED"
        return page.id

    async def reject_pending(self, pending_id: int) -> None:
        pending = await self.session.scalar(
            select(TelegramAttachmentReplacement)
            .where(TelegramAttachmentReplacement.id == pending_id).with_for_update()
        )
        if not pending:
            raise ValueError("Замена не найдена")
        if pending.status == "APPLIED":
            raise ValueError("Применённую замену нельзя отклонить")
        if pending.status == "REJECTED":
            return
        pending.status = "REJECTED"

    async def _conflict_reason(self, page: BotPage) -> str | None:
        if page.status != PageStatus.PUBLISHED or not page.is_active:
            return "page is not active and published"
        if page.draft_revision_id != page.published_revision_id:
            return "CMS page has unpublished changes"
        link = await self.session.scalar(
            select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.child_page_id == page.id)
        )
        if not link or not link.last_synced_revision_id:
            return "page has no confirmed Telegram sync revision"
        if link.last_synced_revision_id != page.published_revision_id:
            return "CMS page changed after Telegram sync"
        return None

    async def _pending(self, post, page, attachment, media, status: str, reason: str | None, source_message_id: int | None = None) -> AttachmentSyncResult:
        type_, file_id, unique_id, file_name, _ = media
        pending = await self.session.scalar(
            select(TelegramAttachmentReplacement)
            .where(TelegramAttachmentReplacement.source_post_id == post.id).with_for_update()
        )
        if pending is None:
            pending = TelegramAttachmentReplacement(
                source_post_id=post.id,
                page_id=page.id if page else post.created_page_id,
                media_type=type_, new_file_id=file_id, source_message_id=source_message_id or post.message_id,
            )
            self.session.add(pending)
        pending.page_id = page.id if page else post.created_page_id
        pending.attachment_id = attachment.id if attachment else None
        pending.base_revision_id = page.published_revision_id if page else None
        pending.media_type = type_
        pending.new_file_id = file_id
        pending.new_file_unique_id = unique_id
        pending.new_file_name = file_name
        pending.source_message_id = source_message_id or post.message_id
        pending.status = status
        pending.conflict_reason = reason
        await self.session.flush()
        self.session.add(AuditLog(
            admin_id=self.actor,
            action="TELEGRAM_ATTACHMENT_REVIEW_CREATED" if status == "REVIEW" else "TELEGRAM_ATTACHMENT_CONFLICT",
            entity_type="BotPage", entity_id=str(pending.page_id),
            new_data={"source_post_id": post.id, "pending_id": pending.id, "status": status},
        ))
        return AttachmentSyncResult(status, pending.page_id, pending.id)

    async def _apply(self, post, page, attachment, media, source_message_id: int) -> None:
        type_, file_id, unique_id, file_name, _ = media
        service = PageService(self.session)
        page = await service.ensure_editable_draft(page.id, self.actor)
        replacement = await self.session.scalar(
            select(PageAttachment).where(
                PageAttachment.revision_id == page.draft_revision_id,
                PageAttachment.telegram_source_chat_id == attachment.telegram_source_chat_id,
                PageAttachment.telegram_source_message_id == attachment.telegram_source_message_id,
                PageAttachment.telegram_source_post_id == attachment.telegram_source_post_id,
            )
        )
        if not replacement:
            raise ValueError("Связанное вложение не найдено в новой ревизии")
        replacement.type = type_
        replacement.telegram_file_id = file_id
        replacement.file_name = file_name
        replacement.telegram_source_post_id = post.id
        replacement.telegram_source_chat_id = post.chat_id
        replacement.telegram_source_message_id = source_message_id
        replacement.telegram_source_file_unique_id = unique_id
        await service.publish(page.id, self.actor)
        link = await self.session.scalar(
            select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.child_page_id == page.id).with_for_update()
        )
        if link:
            link.last_synced_revision_id = page.draft_revision_id
        current_media = await self.session.scalar(
            select(TelegramContentMedia).where(
                TelegramContentMedia.content_post_id == post.id,
                TelegramContentMedia.source_message_id == source_message_id,
            )
        )
        if current_media:
            current_media.type = type_
            current_media.telegram_file_id = file_id
            current_media.file_unique_id = unique_id
            current_media.file_name = file_name
        post.created_page_id = page.id
        if post.status == TelegramContentStatus.NEW:
            post.status = TelegramContentStatus.DRAFT_CREATED
            post.processed_by = self.actor
            post.processed_at = datetime.now(timezone.utc)
        self.session.add(AuditLog(
            admin_id=self.actor, action="TELEGRAM_ATTACHMENT_REPLACED", entity_type="BotPage",
            entity_id=str(page.id), new_data={"source_post_id": post.id, "revision_id": page.draft_revision_id},
        ))
