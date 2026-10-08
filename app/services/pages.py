import re

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import AIKnowledgeSyncJob, AuditLog, BotButton, BotPage, BotPageRevision, BotPageRevisionTranslation, PageAttachment, TelegramAutoDraftLink, TelegramContentPost, TelegramTopicPageMapping
from app.database.models.enums import ButtonAction, LanguageCode, PageStatus
from app.database.repositories.pages import PageRepository


class PageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PageRepository(session)

    @staticmethod
    def _slugify(value: str) -> str:
        table = str.maketrans({
            "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
            "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
            "х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
        })
        slug = value.lower().translate(table)
        slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
        return slug[:48] or "page"

    async def unique_key(self, title: str) -> str:
        base = self._slugify(title)
        candidate, suffix = base, 2
        while await self.repo.key_exists(candidate):
            tail = f"_{suffix}"
            candidate = f"{base[:64-len(tail)]}{tail}"
            suffix += 1
        return candidate

    async def create(self, title: str, text: str, admin_id: int) -> BotPage:
        key = await self.unique_key(title)
        pages = await self.repo.all()
        page = BotPage(key=key, status=PageStatus.DRAFT, position=len(pages), is_active=True, created_by=admin_id, updated_by=admin_id)
        self.session.add(page)
        await self.session.flush()
        revision = BotPageRevision(page_id=page.id, title=title, text=text, created_by=admin_id)
        self.session.add(revision)
        await self.session.flush()
        self.session.add(BotPageRevisionTranslation(revision_id=revision.id, language_code=LanguageCode.RU, title=title, text=text))
        page.draft_revision_id = revision.id
        self.session.add(AuditLog(admin_id=admin_id, action="PAGE_CREATED", entity_type="BotPage", entity_id=str(page.id), new_data={"key": key, "title": title, "status": PageStatus.DRAFT.value}))
        return page

    async def ensure_editable_draft(self, page_id: int, admin_id: int) -> BotPage:
        page = await self.repo.by_id(page_id)

        if not page or not page.draft_revision:
            raise ValueError("Страница не найдена")

        if page.draft_revision_id != page.published_revision_id:
            return page

        source = page.draft_revision

        revision = BotPageRevision(
            page_id=page.id,
            title=source.title,
            text=source.text,
            media_type=source.media_type,
            media_file_id=source.media_file_id,
            created_by=admin_id,
        )

        self.session.add(revision)
        await self.session.flush()

        for value in source.translations:
            self.session.add(
                BotPageRevisionTranslation(
                    revision_id=revision.id,
                    language_code=value.language_code,
                    title=value.title,
                    text=value.text,
                )
            )

        for value in source.attachments:
            self.session.add(
                PageAttachment(
                    revision_id=revision.id,
                    type=value.type,
                    telegram_file_id=value.telegram_file_id,
                    file_name=value.file_name,
                    caption_ru=value.caption_ru,
                    caption_kk=value.caption_kk,
                    position=value.position,
                    is_active=value.is_active,
                    telegram_source_post_id=value.telegram_source_post_id,
                    telegram_source_chat_id=value.telegram_source_chat_id,
                    telegram_source_message_id=value.telegram_source_message_id,
                    telegram_source_file_unique_id=value.telegram_source_file_unique_id,
                )
            )

        page.draft_revision_id = revision.id
        page.draft_revision = revision
        page.updated_by = admin_id

        await self.session.flush()

        return page

    async def deletion_dependencies(self, page_id: int) -> list[tuple[object, BotPage]]:
        page = await self.repo.by_id(page_id)
        return await self.repo.incoming_page_buttons(page.key) if page else []

    async def delete(self, page_id: int, admin_id: int) -> None:
        page = await self.repo.by_id(page_id)
        if not page:
            raise ValueError("Страница не найдена")
        if page.key == "main":
            raise ValueError("Системную страницу main удалить нельзя")
        dependencies = await self.repo.incoming_page_buttons(page.key)
        if dependencies:
            raise ValueError("На страницу существуют ссылки")
        old = {"key": page.key, "title": page.draft_revision.title if page.draft_revision else page.key}
        entity_id = page.id
        await self.session.delete(page)
        await self.session.flush()
        remaining = await self.repo.all()
        for position, item in enumerate(remaining):
            item.position = position
        self.session.add(AuditLog(admin_id=admin_id, action="PAGE_DELETED", entity_type="BotPage", entity_id=str(entity_id), old_data=old))

    async def update_draft(self, page_id: int, admin_id: int, *, title: str | None = None, text: str | None = None) -> None:
        page = await self.repo.by_id(page_id)
        if not page or not page.draft_revision:
            raise ValueError("Page not found")
        old = {"title": page.draft_revision.title, "text": page.draft_revision.text}
        revision = BotPageRevision(
            page_id=page.id,
            title=title if title is not None else page.draft_revision.title,
            text=text if text is not None else page.draft_revision.text,
            media_type=page.draft_revision.media_type,
            media_file_id=page.draft_revision.media_file_id,
            created_by=admin_id,
        )
        self.session.add(revision)
        await self.session.flush()
        for translation in page.draft_revision.translations:
            self.session.add(BotPageRevisionTranslation(revision_id=revision.id, language_code=translation.language_code, title=title if translation.language_code == LanguageCode.RU and title is not None else translation.title, text=text if translation.language_code == LanguageCode.RU and text is not None else translation.text))
        for attachment in page.draft_revision.attachments:
            self.session.add(PageAttachment(revision_id=revision.id, type=attachment.type, telegram_file_id=attachment.telegram_file_id, file_name=attachment.file_name, caption_ru=attachment.caption_ru, caption_kk=attachment.caption_kk, position=attachment.position, is_active=attachment.is_active, telegram_source_post_id=attachment.telegram_source_post_id, telegram_source_chat_id=attachment.telegram_source_chat_id, telegram_source_message_id=attachment.telegram_source_message_id, telegram_source_file_unique_id=attachment.telegram_source_file_unique_id))
        page.draft_revision_id = revision.id
        page.updated_by = admin_id
        self.session.add(AuditLog(admin_id=admin_id, action="PAGE_UPDATED", entity_type="BotPage", entity_id=str(page.id), old_data=old, new_data={"title": revision.title, "text": revision.text}))

    async def update_translation(self, page_id: int, admin_id: int, language: LanguageCode, *, title: str | None = None, text: str | None = None) -> None:
        page = await self.ensure_editable_draft(page_id, admin_id)

        if not page or not page.draft_revision_id:
            raise ValueError("Страница не найдена")

        revision = await self.session.get(
            BotPageRevision,
            page.draft_revision_id,
        )

        if not revision:
            raise ValueError("Ревизия страницы не найдена")

        translation = await self.session.scalar(
            select(BotPageRevisionTranslation).where(
                BotPageRevisionTranslation.revision_id == revision.id,
                BotPageRevisionTranslation.language_code == language,
            )
        )

        old = (
            None
            if translation is None
            else {
                "title": translation.title,
                "text": translation.text,
            }
        )

        if translation is None:
            translation = BotPageRevisionTranslation(
                revision_id=revision.id,
                language_code=language,
                title=title if title is not None else revision.title,
                text=text if text is not None else "",
            )

            self.session.add(translation)

        else:
            if title is not None:
                translation.title = title

            if text is not None:
                translation.text = text

        if language == LanguageCode.RU:
            if title is not None:
                revision.title = title

            if text is not None:
                revision.text = text

        page.updated_by = admin_id

        self.session.add(
            AuditLog(
                admin_id=admin_id,
                action="PAGE_TRANSLATION_UPDATED",
                entity_type="BotPage",
                entity_id=str(page.id),
                old_data=old,
                new_data={
                    "language": language.value,
                    "title": translation.title,
                    "text": translation.text,
                },
            )
        )

        await self.session.flush()

    async def publish(self, page_id: int, admin_id: int) -> None:
        page = await self.repo.by_id(page_id, lock=True)
        if not page or not page.draft_revision_id:
            raise ValueError("Page not found")
        old_id = page.published_revision_id
        page.published_revision_id = page.draft_revision_id
        page.status = PageStatus.PUBLISHED
        page.updated_by = admin_id
        self.session.add(AuditLog(admin_id=admin_id, action="PAGE_PUBLISHED", entity_type="BotPage", entity_id=str(page.id), old_data={"revision_id": old_id}, new_data={"revision_id": page.draft_revision_id}))
        await self._activate_auto_draft_link(page, admin_id)
        await self._queue_knowledge_sync(page, admin_id)

    async def _queue_knowledge_sync(self, page: BotPage, admin_id: int) -> None:
        if not page.published_revision_id:
            return
        job = await self.session.scalar(
            select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.page_id == page.id).with_for_update()
        )
        if job is None:
            job = AIKnowledgeSyncJob(page_id=page.id, revision_id=page.published_revision_id)
            self.session.add(job)
        else:
            job.revision_id = page.published_revision_id
            job.status = "PENDING"
            job.attempts = 0
            job.last_error = None
        self.session.add(AuditLog(
            admin_id=admin_id,
            action="AI_KNOWLEDGE_SYNC_QUEUED",
            entity_type="BotPage",
            entity_id=str(page.id),
            new_data={"revision_id": page.published_revision_id},
        ))

    async def _activate_auto_draft_link(self, page: BotPage, admin_id: int) -> None:
        link = await self._resolve_auto_draft_link(page)
        if not link or not link.parent_page_id or page.status != PageStatus.PUBLISHED:
            return
        parent = await self.session.get(BotPage, link.parent_page_id)
        if not parent or not parent.is_active or parent.status != PageStatus.PUBLISHED:
            raise ValueError("Родительская страница автчерновика больше недоступна")
        revision = await self.session.scalar(
            select(BotPageRevision)
            .where(BotPageRevision.id == page.published_revision_id)
            .options(selectinload(BotPageRevision.translations))
            .execution_options(populate_existing=True)
        )
        if not revision:
            raise ValueError("Опубликованная ревизия дочерней страницы не найдена")
        translations = {item.language_code: item for item in revision.translations}
        ru = translations.get(LanguageCode.RU)
        kk = translations.get(LanguageCode.KK)
        title_ru = ru.title if ru else revision.title
        title_kk = kk.title if kk else None

        button = None
        if link.button_id:
            button = await self.session.scalar(
                select(BotButton).where(BotButton.id == link.button_id).options(selectinload(BotButton.translations))
            )
            if button and not (
                button.page_id == parent.id
                and button.action_type == ButtonAction.PAGE
                and button.target == page.key
            ):
                button = None
                link.button_id = None
        if button is None:
            button = await self.session.scalar(select(BotButton).where(
                BotButton.page_id == parent.id,
                BotButton.action_type == ButtonAction.PAGE,
                BotButton.target == page.key,
            ).options(selectinload(BotButton.translations)).limit(1))
        from app.services.cms import ButtonService
        button_service = ButtonService(self.session)
        if button:
            link.button_id = button.id
            if not button.is_active:
                await button_service.update(admin_id, button, is_active=True)
            existing = {item.language_code: item for item in button.translations}
            if not existing.get(LanguageCode.RU) or existing[LanguageCode.RU].text != title_ru:
                await button_service.update_translation(admin_id, button, LanguageCode.RU, title_ru)
            if title_kk and (not existing.get(LanguageCode.KK) or existing[LanguageCode.KK].text != title_kk):
                await button_service.update_translation(admin_id, button, LanguageCode.KK, title_kk)
            return
        button = await button_service.create(
            admin_id, parent.id, title_ru, ButtonAction.PAGE, page.key, title_kk,
        )
        link.button_id = button.id
        self.session.add(AuditLog(
            admin_id=admin_id, action="TELEGRAM_AUTO_DRAFT_LINK_ACTIVATED",
            entity_type="BotButton", entity_id=str(button.id),
            new_data={"parent_page_id": parent.id, "child_page_id": page.id},
        ))

    async def _resolve_auto_draft_link(self, page: BotPage) -> TelegramAutoDraftLink | None:
        link = await self.session.scalar(
            select(TelegramAutoDraftLink)
            .where(TelegramAutoDraftLink.child_page_id == page.id)
            .with_for_update()
        )
        if link:
            return link
        audit = await self.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "TELEGRAM_AUTO_DRAFT_CREATED",
                AuditLog.entity_type == "BotPage",
                AuditLog.entity_id == str(page.id),
            ).order_by(AuditLog.id.desc()).limit(1)
        )
        data = audit.new_data if audit and isinstance(audit.new_data, dict) else {}
        parent_page_id = data.get("parent_page_id")
        source_post_id = data.get("source_post_id")
        if not isinstance(parent_page_id, int) or not isinstance(source_post_id, int):
            return None
        source = await self.session.get(TelegramContentPost, source_post_id)
        if not source or source.created_page_id != page.id:
            return None
        mapping = await self.session.scalar(select(TelegramTopicPageMapping).where(
            TelegramTopicPageMapping.chat_id == source.chat_id,
            TelegramTopicPageMapping.message_thread_id == source.message_thread_id,
            TelegramTopicPageMapping.parent_page_id == parent_page_id,
        ).limit(1))
        link = TelegramAutoDraftLink(
            source_post_id=source_post_id, child_page_id=page.id,
            parent_page_id=parent_page_id, mapping_id=mapping.id if mapping else None,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def repair_published_auto_draft_buttons(self, admin_id: int, limit: int = 100) -> int:
        pages = list((await self.session.scalars(
            select(BotPage)
            .join(AuditLog, (AuditLog.entity_type == "BotPage") & (AuditLog.entity_id == BotPage.id.cast(String)))
            .where(AuditLog.action == "TELEGRAM_AUTO_DRAFT_CREATED", BotPage.status == PageStatus.PUBLISHED)
            .options(selectinload(BotPage.published_revision), selectinload(BotPage.draft_revision))
            .order_by(BotPage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).unique().all())
        repaired = 0
        for page in pages:
            before = await self.session.scalar(select(TelegramAutoDraftLink.button_id).where(TelegramAutoDraftLink.child_page_id == page.id))
            await self._activate_auto_draft_link(page, admin_id)
            after = await self.session.scalar(select(TelegramAutoDraftLink.button_id).where(TelegramAutoDraftLink.child_page_id == page.id))
            if not before and after:
                repaired += 1
        return repaired
