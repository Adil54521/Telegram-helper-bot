import unittest

from sqlalchemy import select, JSON
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.database.base import Base
from app.database.models import (
    BotPage,
    BotPageRevision,
    PageAttachment,
)
from app.database.models.entities import AuditLog, BotPageRevisionTranslation
from app.database.models.enums import MediaType
from app.services.attachments import AttachmentService
from app.services.pages import PageService

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return compiler.visit_JSON(JSON(), **kw)

class PageCopyOnWriteTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    BotPage.__table__,
                    BotPageRevision.__table__,
                    BotPageRevisionTranslation.__table__,
                    PageAttachment.__table__,
                    AuditLog.__table__,
                ],
            )

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_database_created(self):
        async with self.session_factory() as session:
            self.assertIsNotNone(session)

    async def test_published_attachments_are_copied_to_draft(self):
        async with self.session_factory() as session:
            page = BotPage(
                key="documents",
                is_active=True,
            )
            session.add(page)
            await session.flush()

            revision = BotPageRevision(
                page_id=page.id,
                title="Документы",
                text="Информация об университете",
                created_by=123456,
            )
            session.add(revision)
            await session.flush()

            attachment = PageAttachment(
                revision_id=revision.id,
                type=MediaType.DOCUMENT,
                telegram_file_id="old_file",
                file_name="old.pdf",
                position=0,
                is_active=True,
            )
            session.add(attachment)
            await session.flush()

            page.published_revision_id = revision.id
            page.draft_revision_id = revision.id

            await session.commit()

            original_revision_id = revision.id
            original_attachment_id = attachment.id

        async with self.session_factory() as session:
            page = await PageService(
                session
            ).ensure_editable_draft(
                page.id,
                123456,
            )

            self.assertEqual(
                page.published_revision_id,
                original_revision_id,
            )

            self.assertNotEqual(
                page.draft_revision_id,
                original_revision_id,
            )

            copied = (
                await session.scalars(
                    select(PageAttachment).where(
                        PageAttachment.revision_id
                        == page.draft_revision_id
                    )
                )
            ).all()

            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0].file_name, "old.pdf")
            self.assertNotEqual(
                copied[0].id,
                original_attachment_id,
            )

            await session.commit()

    async def test_repeated_edit_does_not_create_new_revision(self):
        async with self.session_factory() as session:
            page = BotPage(
                key="repeat-test",
                is_active=True,
            )
            session.add(page)
            await session.flush()

            revision = BotPageRevision(
                page_id=page.id,
                title="Тест",
                text="Текст",
            )
            session.add(revision)
            await session.flush()

            page.published_revision_id = revision.id
            page.draft_revision_id = revision.id

            await session.commit()
            page_id = page.id

        async with self.session_factory() as session:
            service = PageService(session)

            first = await service.ensure_editable_draft(
                page_id,
                123456,
            )

            first_draft_id = first.draft_revision_id

            second = await service.ensure_editable_draft(
                page_id,
                123456,
            )

            self.assertEqual(
                second.draft_revision_id,
                first_draft_id,
            )

    async def test_new_attachment_position_after_copy(self):
        async with self.session_factory() as session:
            page = BotPage(
                key="attachment-position-test",
                is_active=True,
            )
            session.add(page)
            await session.flush()

            revision = BotPageRevision(
                page_id=page.id,
                title="Документы",
                text="Тест",
            )
            session.add(revision)
            await session.flush()

            session.add_all([
                PageAttachment(
                    revision_id=revision.id,
                    type=MediaType.DOCUMENT,
                    telegram_file_id=f"old_{i}",
                    file_name=f"old_{i}.pdf",
                    position=i,
                    is_active=True,
                )
                for i in range(2)
            ])

            page.published_revision_id = revision.id
            page.draft_revision_id = revision.id

            page_id = page.id
            published_id = revision.id

            await session.commit()

        async with self.session_factory() as session:
            attachment = await AttachmentService(session).create(
                page_id=page_id,
                actor=123456,
                type_=MediaType.DOCUMENT,
                file_id="new_file",
                file_name="new.pdf",
            )

            self.assertEqual(attachment.position, 2)

            page = await session.get(BotPage, page_id)

            self.assertEqual(
                page.published_revision_id,
                published_id,
            )

            self.assertNotEqual(
                page.draft_revision_id,
                published_id,
            )

            draft_attachments = (
                await session.scalars(
                    select(PageAttachment).where(
                        PageAttachment.revision_id
                        == page.draft_revision_id
                    ).order_by(PageAttachment.position)
                )
            ).all()

            self.assertEqual(len(draft_attachments), 3)

            self.assertEqual(
                [item.position for item in draft_attachments],
                [0, 1, 2],
            )

            self.assertEqual(
                [item.file_name for item in draft_attachments],
                ["old_0.pdf", "old_1.pdf", "new.pdf"],
            )

            published_attachments = (
                await session.scalars(
                    select(PageAttachment).where(
                        PageAttachment.revision_id == published_id
                    ).order_by(PageAttachment.position)
                )
            ).all()

            self.assertEqual(len(published_attachments), 2)

            self.assertEqual(
                [item.file_name for item in published_attachments],
                ["old_0.pdf", "old_1.pdf"],
            )