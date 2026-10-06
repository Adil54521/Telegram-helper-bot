import os
import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import FAQItem, PageAttachment, TelegramContentMedia, TelegramContentPost
from app.database.models.enums import MediaType, PageStatus, TelegramContentStatus
from app.services.channel_import import TelegramContentImportService


@unittest.skipUnless(os.getenv("CHANNEL_TEST_DATABASE_URL"), "integration database is not configured")
class ChannelImportDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(os.environ["CHANNEL_TEST_DATABASE_URL"])
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_page_is_draft_media_is_copied_and_double_confirm_is_blocked(self) -> None:
        async with self.factory() as session:
            post = TelegramContentPost(chat_id=-100777, message_id=9001, chat_type="supergroup", status=TelegramContentStatus.NEW, text="Page body")
            session.add(post); await session.flush()
            session.add(TelegramContentMedia(content_post_id=post.id, source_message_id=9001, type=MediaType.PHOTO, telegram_file_id="opaque-photo", position=0))
            await session.flush()
            page = await TelegramContentImportService(session).create_page(post.id, post.chat_id, 1, title_ru="Channel Page", text_ru="Page body")
            await session.flush()
            self.assertEqual(page.status, PageStatus.DRAFT)
            self.assertEqual(await session.scalar(select(func.count(PageAttachment.id)).where(PageAttachment.revision_id == page.draft_revision_id)), 1)
            with self.assertRaisesRegex(ValueError, "уже обработана"):
                await TelegramContentImportService(session).create_page(post.id, post.chat_id, 2, title_ru="Duplicate", text_ru="No")
            await session.rollback()

    async def test_faq_does_not_copy_media(self) -> None:
        async with self.factory() as session:
            before = await session.scalar(select(func.count(PageAttachment.id))) or 0
            post = TelegramContentPost(chat_id=-100777, message_id=9002, chat_type="group", status=TelegramContentStatus.NEW, text="FAQ")
            session.add(post); await session.flush()
            session.add(TelegramContentMedia(content_post_id=post.id, source_message_id=9002, type=MediaType.DOCUMENT, telegram_file_id="opaque-doc", file_name="info.pdf", position=0))
            await session.flush()
            faq = await TelegramContentImportService(session).create_faq(post.id, post.chat_id, 1, question_ru="Как?", answer_ru="Так.")
            await session.flush()
            self.assertIsInstance(await session.get(FAQItem, faq.id), FAQItem)
            self.assertEqual(await session.scalar(select(func.count(PageAttachment.id))) or 0, before)
            await session.rollback()
