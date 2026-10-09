from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditLog, FAQItem, FAQItemTranslation, TelegramContentPost,
    TelegramFAQEditPending, TelegramFAQSyncLink,
)
from app.database.models.enums import LanguageCode, TelegramContentStatus, TelegramContentTarget, TelegramEditSyncMode
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.faq_revisions import create_faq_revision
from app.services.knowledge_sync import queue_faq_knowledge_sync
from app.services.telegram_edit_sync import source_fingerprint
from app.services.telegram_faq_parser import TelegramFAQParser
from app.services.telegram_content_parser import TelegramContentConfidence


@dataclass(frozen=True, slots=True)
class FAQEditSyncResult:
    status: str
    faq_id: int | None = None
    pending_id: int | None = None

def normalize_edit_date(value: datetime | int | None) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    if isinstance(value, int) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    raise TypeError(
        f"Unsupported edit_date type: {type(value).__name__}"
    )

class TelegramFAQEditSyncService:
    def __init__(self, session: AsyncSession, actor: int) -> None:
        self.session = session
        self.actor = actor
        self.parser = TelegramFAQParser()

    async def process(self, post_id: int, new_text: str, edit_date: datetime | None = None) -> FAQEditSyncResult:
        edit_date = normalize_edit_date(edit_date)
        post = await self.session.scalar(select(TelegramContentPost).where(TelegramContentPost.id == post_id).with_for_update())
        if (
            not post or post.status == TelegramContentStatus.NEW or not post.created_faq_id
            or post.created_page_id or post.message_thread_id is None
        ):
            return FAQEditSyncResult("IGNORED")
        mapping = await TelegramAutoDraftRepository(self.session).mapping(post.chat_id, post.message_thread_id)
        if not mapping or not mapping.is_active or mapping.content_target != TelegramContentTarget.FAQ:
            return FAQEditSyncResult("IGNORED", post.created_faq_id)
        if mapping.edit_sync_mode == TelegramEditSyncMode.OFF:
            return FAQEditSyncResult("OFF", post.created_faq_id)
        faq = await self.session.scalar(select(FAQItem).where(FAQItem.id == post.created_faq_id).with_for_update())
        if not faq:
            return FAQEditSyncResult("IGNORED", post.created_faq_id)
        link = await self.session.scalar(
            select(TelegramFAQSyncLink).where(TelegramFAQSyncLink.source_post_id == post.id).with_for_update()
        )
        if link is None:
            if not faq.current_revision_id:
                return FAQEditSyncResult("IGNORED", faq.id)
            link = TelegramFAQSyncLink(
                source_post_id=post.id, faq_id=faq.id,
                last_synced_revision_id=faq.current_revision_id,
                source_fingerprint=source_fingerprint(post.text or post.caption),
                last_synced_at=datetime.now(timezone.utc),
            )
            self.session.add(link)
            await self.session.flush()
        fingerprint = source_fingerprint(new_text)
        if link.source_fingerprint == fingerprint:
            return FAQEditSyncResult("UNCHANGED", faq.id)
        if edit_date and link.last_edit_date and edit_date <= link.last_edit_date:
            return FAQEditSyncResult("STALE", faq.id)

        parsed = self.parser.parse(new_text)
        complete = parsed.is_complete and parsed.confidence == TelegramContentConfidence.HIGH and all((
            parsed.question_ru, parsed.answer_ru, parsed.question_kk, parsed.answer_kk,
        ))
        conflict = faq.current_revision_id != link.last_synced_revision_id
        self.session.add(AuditLog(
            admin_id=self.actor, action="TELEGRAM_FAQ_EDIT_DETECTED", entity_type="FAQItem",
            entity_id=str(faq.id), new_data={"source_post_id": post.id, "fingerprint": fingerprint},
        ))
        if conflict:
            pending = await self._save_pending(post, faq, parsed, fingerprint, edit_date, "CONFLICT", "FAQ changed manually after Telegram sync")
            self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_FAQ_EDIT_CONFLICT", entity_type="FAQItem", entity_id=str(faq.id), new_data={"pending_id": pending.id, "source_post_id": post.id}))
            return FAQEditSyncResult("CONFLICT", faq.id, pending.id)
        if mapping.edit_sync_mode == TelegramEditSyncMode.REVIEW or not complete:
            status = "REVIEW" if complete else "INCOMPLETE"
            pending = await self._save_pending(post, faq, parsed, fingerprint, edit_date, status, None if complete else parsed.diagnostic)
            self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_FAQ_EDIT_REVIEW_CREATED", entity_type="FAQItem", entity_id=str(faq.id), new_data={"pending_id": pending.id, "status": status}))
            return FAQEditSyncResult(status, faq.id, pending.id)
        await self._apply(post, faq, link, parsed, fingerprint, edit_date, "TELEGRAM_FAQ_EDIT_AUTO_APPLIED")
        return FAQEditSyncResult("APPLIED", faq.id)

    async def _save_pending(self, post, faq, parsed, fingerprint, edit_date, status, reason) -> TelegramFAQEditPending:
        pending = await self.session.scalar(
            select(TelegramFAQEditPending).where(TelegramFAQEditPending.source_post_id == post.id).with_for_update()
        )
        if pending and pending.source_edit_date and edit_date and edit_date < pending.source_edit_date:
            return pending
        if pending is None:
            pending = TelegramFAQEditPending(source_post_id=post.id, faq_id=faq.id, source_fingerprint=fingerprint)
            self.session.add(pending)
        pending.base_revision_id = faq.current_revision_id
        pending.source_fingerprint = fingerprint
        pending.question_ru = parsed.question_ru
        pending.answer_ru = parsed.answer_ru
        pending.question_kk = parsed.question_kk
        pending.answer_kk = parsed.answer_kk
        pending.source_edit_date = edit_date
        pending.status = status
        pending.conflict_reason = reason
        await self.session.flush()
        return pending

    async def _apply(self, post, faq, link, parsed, fingerprint, edit_date, action: str) -> None:
        translations = list((await self.session.scalars(
            select(FAQItemTranslation).where(FAQItemTranslation.faq_id == faq.id).with_for_update()
        )).all())
        values = {
            LanguageCode.RU: (parsed.question_ru, parsed.answer_ru),
            LanguageCode.KK: (parsed.question_kk, parsed.answer_kk),
        }
        for language, (question, answer) in values.items():
            translation = next((item for item in translations if item.language_code == language), None)
            if translation:
                translation.question, translation.answer = question, answer
            else:
                self.session.add(FAQItemTranslation(faq_id=faq.id, language_code=language, question=question, answer=answer))
        faq.question, faq.answer = parsed.question_ru, parsed.answer_ru
        revision = await create_faq_revision(self.session, faq, self.actor, source_type="TELEGRAM", source_post_id=post.id)
        link.last_synced_revision_id = revision.id
        link.source_fingerprint = fingerprint
        link.last_edit_date = edit_date
        link.last_synced_at = datetime.now(timezone.utc)
        post.text = parsed.raw_text if hasattr(parsed, "raw_text") else post.text
        await queue_faq_knowledge_sync(self.session, faq.id, self.actor, revision_id=revision.id)
        self.session.add(AuditLog(admin_id=self.actor, action=action, entity_type="FAQItem", entity_id=str(faq.id), new_data={"source_post_id": post.id, "revision_id": revision.id}))

    async def apply_pending(self, pending_id: int) -> int:
        pending = await self.session.scalar(select(TelegramFAQEditPending).where(TelegramFAQEditPending.id == pending_id).with_for_update())
        if not pending:
            raise ValueError("Изменение FAQ не найдено")
        if pending.status == "APPLIED":
            return pending.faq_id
        if pending.status != "REVIEW" or not all((pending.question_ru, pending.answer_ru, pending.question_kk, pending.answer_kk)):
            raise ValueError("Изменение FAQ недоступно для применения")
        faq = await self.session.scalar(select(FAQItem).where(FAQItem.id == pending.faq_id).with_for_update())
        post = await self.session.scalar(select(TelegramContentPost).where(TelegramContentPost.id == pending.source_post_id).with_for_update())
        link = await self.session.scalar(select(TelegramFAQSyncLink).where(TelegramFAQSyncLink.source_post_id == pending.source_post_id).with_for_update())
        mapping = await TelegramAutoDraftRepository(self.session).mapping(post.chat_id, post.message_thread_id) if post and post.message_thread_id is not None else None
        identity_valid = bool(
            post and post.status != TelegramContentStatus.NEW and post.created_faq_id == pending.faq_id
            and link and link.faq_id == pending.faq_id
            and mapping and mapping.is_active and mapping.content_target == TelegramContentTarget.FAQ
        )
        if not faq or not identity_valid or faq.current_revision_id != pending.base_revision_id or link.last_synced_revision_id != pending.base_revision_id:
            pending.status = "CONFLICT"
            pending.conflict_reason = "FAQ changed after review was created"
            raise ValueError("FAQ был изменён; требуется повторная проверка")
        parsed = type("PendingFAQ", (), {
            "question_ru": pending.question_ru, "answer_ru": pending.answer_ru,
            "question_kk": pending.question_kk, "answer_kk": pending.answer_kk,
        })()
        await self._apply(post, faq, link, parsed, pending.source_fingerprint, pending.source_edit_date, "TELEGRAM_FAQ_EDIT_MANUALLY_APPLIED")
        pending.status = "APPLIED"
        return faq.id

    async def reject_pending(self, pending_id: int) -> None:
        pending = await self.session.scalar(select(TelegramFAQEditPending).where(TelegramFAQEditPending.id == pending_id).with_for_update())
        if not pending:
            raise ValueError("Изменение FAQ не найдено")
        if pending.status == "APPLIED":
            raise ValueError("Изменение FAQ уже применено")
        pending.status = "REJECTED"
