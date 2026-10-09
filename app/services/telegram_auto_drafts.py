import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.callbacks import AdminPageCallback, ChannelPostCallback, FAQCallback
from app.config import Settings
from app.database.models import Admin, AuditLog, BotPage, TelegramAutoDraftLink, TelegramContentPost
from app.database.models.enums import PageStatus, TelegramContentStatus, TelegramContentTarget, TelegramProcessingMode
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.channel_import import TelegramContentImportService, source_text
from app.services.permissions import Permission, ROLE_PERMISSIONS
from app.services.pages import PageService
from app.services.knowledge_sync import process_knowledge_job, queue_faq_knowledge_sync, recover_knowledge_jobs
from app.services.telegram_content_parser import TelegramContentConfidence, TelegramContentParseKind, TelegramContentParser
from app.services.telegram_edit_sync import source_fingerprint
from app.services.telegram_faq_parser import TelegramFAQParser
from app.services.telegram_publication_grouper import SelectedPublicationMaterial, TelegramPublicationGrouper
from app.database.repositories.channel_posts import TelegramContentRepository
from app.utils.presentation import html_text, text_preview

logger = logging.getLogger(__name__)
AUTO_POLL_SECONDS = 5
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class AutoDraftCreated:
    source_post_id: int
    page_id: int
    title: str
    section_title: str
    media_count: int
    published: bool
    content_target: TelegramContentTarget = TelegramContentTarget.PAGE
    faq_id: int | None = None


def processing_mode(mapping) -> TelegramProcessingMode:
    value = getattr(mapping, "processing_mode", None)
    if value:
        return TelegramProcessingMode(value)
    return TelegramProcessingMode.AUTO_DRAFT if mapping.auto_draft_enabled else TelegramProcessingMode.MANUAL


class TelegramAutoDraftService:
    def __init__(self, session: AsyncSession, configured_chat_id: int, actor: int) -> None:
        self.session = session
        self.chat_id = configured_chat_id
        self.actor = actor
        self.repo = TelegramAutoDraftRepository(session)
        self.parser = TelegramContentParser()
        self.faq_parser = TelegramFAQParser()

    async def schedule(self, post: TelegramContentPost, media_count: int = 0) -> None:
        logger.warning(
            "AUTO SCHEDULE ENTER post_id=%s chat_id=%s thread_id=%s status=%s",
            post.id,
            post.chat_id,
            post.message_thread_id,
            post.status,
        )
        if post.chat_id != self.chat_id or post.status != TelegramContentStatus.NEW or post.message_thread_id is None:
            return
        config = await self.repo.settings(post.chat_id)
        mapping = await self.repo.mapping(post.chat_id, post.message_thread_id)
        if not config or not config.auto_draft_enabled or not config.auto_draft_enabled_at:
            return
        if not mapping or not mapping.is_active or processing_mode(mapping) == TelegramProcessingMode.MANUAL:
            return
        received = post.telegram_created_at or post.created_at or datetime.now(timezone.utc)
        mode_boundary = mapping.processing_mode_enabled_at or config.auto_draft_enabled_at
        if received < config.auto_draft_enabled_at or (mode_boundary and received < mode_boundary):
            return
        deadline = datetime.now(timezone.utc) + timedelta(seconds=config.settle_seconds)
        value = source_text(post)
        is_probable_material = media_count > 0 and len(value) <= 240
        if mapping.content_target == TelegramContentTarget.FAQ:
            faq = self.faq_parser.parse(value)
            recognizable = faq.is_complete or faq.diagnostic in {"FAQ_INCOMPLETE", "FAQ_AMBIGUOUS"}
        else:
            recognizable = self.parser.parse(value).kind != TelegramContentParseKind.UNKNOWN
        if not is_probable_material and recognizable:
            post.auto_process_after = deadline
            post.auto_processing_attempts = 0
            post.auto_last_error = None
            return
        pending = await self.repo.pending_source(post.chat_id, post.message_thread_id, post.message_id)
        if pending:
            pending.auto_process_after = deadline

    async def process_claimed(self, post: TelegramContentPost) -> AutoDraftCreated | None:
        post.auto_process_after = None
        if post.chat_id != self.chat_id or post.message_thread_id is None or post.status != TelegramContentStatus.NEW or post.created_faq_id:
            return None
        config = await self.repo.settings(post.chat_id)
        mapping = await self.repo.mapping(post.chat_id, post.message_thread_id)
        parent = await self.session.get(BotPage, mapping.parent_page_id) if mapping and mapping.parent_page_id else None
        received = post.telegram_created_at or post.created_at
        if not config or not config.auto_draft_enabled or not config.auto_draft_enabled_at:
            return None
        if not mapping or not mapping.is_active or processing_mode(mapping) == TelegramProcessingMode.MANUAL:
            return None
        if mapping.content_target == TelegramContentTarget.PAGE and (not parent or not parent.is_active or parent.status != PageStatus.PUBLISHED):
            post.auto_last_error = "parent page is not active and published"
            return None
        mode_boundary = mapping.processing_mode_enabled_at or config.auto_draft_enabled_at
        if not received or received < config.auto_draft_enabled_at or (mode_boundary and received < mode_boundary):
            return None
        if mapping.content_target == TelegramContentTarget.FAQ:
            media_count = await TelegramContentRepository(self.session).media_count(post.id)
            parsed_faq = self.faq_parser.parse(source_text(post))
            if media_count:
                post.auto_last_error = "FAQ_REVIEW_REQUIRED: attachments are not supported"
                self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_FAQ_REVIEW_REQUIRED", entity_type="TelegramContentPost", entity_id=str(post.id), new_data={"reason": "attachments"}))
                return None
            if not parsed_faq.is_complete or parsed_faq.confidence != TelegramContentConfidence.HIGH:
                post.auto_last_error = parsed_faq.diagnostic
                self.session.add(AuditLog(admin_id=self.actor, action="TELEGRAM_FAQ_REVIEW_REQUIRED", entity_type="TelegramContentPost", entity_id=str(post.id), new_data={"reason": parsed_faq.diagnostic}))
                return None
            mode = processing_mode(mapping)
            faq = await TelegramContentImportService(self.session).create_faq(
                post.id, post.chat_id, self.actor,
                question_ru=parsed_faq.question_ru or "", answer_ru=parsed_faq.answer_ru or "",
                question_kk=parsed_faq.question_kk, answer_kk=parsed_faq.answer_kk,
                is_active=mode == TelegramProcessingMode.AUTO_PUBLISH,
            )
            action = "TELEGRAM_FAQ_AUTO_PUBLISHED" if faq.is_active else "TELEGRAM_FAQ_DRAFT_CREATED"
            self.session.add(AuditLog(admin_id=self.actor, action=action, entity_type="FAQItem", entity_id=str(faq.id), new_data={"source_post_id": post.id, "thread_id": post.message_thread_id}))
            if faq.is_active:
                await queue_faq_knowledge_sync(self.session, faq.id, self.actor)
            topic_names = await self.repo.topic_names(post.chat_id, [post.message_thread_id])
            section_title = topic_names.get(post.message_thread_id, f"Тема #{post.message_thread_id}")
            return AutoDraftCreated(post.id, 0, parsed_faq.question_ru or "FAQ", section_title, 0, faq.is_active, TelegramContentTarget.FAQ, faq.id)

        parsed = self.parser.parse(source_text(post))
        complete = all((parsed.title_ru, parsed.text_ru, parsed.title_kk, parsed.text_kk))
        if parsed.kind != TelegramContentParseKind.BILINGUAL or parsed.confidence != TelegramContentConfidence.HIGH or not complete:
            post.auto_last_error = "publication is not complete BILINGUAL/HIGH"
            return None
        materials: tuple[SelectedPublicationMaterial, ...] = ()
        content_repo = TelegramContentRepository(self.session)
        material_count = await content_repo.media_count(post.id)
        if mapping.auto_attach_materials:
            grouped = await TelegramPublicationGrouper(content_repo, self.parser).group(post)
            materials = tuple(SelectedPublicationMaterial(item.content_post_id, item.suggested_language) for item in grouped.materials)
            material_count += sum(len(item.files) for item in grouped.materials)
        page = await TelegramContentImportService(self.session).create_page(
            post.id, post.chat_id, self.actor,
            title_ru=parsed.title_ru or "", text_ru=parsed.text_ru or "",
            title_kk=parsed.title_kk, text_kk=parsed.text_kk, materials=materials,
        )
        link = TelegramAutoDraftLink(
            source_post_id=post.id, child_page_id=page.id,
            parent_page_id=mapping.parent_page_id, mapping_id=mapping.id,
            last_synced_revision_id=page.draft_revision_id,
            source_fingerprint=source_fingerprint(source_text(post)),
        )
        self.session.add(link)
        self.session.add(AuditLog(
            admin_id=self.actor, action="TELEGRAM_AUTO_DRAFT_CREATED", entity_type="BotPage",
            entity_id=str(page.id), new_data={"source_post_id": post.id, "parent_page_id": mapping.parent_page_id, "thread_id": post.message_thread_id},
        ))
        await self.session.flush()
        mode = processing_mode(mapping)
        published = mode == TelegramProcessingMode.AUTO_PUBLISH
        if published:
            await PageService(self.session).publish(page.id, self.actor)
            self.session.add(AuditLog(
                admin_id=self.actor, action="TELEGRAM_AUTO_PUBLISHED", entity_type="BotPage",
                entity_id=str(page.id), new_data={"source_post_id": post.id, "parent_page_id": mapping.parent_page_id, "thread_id": post.message_thread_id, "media_count": material_count},
            ))
        topic_names = await self.repo.topic_names(post.chat_id, [post.message_thread_id])
        section_title = topic_names.get(post.message_thread_id, f"Тема #{post.message_thread_id}")
        return AutoDraftCreated(post.id, page.id, parsed.title_ru or page.key, section_title, material_count, published)


async def _notify_created(bot: Bot, session: AsyncSession, settings: Settings, result: AutoDraftCreated) -> None:
    if settings.content_chat_id is None or not await TelegramAutoDraftRepository(session).notifications_enabled(
        settings.content_chat_id,
        settings.content_channel_notify_admins,
    ):
        return
    admins = list((await session.scalars(select(Admin).where(Admin.is_active.is_(True)))).all())
    recipients = {item.telegram_id for item in admins if Permission.CONTENT in ROLE_PERMISSIONS.get(item.role, set())}
    recipients.add(settings.super_admin_id)
    target_button = InlineKeyboardButton(
        text="❓ Открыть FAQ",
        callback_data=FAQCallback(action="open", item_id=result.faq_id or 0).pack(),
    ) if result.content_target == TelegramContentTarget.FAQ else InlineKeyboardButton(
        text="👁 Открыть страницу" if result.published else "📄 Открыть черновик",
        callback_data=AdminPageCallback(action="open", page_id=result.page_id).pack(),
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [target_button],
        [InlineKeyboardButton(text="📥 Исходная публикация", callback_data=ChannelPostCallback(action="open", item_id=result.source_post_id).pack())],
    ])
    for recipient in recipients:
        try:
            if result.content_target == TelegramContentTarget.FAQ:
                text = ("🚀 <b>FAQ автоматически опубликован</b>" if result.published else "📝 <b>Создан черновик FAQ</b>") + f"\n\n❓ {html_text(text_preview(result.title, 120))}"
            elif result.published:
                text = (
                    "🚀 <b>Автоматически опубликовано</b>\n\n"
                    f"📂 Раздел: {html_text(result.section_title)}\n"
                    f"📄 Страница: {html_text(text_preview(result.title, 120))}\n"
                    "🌐 RU + KK\n"
                    f"📎 Материалов: {result.media_count}\n\n"
                    "✅ Кнопка добавлена в родительский раздел."
                )
            else:
                text = f"📝 <b>Создан авточерновик</b>\n\n{html_text(text_preview(result.title, 120))}"
            await bot.send_message(recipient, text, reply_markup=markup)
        except TelegramAPIError as exc:
            logger.warning("Auto-draft notification failed admin_id=%s error=%s", recipient, type(exc).__name__)


async def run_auto_draft_processor(bot: Bot, factory: async_sessionmaker[AsyncSession], settings: Settings, stop: asyncio.Event, knowledge_index=None) -> None:
    try:
        async with factory() as recovery_session:
            repaired = await PageService(recovery_session).repair_published_auto_draft_buttons(settings.super_admin_id)
            await recovery_session.commit()
            if repaired:
                logger.info("Recovered missing auto-draft parent buttons count=%s", repaired)
    except Exception:
        logger.exception("Auto-draft parent button recovery failed")
    await recover_knowledge_jobs(factory)
    while not stop.is_set():
        result = None
        post_id = None
        try:
            async with factory() as session:
                post = await TelegramAutoDraftRepository(session).claim_due(datetime.now(timezone.utc)) if settings.content_chat_id is not None else None
                if post and settings.content_chat_id is not None:
                    post_id = post.id
                    result = await TelegramAutoDraftService(session, settings.content_chat_id, settings.super_admin_id).process_claimed(post)
                    await session.commit()
                    if result:
                        await _notify_created(bot, session, settings, result)
        except Exception as exc:
            logger.exception("Auto-draft processing failed post_id=%s", post_id)
            if post_id:
                async with factory() as retry_session:
                    failed = await retry_session.get(TelegramContentPost, post_id, with_for_update=True)
                    if failed and failed.status == TelegramContentStatus.NEW:
                        failed.auto_processing_attempts += 1
                        failed.auto_last_error = type(exc).__name__[:255]
                        failed.auto_process_after = (datetime.now(timezone.utc) + timedelta(seconds=30 * failed.auto_processing_attempts)) if failed.auto_processing_attempts < MAX_ATTEMPTS else None
                        mapping = None
                        if failed.message_thread_id is not None:
                            mapping = await TelegramAutoDraftRepository(retry_session).mapping(
                                failed.chat_id, failed.message_thread_id,
                            )
                        if mapping and processing_mode(mapping) == TelegramProcessingMode.AUTO_PUBLISH:
                            retry_session.add(AuditLog(
                                admin_id=settings.super_admin_id, action="TELEGRAM_AUTO_PUBLISH_FAILED",
                                entity_type="TelegramContentPost", entity_id=str(failed.id),
                                new_data={"error_type": type(exc).__name__, "attempt": failed.auto_processing_attempts, "retry_scheduled": failed.auto_process_after is not None},
                            ))
                        await retry_session.commit()
        await process_knowledge_job(factory, knowledge_index, settings.super_admin_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=AUTO_POLL_SECONDS)
        except TimeoutError:
            pass
