import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, BotPage, TelegramAutoDraftLink, TelegramContentPost, TelegramEditPending
from app.database.models.enums import LanguageCode, PageStatus, TelegramEditSyncMode
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.pages import PageService
from app.services.telegram_content_parser import TelegramContentConfidence, TelegramContentParser


def source_fingerprint(value: str | None) -> str:
    normalized = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EditSyncResult:
    status: str
    page_id: int | None = None
    pending_id: int | None = None


class TelegramEditSyncService:
    def __init__(self, session: AsyncSession, actor: int) -> None:
        self.session = session
        self.actor = actor
        self.parser = TelegramContentParser()

    async def process(self, post_id: int, new_text: str) -> EditSyncResult:
        post = await self.session.scalar(
            select(TelegramContentPost).where(TelegramContentPost.id == post_id).with_for_update()
        )
        if not post or post.status.value == "NEW" or not post.created_page_id or post.message_thread_id is None:
            return EditSyncResult("IGNORED")
        mapping = await TelegramAutoDraftRepository(self.session).mapping(post.chat_id, post.message_thread_id)
        if not mapping or not mapping.is_active or mapping.edit_sync_mode == TelegramEditSyncMode.OFF:
            return EditSyncResult("OFF", post.created_page_id)
        fingerprint = source_fingerprint(new_text)
        link = await self.session.scalar(
            select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.source_post_id == post.id).with_for_update()
        )
        if link and link.source_fingerprint == fingerprint:
            return EditSyncResult("UNCHANGED", post.created_page_id)
        if not link and source_fingerprint(post.text or post.caption) == fingerprint:
            # A media-only edited_message for an attached material belongs to
            # TelegramAttachmentSyncService, not to the text revision flow.
            return EditSyncResult("UNCHANGED", post.created_page_id)
        self.session.add(AuditLog(
            admin_id=self.actor, action="TELEGRAM_EDIT_DETECTED", entity_type="TelegramContentPost",
            entity_id=str(post.id), new_data={"page_id": post.created_page_id, "fingerprint": fingerprint},
        ))
        page = await self.session.get(BotPage, post.created_page_id, with_for_update=True)
        if not link:
            pending = await self._save_pending(post, page, fingerprint, None, "MATERIAL_REVIEW", "material or unconfirmed source")
            return EditSyncResult("REVIEW", post.created_page_id, pending.id)
        conflict = (
            not page
            or page.status != PageStatus.PUBLISHED
            or not page.is_active
            or page.draft_revision_id != page.published_revision_id
            or (link.last_synced_revision_id is not None and link.last_synced_revision_id != page.published_revision_id)
        )
        parsed = self.parser.parse(new_text)
        complete = all((parsed.title_ru, parsed.text_ru, parsed.title_kk, parsed.text_kk))
        strict = parsed.kind.value == "BILINGUAL" and parsed.confidence == TelegramContentConfidence.HIGH and complete
        if conflict:
            pending = await self._save_pending(post, page, fingerprint, parsed, "CONFLICT", "CMS page changed after Telegram sync")
            self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_EDIT_CONFLICT", entity_type="BotPage", entity_id=str(post.created_page_id), new_data={"source_post_id": post.id, "pending_id": pending.id}))
            return EditSyncResult("CONFLICT", post.created_page_id, pending.id)
        if mapping.edit_sync_mode == TelegramEditSyncMode.REVIEW or not strict:
            status = "REVIEW" if strict else "INCOMPLETE"
            pending = await self._save_pending(post, page, fingerprint, parsed, status, None if strict else "content is not complete BILINGUAL/HIGH")
            self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_EDIT_REVIEW_CREATED", entity_type="BotPage", entity_id=str(post.created_page_id), new_data={"source_post_id": post.id, "pending_id": pending.id, "status": status}))
            return EditSyncResult(status, post.created_page_id, pending.id)
        await self._apply(page, link, parsed, fingerprint)
        post.text = new_text
        self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_EDIT_APPLIED", entity_type="BotPage", entity_id=str(page.id), new_data={"source_post_id": post.id, "revision_id": page.published_revision_id}))
        return EditSyncResult("APPLIED", page.id)

    async def _save_pending(self, post, page, fingerprint, parsed, status, reason):
        pending = await self.session.scalar(select(TelegramEditPending).where(TelegramEditPending.source_post_id == post.id).with_for_update())
        if pending is None:
            pending = TelegramEditPending(source_post_id=post.id, page_id=post.created_page_id, source_fingerprint=fingerprint)
            self.session.add(pending)
        pending.base_revision_id = page.published_revision_id if page else None
        pending.source_fingerprint = fingerprint
        pending.title_ru = getattr(parsed, "title_ru", None)
        pending.text_ru = getattr(parsed, "text_ru", None)
        pending.title_kk = getattr(parsed, "title_kk", None)
        pending.text_kk = getattr(parsed, "text_kk", None)
        pending.status = status
        pending.conflict_reason = reason
        await self.session.flush()
        return pending

    async def _apply(self, page, link, parsed, fingerprint) -> None:
        service = PageService(self.session)
        await service.update_draft(page.id, self.actor, title=parsed.title_ru, text=parsed.text_ru)
        await service.update_translation(page.id, self.actor, LanguageCode.KK, title=parsed.title_kk, text=parsed.text_kk)
        await service.publish(page.id, self.actor)
        link.last_synced_revision_id = page.draft_revision_id
        link.source_fingerprint = fingerprint

    async def apply_pending(self, pending_id: int) -> int:
        pending = await self.session.scalar(select(TelegramEditPending).where(TelegramEditPending.id == pending_id).with_for_update())
        if not pending or pending.status != "REVIEW":
            raise ValueError("Изменение недоступно для применения")
        page = await self.session.get(BotPage, pending.page_id, with_for_update=True)
        link = await self.session.scalar(select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.source_post_id == pending.source_post_id).with_for_update())
        if not page or not link or page.published_revision_id != pending.base_revision_id or page.draft_revision_id != page.published_revision_id:
            pending.status = "CONFLICT"
            pending.conflict_reason = "CMS page changed after review was created"
            raise ValueError("Страница была изменена; требуется повторная проверка")
        parsed = type("PendingContent", (), {
            "title_ru": pending.title_ru, "text_ru": pending.text_ru,
            "title_kk": pending.title_kk, "text_kk": pending.text_kk,
        })()
        await self._apply(page, link, parsed, pending.source_fingerprint)
        pending.status = "APPLIED"
        return page.id

    async def reject_pending(self, pending_id: int) -> None:
        pending = await self.session.scalar(select(TelegramEditPending).where(TelegramEditPending.id == pending_id).with_for_update())
        if not pending:
            raise ValueError("Изменение не найдено")
        pending.status = "REJECTED"
