from datetime import datetime, timezone
from dataclasses import dataclass

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, PageAttachment, TelegramContentMedia, TelegramContentPost
from app.database.models.enums import LanguageCode, MediaType, TelegramContentStatus
from app.database.repositories.channel_posts import TelegramContentRepository
from app.services.cms import FAQService
from app.services.pages import PageService

MAX_TRANSFER_DOCUMENT_SIZE = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestResult:
    post: TelegramContentPost
    created: bool
    media_count: int


def source_text(post: TelegramContentPost) -> str:
    return (post.text or post.caption or "").strip()


def suggested_title(value: str) -> str:
    first = next((line.strip() for line in value.splitlines() if line.strip()), "Публикация канала")
    return first[:120].rstrip(" .,:;—-") or "Публикация канала"


def message_media(message: Message) -> tuple[MediaType, str, str | None, str | None, int | None] | None:
    if message.photo:
        photo = message.photo[-1]
        return MediaType.PHOTO, photo.file_id, photo.file_unique_id, None, photo.file_size
    if message.document:
        document = message.document
        return MediaType.DOCUMENT, document.file_id, document.file_unique_id, document.file_name, document.file_size
    return None


class TelegramContentImportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TelegramContentRepository(session)

    async def ingest(self, message: Message, *, edited: bool = False) -> IngestResult:
        chat_id, message_id = message.chat.id, message.message_id
        thread_id = getattr(message, "message_thread_id", None)
        post = await self.repo.by_source(chat_id, message_id)
        if not post and message.media_group_id:
            post = await self.repo.by_group(chat_id, message.media_group_id, thread_id)
        created = post is None
        if post is None:
            post = TelegramContentPost(
                chat_id=chat_id,
                message_id=message_id,
                media_group_id=message.media_group_id,
                message_thread_id=thread_id,
                chat_type=str(getattr(message.chat.type, "value", message.chat.type)),
                chat_title=getattr(message.chat, "title", None),
                status=TelegramContentStatus.NEW,
                text=message.text,
                caption=message.caption,
                telegram_created_at=message.date,
            )
            self.session.add(post)
            await self.session.flush()
        elif post.status == TelegramContentStatus.NEW:
            if message.text is not None:
                post.text = message.text
            if message.caption is not None:
                post.caption = message.caption
            if edited and message.date:
                post.telegram_created_at = message.date
        media_count, media_exists = await self.repo.media_state(post.id, message_id)
        media = message_media(message)
        if media and post.status == TelegramContentStatus.NEW and not media_exists:
            type_, file_id, unique_id, file_name, file_size = media
            item = TelegramContentMedia(
                content_post_id=post.id, source_message_id=message_id, type=type_,
                telegram_file_id=file_id, file_unique_id=unique_id, file_name=file_name,
                file_size=file_size, position=media_count,
            )
            self.session.add(item)
            media_count += 1
        await self.session.flush()
        return IngestResult(post, created, media_count)

    async def ignore(self, item_id: int, chat_id: int, actor: int) -> TelegramContentPost:
        post = await self._new_locked(item_id, chat_id)
        post.status = TelegramContentStatus.IGNORED
        post.processed_by = actor
        post.processed_at = datetime.now(timezone.utc)
        self._audit(actor, "CHANNEL_POST_IGNORED", post)
        return post

    async def restore(self, item_id: int, chat_id: int, actor: int) -> TelegramContentPost:
        post = await self.repo.by_id(item_id, chat_id, lock=True)
        if not post or post.status != TelegramContentStatus.IGNORED:
            raise ValueError("Публикация не может быть возвращена")
        post.status = TelegramContentStatus.NEW
        post.processed_by = None
        post.processed_at = None
        self._audit(actor, "CHANNEL_POST_RESTORED", post)
        return post

    async def create_page(
        self, item_id: int, chat_id: int, actor: int, *,
        title_ru: str, text_ru: str, title_kk: str | None = None, text_kk: str | None = None,
    ):
        post = await self._new_locked(item_id, chat_id)
        page = await PageService(self.session).create(title_ru, text_ru, actor)
        await self.session.flush()
        if title_kk and text_kk:
            await PageService(self.session).update_translation(page.id, actor, LanguageCode.KK, title=title_kk, text=text_kk)
        revision_id = page.draft_revision_id
        if not revision_id:
            raise RuntimeError("Draft revision was not created")
        for media in post.media:
            if media.type == MediaType.DOCUMENT and media.file_size and media.file_size > MAX_TRANSFER_DOCUMENT_SIZE:
                continue
            self.session.add(PageAttachment(
                revision_id=revision_id, type=media.type, telegram_file_id=media.telegram_file_id,
                file_name=media.file_name, position=media.position, is_active=True,
            ))
        post.status = TelegramContentStatus.DRAFT_CREATED
        post.created_page_id = page.id
        post.processed_by = actor
        post.processed_at = datetime.now(timezone.utc)
        self._audit(actor, "CHANNEL_POST_PAGE_CREATED", post, {"page_id": page.id})
        return page

    async def create_faq(
        self, item_id: int, chat_id: int, actor: int, *,
        question_ru: str, answer_ru: str, question_kk: str | None = None, answer_kk: str | None = None,
    ):
        post = await self._new_locked(item_id, chat_id)
        faq = await FAQService(self.session).create(actor, question_ru, answer_ru, question_kk, answer_kk)
        post.status = TelegramContentStatus.DRAFT_CREATED
        post.created_faq_id = faq.id
        post.processed_by = actor
        post.processed_at = datetime.now(timezone.utc)
        self._audit(actor, "CHANNEL_POST_FAQ_CREATED", post, {"faq_id": faq.id})
        return faq

    async def _new_locked(self, item_id: int, chat_id: int) -> TelegramContentPost:
        post = await self.repo.by_id(item_id, chat_id, lock=True)
        if not post:
            raise ValueError("Публикация не найдена")
        if post.status != TelegramContentStatus.NEW:
            raise ValueError("Публикация уже обработана")
        return post

    def _audit(self, actor: int, action: str, post: TelegramContentPost, new: dict | None = None) -> None:
        self.session.add(AuditLog(
            admin_id=actor, action=action, entity_type="TelegramContentPost", entity_id=str(post.id), new_data=new,
        ))
