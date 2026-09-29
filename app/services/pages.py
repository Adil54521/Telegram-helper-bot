import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, BotPage, BotPageRevision, BotPageRevisionTranslation, PageAttachment
from app.database.models.enums import LanguageCode, PageStatus
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
            self.session.add(PageAttachment(revision_id=revision.id, type=attachment.type, telegram_file_id=attachment.telegram_file_id, file_name=attachment.file_name, caption_ru=attachment.caption_ru, caption_kk=attachment.caption_kk, position=attachment.position, is_active=attachment.is_active))
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
        page = await self.repo.by_id(page_id)
        if not page or not page.draft_revision_id:
            raise ValueError("Page not found")
        old_id = page.published_revision_id
        page.published_revision_id = page.draft_revision_id
        page.status = PageStatus.PUBLISHED
        page.updated_by = admin_id
        self.session.add(AuditLog(admin_id=admin_id, action="PAGE_PUBLISHED", entity_type="BotPage", entity_id=str(page.id), old_data={"revision_id": old_id}, new_data={"revision_id": page.draft_revision_id}))
