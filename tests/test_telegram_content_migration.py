import os
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.index import KnowledgeIndexService
from app.database.models import AIKnowledgeChunk, AIKnowledgeSyncJob, AuditLog, BotPageRevision, FAQItem, FAQRevision, PageAttachment, TelegramAttachmentReplacement, TelegramAutoDraftLink, TelegramContentMedia, TelegramContentPost, TelegramEditPending, TelegramFAQEditPending, TelegramFAQSyncLink, TelegramForumTopic, TelegramImportSettings, TelegramTopicPageMapping
from app.database.models.enums import ButtonAction, LanguageCode, MediaType, PageStatus, TelegramContentStatus, TelegramContentTarget, TelegramEditSyncMode, TelegramFileSyncMode, TelegramProcessingMode
from app.database.repositories.pages import PageRepository
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.cms import ButtonService, FAQService
from app.services.channel_import import TelegramContentImportService
from app.services.pages import PageService
from app.services.knowledge_sync import process_knowledge_job
from app.services.telegram_auto_drafts import TelegramAutoDraftService
from app.services.telegram_edit_sync import TelegramEditSyncService
from app.services.telegram_faq_edit_sync import TelegramFAQEditSyncService
from app.services.telegram_attachment_sync import TelegramAttachmentSyncService


BILINGUAL = """5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ

Қазақ және орыс тілдеріндегі практика есебінің үлгілерін төменнен жүктеп алуға болады.

5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ

Образцы отчётов по практике на казахском и русском языках можно скачать ниже."""

BILINGUAL_EDITED = BILINGUAL.replace("төменнен жүктеп алуға болады", "жаңартылған үлгілерді жүктеп алуға болады").replace(
    "можно скачать ниже", "можно скачать обновлённые файлы ниже"
)

FAQ_BILINGUAL = """❓ UTS жүйесіне қалай кіруге болады?

UTS жүйесіне университет берген логин және құпиясөз арқылы кіруге болады.

━━━━━━━━━━━━━━

❓ Как войти в UTS?

Для входа используйте логин и пароль, выданные университетом."""

FAQ_BILINGUAL_EDITED = FAQ_BILINGUAL.replace(
    "логин және құпиясөз", "логин, құпиясөз және растау кодын",
).replace(
    "логин и пароль", "логин, пароль и код подтверждения",
)


DATABASE_URL = os.getenv("TELEGRAM_MIGRATION_TEST_DATABASE_URL", "")


@unittest.skipUnless(DATABASE_URL, "TELEGRAM_MIGRATION_TEST_DATABASE_URL is not configured")
class TelegramContentMigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        url = make_url(DATABASE_URL)
        cls.alembic_env = os.environ.copy()
        cls.alembic_env.update(
            POSTGRES_DB=url.database or "",
            POSTGRES_USER=url.username or "",
            POSTGRES_PASSWORD=url.password or "",
            POSTGRES_HOST=url.host or "",
            POSTGRES_PORT=str(url.port or 5432),
        )

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(DATABASE_URL)
        async with self.engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))

    async def asyncTearDown(self) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
        await self.engine.dispose()

    def alembic(self, *arguments: str) -> None:
        subprocess.run(
            ["alembic", *arguments],
            check=True,
            env=self.alembic_env,
            capture_output=True,
            text=True,
        )

    async def test_legacy_data_survives_upgrade_and_downgrade(self) -> None:
        self.alembic("upgrade", "20261005_05")
        async with self.engine.begin() as connection:
            await connection.execute(sa.text("CREATE TYPE channel_post_status AS ENUM ('NEW', 'DRAFT_CREATED', 'IGNORED')"))
            await connection.execute(sa.text("CREATE TYPE channel_media_type AS ENUM ('PHOTO', 'DOCUMENT')"))
            await connection.execute(sa.text(
                """CREATE TABLE channel_posts (
                    id SERIAL PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    media_group_id VARCHAR(128),
                    status channel_post_status NOT NULL DEFAULT 'NEW',
                    text TEXT,
                    caption TEXT,
                    telegram_created_at TIMESTAMPTZ,
                    processed_at TIMESTAMPTZ,
                    processed_by BIGINT,
                    created_page_id INTEGER REFERENCES bot_pages(id) ON DELETE SET NULL,
                    created_faq_id INTEGER REFERENCES faq_items(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (channel_id, message_id)
                )"""
            ))
            await connection.execute(sa.text("CREATE INDEX legacy_status_date_idx ON channel_posts (status, created_at)"))
            await connection.execute(sa.text(
                """CREATE TABLE channel_post_media (
                    id SERIAL PRIMARY KEY,
                    channel_post_id INTEGER NOT NULL REFERENCES channel_posts(id) ON DELETE CASCADE,
                    source_message_id INTEGER NOT NULL,
                    type channel_media_type NOT NULL,
                    telegram_file_id VARCHAR(512) NOT NULL,
                    file_unique_id VARCHAR(512),
                    file_name VARCHAR(255),
                    file_size BIGINT,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (channel_post_id, source_message_id)
                )"""
            ))
            post_id = await connection.scalar(
                sa.text(
                    """INSERT INTO channel_posts
                    (channel_id, message_id, status, text)
                    VALUES (-100123, 77, 'NEW', 'Preserve me')
                    RETURNING id"""
                )
            )
            await connection.execute(
                sa.text(
                    """INSERT INTO channel_post_media
                    (channel_post_id, source_message_id, type, telegram_file_id, position)
                    VALUES (:post_id, 77, 'PHOTO', 'telegram-file', 0)"""
                ),
                {"post_id": post_id},
            )
        self.alembic("stamp", "20261006_06")

        self.alembic("upgrade", "20261007_10")
        async with self.engine.connect() as connection:
            post = (await connection.execute(sa.text(
                "SELECT id, chat_id, message_thread_id, chat_type, chat_title, text FROM telegram_content_posts"
            ))).mappings().one()
            media = (await connection.execute(sa.text(
                "SELECT content_post_id, source_message_id, telegram_file_id FROM telegram_content_media"
            ))).mappings().one()
            self.assertEqual(post["chat_id"], -100123)
            self.assertIsNone(post["message_thread_id"])
            self.assertEqual(post["chat_type"], "channel")
            self.assertIsNone(post["chat_title"])
            self.assertEqual(post["text"], "Preserve me")
            self.assertEqual(media["content_post_id"], post["id"])
            self.assertEqual(media["telegram_file_id"], "telegram-file")
            schema = await connection.run_sync(self._inspect_universal_schema)
            await connection.run_sync(self._assert_model_column_parity)
            table_names = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertIn("telegram_import_settings", table_names)
            self.assertIn("telegram_topic_page_mappings", table_names)
            self.assertIn("telegram_auto_draft_links", table_names)
            self.assertIn("telegram_forum_topics", table_names)

        self.assertIn(("chat_id", "message_id"), schema["post_uniques"])
        self.assertIn(("content_post_id", "source_message_id"), schema["media_uniques"])
        self.assertIn(("status", "created_at"), schema["post_indexes"])
        self.assertIn(("chat_id", "media_group_id", "message_thread_id"), schema["post_indexes"])
        self.assertEqual(schema["media_fk"], ("telegram_content_posts", "CASCADE"))
        await self._orm_smoke_test(message_id=78)

        self.alembic("downgrade", "20261006_06")
        async with self.engine.connect() as connection:
            post = (await connection.execute(sa.text(
                "SELECT id, channel_id, channel_title, text FROM channel_posts WHERE message_id = 77"
            ))).mappings().one()
            media = (await connection.execute(sa.text(
                "SELECT channel_post_id, source_message_id, telegram_file_id "
                "FROM channel_post_media WHERE source_message_id = 77"
            ))).mappings().one()
            self.assertEqual(post["channel_id"], -100123)
            self.assertEqual(post["text"], "Preserve me")
            self.assertEqual(media["channel_post_id"], post["id"])
            self.assertEqual(media["telegram_file_id"], "telegram-file")

    async def test_clean_base_to_head_has_model_parity(self) -> None:
        self.alembic("upgrade", "head")
        async with self.engine.connect() as connection:
            await connection.run_sync(self._assert_model_column_parity)
        await self._orm_smoke_test(message_id=88)

    async def test_forum_topic_event_is_immediately_discoverable_without_content_post(self) -> None:
        self.alembic("upgrade", "head")
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with session_factory() as session:
            repository = TelegramAutoDraftRepository(session)
            await repository.upsert_topic_name(-100123, 2, "Практика")
            await session.commit()

            self.assertEqual(await repository.seen_threads(-100123), [2])
            self.assertEqual(await repository.topic_names(-100123), {2: "Практика"})
            content_count = await session.scalar(sa.select(sa.func.count(TelegramContentPost.id)))
            self.assertEqual(content_count, 0)

            parent_id = await session.scalar(sa.text(
                "INSERT INTO bot_pages (key, status, position, is_active) "
                "VALUES ('topic-parent', 'PUBLISHED', 0, true) RETURNING id"
            ))
            session.add(TelegramTopicPageMapping(
                chat_id=-100123,
                message_thread_id=2,
                parent_page_id=parent_id,
            ))
            await session.commit()

            await repository.upsert_topic_name(-100123, 2, "Практика и трудоустройство")
            await repository.upsert_topic_name(-100123, 2, "Практика и трудоустройство")
            await session.commit()

            topics = list((await session.scalars(sa.select(TelegramForumTopic))).all())
            mapping = await repository.mapping(-100123, 2)
            self.assertEqual(len(topics), 1)
            self.assertEqual(topics[0].topic_name, "Практика и трудоустройство")
            self.assertIsNotNone(mapping)
            self.assertEqual(mapping.parent_page_id, parent_id)

            await session.execute(sa.text(
                "INSERT INTO telegram_content_posts "
                "(chat_id, message_id, message_thread_id, chat_type, status, text) "
                "VALUES (-100123, 77, 30, 'supergroup', 'NEW', 'Observed content')"
            ))
            await session.commit()
            self.assertEqual(await repository.seen_threads(-100123), [2, 30])

    async def test_faq_revision_auto_review_conflict_and_idempotency(self) -> None:
        self.alembic("upgrade", "head")
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with factory() as session:
            mapping = TelegramTopicPageMapping(
                chat_id=-100500, message_thread_id=9, parent_page_id=None,
                content_target=TelegramContentTarget.FAQ,
                processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
                edit_sync_mode=TelegramEditSyncMode.AUTO,
            )
            post = TelegramContentPost(
                chat_id=-100500, message_id=100, message_thread_id=9,
                chat_type="supergroup", status=TelegramContentStatus.NEW,
                text=FAQ_BILINGUAL, telegram_created_at=now,
            )
            session.add_all([mapping, post]); await session.flush()
            faq = await TelegramContentImportService(session).create_faq(
                post.id, post.chat_id, 42,
                question_ru="Как войти в UTS?", answer_ru="Для входа используйте логин и пароль, выданные университетом.",
                question_kk="UTS жүйесіне қалай кіруге болады?", answer_kk="UTS жүйесіне университет берген логин және құпиясөз арқылы кіруге болады.",
                is_active=True,
            )
            faq_id, post_id = faq.id, post.id
            await session.commit()

        async with factory() as session:
            result = await TelegramFAQEditSyncService(session, 42).process(post_id, FAQ_BILINGUAL_EDITED, now + timedelta(seconds=10))
            self.assertEqual(result.status, "APPLIED")
            await session.commit()
            faq = await session.get(FAQItem, faq_id)
            revisions = list((await session.scalars(sa.select(FAQRevision).where(FAQRevision.faq_id == faq_id).order_by(FAQRevision.version))).all())
            self.assertEqual(len(revisions), 2)
            self.assertEqual(revisions[0].answer_ru, "Для входа используйте логин и пароль, выданные университетом.")
            self.assertIn("код подтверждения", revisions[1].answer_ru)
            self.assertEqual(await session.scalar(sa.select(sa.func.count()).select_from(FAQItem)), 1)
            link = await session.scalar(sa.select(TelegramFAQSyncLink).where(TelegramFAQSyncLink.faq_id == faq_id))
            job = await session.scalar(sa.select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.faq_id == faq_id))
            self.assertEqual(link.last_synced_revision_id, faq.current_revision_id)
            self.assertEqual(job.faq_revision_id, faq.current_revision_id)

            duplicate = await TelegramFAQEditSyncService(session, 42).process(post_id, FAQ_BILINGUAL_EDITED, now + timedelta(seconds=10))
            stale = await TelegramFAQEditSyncService(session, 42).process(post_id, FAQ_BILINGUAL, now + timedelta(seconds=5))
            self.assertEqual(duplicate.status, "UNCHANGED")
            self.assertEqual(stale.status, "STALE")
            self.assertEqual(await session.scalar(sa.select(sa.func.count(FAQRevision.id)).where(FAQRevision.faq_id == faq_id)), 2)

            await FAQService(session).update_translation(77, faq, LanguageCode.RU, answer="Ручное изменение администратора")
            await session.flush()
            conflict_text = FAQ_BILINGUAL_EDITED.replace("код подтверждения", "одноразовый код")
            conflict = await TelegramFAQEditSyncService(session, 42).process(post_id, conflict_text, now + timedelta(seconds=20))
            self.assertEqual(conflict.status, "CONFLICT")
            pending = await session.get(TelegramFAQEditPending, conflict.pending_id)
            self.assertEqual(pending.status, "CONFLICT")
            self.assertEqual(faq.answer, "Ручное изменение администратора")

            link.last_synced_revision_id = faq.current_revision_id
            mapping = await session.scalar(sa.select(TelegramTopicPageMapping).where(TelegramTopicPageMapping.message_thread_id == 9))
            mapping.edit_sync_mode = TelegramEditSyncMode.REVIEW
            review_text = FAQ_BILINGUAL_EDITED.replace("код подтверждения", "SMS-код")
            review = await TelegramFAQEditSyncService(session, 42).process(post_id, review_text, now + timedelta(seconds=30))
            self.assertEqual(review.status, "REVIEW")
            before_apply = faq.current_revision_id
            self.assertNotEqual(pending.source_fingerprint, link.source_fingerprint)
            applied_id = await TelegramFAQEditSyncService(session, 77).apply_pending(review.pending_id)
            self.assertEqual(applied_id, faq_id)
            self.assertNotEqual(faq.current_revision_id, before_apply)
            self.assertEqual(await TelegramFAQEditSyncService(session, 77).apply_pending(review.pending_id), faq_id)

            reject_text = FAQ_BILINGUAL_EDITED.replace("код подтверждения", "резервный код")
            rejected = await TelegramFAQEditSyncService(session, 42).process(post_id, reject_text, now + timedelta(seconds=35))
            revision_before_reject = faq.current_revision_id
            await TelegramFAQEditSyncService(session, 77).reject_pending(rejected.pending_id)
            self.assertEqual(faq.current_revision_id, revision_before_reject)
            self.assertEqual((await session.get(TelegramFAQEditPending, rejected.pending_id)).status, "REJECTED")

            mapping.edit_sync_mode = TelegramEditSyncMode.AUTO
            incomplete = await TelegramFAQEditSyncService(session, 42).process(
                post_id, "❓ Только вопрос без двуязычного ответа?", now + timedelta(seconds=38),
            )
            self.assertEqual(incomplete.status, "INCOMPLETE")
            self.assertEqual(faq.current_revision_id, revision_before_reject)

            mapping.edit_sync_mode = TelegramEditSyncMode.OFF
            revision_before_off = faq.current_revision_id
            off = await TelegramFAQEditSyncService(session, 42).process(post_id, FAQ_BILINGUAL, now + timedelta(seconds=40))
            self.assertEqual(off.status, "OFF")
            self.assertEqual(faq.current_revision_id, revision_before_off)
            await session.commit()

    async def test_previous_head_forum_topic_upgrade_and_downgrade(self) -> None:
        self.alembic("upgrade", "20261007_09")
        async with self.engine.connect() as connection:
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertNotIn("telegram_forum_topics", tables)
        self.alembic("upgrade", "20261007_10")
        async with self.engine.begin() as connection:
            await connection.execute(sa.text(
                "INSERT INTO telegram_forum_topics (chat_id, message_thread_id, topic_name) VALUES (-100123, 2, 'Практика')"
            ))
            row = (await connection.execute(sa.text(
                "SELECT chat_id, message_thread_id, topic_name FROM telegram_forum_topics"
            ))).mappings().one()
            self.assertEqual((row["chat_id"], row["message_thread_id"], row["topic_name"]), (-100123, 2, "Практика"))
        self.alembic("downgrade", "20261007_09")
        async with self.engine.connect() as connection:
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertNotIn("telegram_forum_topics", tables)

    async def test_auto_publish_mode_migration_preserves_existing_mapping_behavior(self) -> None:
        self.alembic("upgrade", "20261007_10")
        async with self.engine.begin() as connection:
            parent_id = await connection.scalar(sa.text(
                "INSERT INTO bot_pages (key, status, position, is_active) VALUES ('parent', 'PUBLISHED', 0, true) RETURNING id"
            ))
            await connection.execute(sa.text(
                "INSERT INTO telegram_topic_page_mappings "
                "(chat_id, message_thread_id, parent_page_id, is_active, auto_draft_enabled, auto_attach_materials) "
                "VALUES (-1001, 2, :parent, true, true, true), (-1001, 3, :parent, true, false, true)"
            ), {"parent": parent_id})
        self.alembic("upgrade", "20261008_11")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(sa.text(
                "SELECT message_thread_id, processing_mode FROM telegram_topic_page_mappings ORDER BY message_thread_id"
            ))).all()
            self.assertEqual([(row[0], row[1]) for row in rows], [(2, "AUTO_DRAFT"), (3, "MANUAL")])
        self.alembic("downgrade", "20261007_10")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(sa.text(
                "SELECT message_thread_id, auto_draft_enabled FROM telegram_topic_page_mappings ORDER BY message_thread_id"
            ))).all()
            self.assertEqual([(row[0], row[1]) for row in rows], [(2, True), (3, False)])

    async def test_edit_sync_migration_defaults_existing_mappings_to_off_and_downgrades(self) -> None:
        self.alembic("upgrade", "20261008_11")
        async with self.engine.begin() as connection:
            parent_id = await connection.scalar(sa.text(
                "INSERT INTO bot_pages (key, status, position, is_active) VALUES ('edit-parent', 'PUBLISHED', 0, true) RETURNING id"
            ))
            await connection.execute(sa.text(
                "INSERT INTO telegram_topic_page_mappings "
                "(chat_id, message_thread_id, parent_page_id, is_active, auto_draft_enabled, processing_mode, processing_mode_enabled_at, auto_attach_materials) "
                "VALUES (-1001, 30, :parent, true, true, 'AUTO_DRAFT', now(), true)"
            ), {"parent": parent_id})
        self.alembic("upgrade", "20261008_12")
        async with self.engine.connect() as connection:
            mode = await connection.scalar(sa.text(
                "SELECT edit_sync_mode FROM telegram_topic_page_mappings WHERE message_thread_id = 30"
            ))
            self.assertEqual(mode, "OFF")
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertIn("telegram_edit_pending", tables)
            self.assertIn("ai_knowledge_sync_jobs", tables)
        self.alembic("downgrade", "20261008_11")
        async with self.engine.connect() as connection:
            columns = {item["name"] for item in await connection.run_sync(lambda conn: sa.inspect(conn).get_columns("telegram_topic_page_mappings"))}
            self.assertNotIn("edit_sync_mode", columns)

    async def test_attachment_sync_migration_defaults_existing_mappings_to_off(self) -> None:
        self.alembic("upgrade", "20261008_12")
        async with self.engine.begin() as connection:
            parent_id = await connection.scalar(sa.text(
                "INSERT INTO bot_pages (key, status, position, is_active) VALUES ('file-parent', 'PUBLISHED', 0, true) RETURNING id"
            ))
            await connection.execute(sa.text(
                "INSERT INTO telegram_topic_page_mappings "
                "(chat_id, message_thread_id, parent_page_id, is_active, auto_draft_enabled, processing_mode, processing_mode_enabled_at, edit_sync_mode, auto_attach_materials) "
                "VALUES (-1001, 32, :parent, true, true, 'AUTO_PUBLISH', now(), 'OFF', true)"
            ), {"parent": parent_id})
        self.alembic("upgrade", "20261008_13")
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(sa.text(
                "SELECT file_sync_mode FROM telegram_topic_page_mappings WHERE message_thread_id=32"
            )), "OFF")
            columns = {item["name"] for item in await connection.run_sync(lambda conn: sa.inspect(conn).get_columns("page_attachments"))}
            self.assertTrue({"telegram_source_post_id", "telegram_source_chat_id", "telegram_source_message_id", "telegram_source_file_unique_id"}.issubset(columns))
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertIn("telegram_attachment_replacements", tables)
        self.alembic("downgrade", "20261008_12")

    async def test_faq_target_migration_defaults_existing_mappings_to_page_and_downgrades(self) -> None:
        self.alembic("upgrade", "20261008_13")
        async with self.engine.begin() as connection:
            parent_id = await connection.scalar(sa.text(
                "INSERT INTO bot_pages (key, status, position, is_active) VALUES ('faq-migration-parent', 'PUBLISHED', 0, true) RETURNING id"
            ))
            await connection.execute(sa.text(
                "INSERT INTO telegram_topic_page_mappings "
                "(chat_id, message_thread_id, parent_page_id, is_active, auto_draft_enabled, processing_mode, processing_mode_enabled_at, edit_sync_mode, file_sync_mode, auto_attach_materials) "
                "VALUES (-1001, 77, :parent, true, true, 'AUTO_DRAFT', now(), 'OFF', 'OFF', true)"
            ), {"parent": parent_id})
        self.alembic("upgrade", "20261008_14")
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(sa.text(
                "SELECT content_target FROM telegram_topic_page_mappings WHERE message_thread_id=77"
            )), "PAGE")
        self.alembic("downgrade", "20261008_13")
        async with self.engine.connect() as connection:
            columns = {item["name"] for item in await connection.run_sync(lambda conn: sa.inspect(conn).get_columns("telegram_topic_page_mappings"))}
            self.assertNotIn("content_target", columns)

    async def test_faq_revision_migration_backfills_and_preserves_existing_faq(self) -> None:
        self.alembic("upgrade", "20261008_14")
        async with self.engine.begin() as connection:
            faq_id = await connection.scalar(sa.text(
                "INSERT INTO faq_items (question, answer, position, is_active) "
                "VALUES ('Старый вопрос?', 'Старый ответ', 0, true) RETURNING id"
            ))
            await connection.execute(sa.text(
                "INSERT INTO faq_item_translations (faq_id, language_code, question, answer) VALUES "
                "(:faq, 'RU', 'Старый вопрос?', 'Старый ответ'), "
                "(:faq, 'KK', 'Ескі сұрақ?', 'Ескі жауап')"
            ), {"faq": faq_id})
        self.alembic("upgrade", "20261009_15")
        async with self.engine.connect() as connection:
            row = (await connection.execute(sa.text(
                "SELECT f.question, f.current_revision_id, r.version, r.question_kk, r.source_type "
                "FROM faq_items f JOIN faq_revisions r ON r.id=f.current_revision_id WHERE f.id=:faq"
            ), {"faq": faq_id})).mappings().one()
            self.assertEqual(row["question"], "Старый вопрос?")
            self.assertEqual((row["version"], row["question_kk"], row["source_type"]), (1, "Ескі сұрақ?", "MANUAL"))
        self.alembic("downgrade", "20261008_14")
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(sa.text("SELECT question FROM faq_items WHERE id=:faq"), {"faq": faq_id}), "Старый вопрос?")
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            self.assertNotIn("faq_revisions", tables)

    async def test_attachment_review_auto_history_and_conflict_safety(self) -> None:
        self.alembic("upgrade", "head")
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with factory() as session:
            pages = PageService(session)
            page = await pages.create("Документы", "Описание", 42)
            source = TelegramContentPost(
                chat_id=-1001, message_id=700, message_thread_id=30,
                chat_type="supergroup", status=TelegramContentStatus.DRAFT_CREATED,
                created_page_id=page.id, caption="Документ",
            )
            session.add(source)
            await session.flush()
            old = PageAttachment(
                revision_id=page.draft_revision_id, type=MediaType.DOCUMENT,
                telegram_file_id="old-file", file_name="old.pdf", is_active=True,
                telegram_source_post_id=source.id, telegram_source_chat_id=-1001,
                telegram_source_message_id=700, telegram_source_file_unique_id="old-unique",
            )
            session.add(old)
            other = PageAttachment(
                revision_id=page.draft_revision_id, type=MediaType.DOCUMENT,
                telegram_file_id="keep-file", file_name="keep.pdf", position=1, is_active=True,
            )
            session.add(other)
            mapping = TelegramTopicPageMapping(
                chat_id=-1001, message_thread_id=30, parent_page_id=page.id,
                processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
                edit_sync_mode=TelegramEditSyncMode.REVIEW,
                file_sync_mode=TelegramFileSyncMode.REVIEW,
            )
            session.add(mapping)
            main_source = TelegramContentPost(
                chat_id=-1001, message_id=699, message_thread_id=30,
                chat_type="supergroup", status=TelegramContentStatus.DRAFT_CREATED,
                created_page_id=page.id, text=BILINGUAL,
            )
            session.add(main_source)
            await session.flush()
            await pages.publish(page.id, 42)
            await session.flush()
            session.add(TelegramAutoDraftLink(
                source_post_id=main_source.id, child_page_id=page.id, parent_page_id=page.id,
                mapping_id=mapping.id, last_synced_revision_id=page.published_revision_id,
            ))
            await session.commit()
            page_id, post_id, old_revision = page.id, source.id, page.published_revision_id

        async with factory() as session:
            text_result = await TelegramEditSyncService(session, 42).process(post_id, "Документ")
            self.assertEqual(text_result.status, "UNCHANGED")
            self.assertIsNone(await session.scalar(sa.select(TelegramEditPending).where(TelegramEditPending.source_post_id == post_id)))
            service = TelegramAttachmentSyncService(session, 42)
            review = await service.process_edit(
                post_id, (MediaType.DOCUMENT, "new-file", "new-unique", "new.pdf", 100), 700,
            )
            self.assertEqual(review.status, "REVIEW")
            self.assertIsNotNone(review.pending_id)
            await service.apply_pending(review.pending_id)
            await session.commit()

        async with factory() as session:
            page = await PageRepository(session).by_id(page_id)
            self.assertNotEqual(page.published_revision_id, old_revision)
            old_attachment = await session.scalar(sa.select(PageAttachment).where(
                PageAttachment.revision_id == old_revision,
                PageAttachment.telegram_source_post_id == post_id,
            ))
            new_attachment = await session.scalar(sa.select(PageAttachment).where(
                PageAttachment.revision_id == page.published_revision_id,
                PageAttachment.telegram_source_post_id == post_id,
            ))
            self.assertEqual(old_attachment.telegram_file_id, "old-file")
            self.assertEqual(new_attachment.telegram_file_id, "new-file")
            kept = await session.scalar(sa.select(PageAttachment).where(
                PageAttachment.revision_id == page.published_revision_id,
                PageAttachment.file_name == "keep.pdf",
            ))
            self.assertEqual(kept.telegram_file_id, "keep-file")
            applied = await session.scalar(sa.select(TelegramAttachmentReplacement).where(TelegramAttachmentReplacement.source_post_id == post_id))
            with self.assertRaisesRegex(ValueError, "уже применена"):
                await TelegramAttachmentSyncService(session, 42).apply_pending(applied.id)
            count_before = await session.scalar(sa.select(sa.func.count(BotPageRevision.id)).where(BotPageRevision.page_id == page_id))
            mapping = await session.scalar(sa.select(TelegramTopicPageMapping).where(TelegramTopicPageMapping.message_thread_id == 30))
            mapping.file_sync_mode = TelegramFileSyncMode.AUTO
            unchanged = await TelegramAttachmentSyncService(session, 42).process_edit(
                post_id, (MediaType.DOCUMENT, "new-file", "new-unique", "new.pdf", 100), 700,
            )
            self.assertEqual(unchanged.status, "UNCHANGED")
            self.assertEqual(await session.scalar(sa.select(sa.func.count(BotPageRevision.id)).where(BotPageRevision.page_id == page_id)), count_before)

            await PageService(session).update_draft(page_id, 42, text="Ручное изменение")
            conflict = await TelegramAttachmentSyncService(session, 42).process_edit(
                post_id, (MediaType.DOCUMENT, "third-file", "third-unique", "third.pdf", 100), 700,
            )
            self.assertEqual(conflict.status, "CONFLICT")
            self.assertEqual(page.published_revision_id, new_attachment.revision_id)
            pending = await session.scalar(sa.select(TelegramAttachmentReplacement).where(TelegramAttachmentReplacement.source_post_id == post_id))
            self.assertEqual(pending.status, "CONFLICT")

    async def test_telegram_faq_auto_draft_and_publish_are_idempotent(self) -> None:
        self.alembic("upgrade", "head")
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with factory() as session:
            session.add(TelegramImportSettings(
                chat_id=-1001, auto_draft_enabled=True,
                auto_draft_enabled_at=now - timedelta(minutes=1), notify_admins=False,
            ))
            session.add_all([
                TelegramTopicPageMapping(
                    chat_id=-1001, message_thread_id=40, parent_page_id=None,
                    content_target=TelegramContentTarget.FAQ,
                    processing_mode=TelegramProcessingMode.AUTO_DRAFT,
                    processing_mode_enabled_at=now - timedelta(seconds=30),
                ),
                TelegramTopicPageMapping(
                    chat_id=-1001, message_thread_id=41, parent_page_id=None,
                    content_target=TelegramContentTarget.FAQ,
                    processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
                    processing_mode_enabled_at=now - timedelta(seconds=30),
                ),
            ])
            draft_source = TelegramContentPost(
                chat_id=-1001, message_id=801, message_thread_id=40, chat_type="supergroup",
                status=TelegramContentStatus.NEW, text=FAQ_BILINGUAL, telegram_created_at=now,
            )
            published_source = TelegramContentPost(
                chat_id=-1001, message_id=802, message_thread_id=41, chat_type="supergroup",
                status=TelegramContentStatus.NEW, text=FAQ_BILINGUAL, telegram_created_at=now,
            )
            session.add_all([draft_source, published_source])
            await session.flush()
            page_count_before = await session.scalar(sa.select(sa.func.count()).select_from(sa.text("bot_pages")))
            draft_result = await TelegramAutoDraftService(session, -1001, 42).process_claimed(draft_source)
            published_result = await TelegramAutoDraftService(session, -1001, 42).process_claimed(published_source)
            self.assertFalse(draft_result.published)
            self.assertTrue(published_result.published)
            await session.commit()
            ids = (draft_result.faq_id, published_result.faq_id)

        async with factory() as session:
            draft = await session.get(FAQItem, ids[0])
            published = await session.get(FAQItem, ids[1])
            self.assertFalse(draft.is_active)
            self.assertTrue(published.is_active)
            for item in (draft, published):
                translations = {row.language_code: row for row in item.translations}
                self.assertEqual(set(translations), {LanguageCode.RU, LanguageCode.KK})
            self.assertEqual(await session.scalar(sa.select(sa.func.count()).select_from(sa.text("bot_pages"))), page_count_before)
            self.assertIsNone(await session.scalar(sa.select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.faq_id == draft.id)))
            self.assertIsNotNone(await session.scalar(sa.select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.faq_id == published.id)))
            self.assertEqual(await session.scalar(sa.select(sa.func.count()).select_from(FAQItem)), 2)

    async def test_auto_draft_publish_creates_and_recovers_parent_button(self) -> None:
        self.alembic("upgrade", "head")
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with session_factory() as session:
            pages = PageService(session)
            parent = await pages.create("Практика", "Описание", 42)
            await pages.update_translation(parent.id, 42, LanguageCode.KK, title="Практика", text="Сипаттама")
            await pages.publish(parent.id, 42)
            await session.flush()
            old_button = await ButtonService(session).create(42, parent.id, "Старая кнопка", ButtonAction.PAGE, "main", "Ескі батырма")
            await session.commit()
            published_parent_revision = parent.published_revision_id
            await pages.update_translation(parent.id, 42, LanguageCode.RU, title="Непубликованный заголовок")
            await session.flush()
            draft_parent_revision = parent.draft_revision_id
            child = await pages.create("Отчёты", "Материалы", 42)
            await pages.update_translation(child.id, 42, LanguageCode.KK, title="Есептер", text="Материалдар")
            mapping = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=2, parent_page_id=parent.id, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
            session.add(mapping)
            source = TelegramContentPost(chat_id=-1001, message_id=100, message_thread_id=2, chat_type="supergroup", status=TelegramContentStatus.DRAFT_CREATED, text="source", created_page_id=child.id)
            session.add(source)
            await session.flush()
            link = TelegramAutoDraftLink(source_post_id=source.id, child_page_id=child.id, parent_page_id=parent.id, mapping_id=mapping.id)
            session.add(link)
            session.add(AuditLog(admin_id=42, action="TELEGRAM_AUTO_DRAFT_CREATED", entity_type="BotPage", entity_id=str(child.id), new_data={"source_post_id": source.id, "parent_page_id": parent.id}))
            await session.commit()
            parent_id, child_id, child_key, old_button_id = parent.id, child.id, child.key, old_button.id

        async with session_factory() as session:
            before = await PageRepository(session).buttons(parent_id)
            self.assertEqual([item.id for item in before], [old_button_id])
            await PageService(session).publish(child_id, 42)
            await session.commit()

        async with session_factory() as session:
            after = await PageRepository(session).buttons(parent_id)
            self.assertEqual(len(after), 2)
            generated = next(item for item in after if item.id != old_button_id)
            self.assertEqual((generated.action_type, generated.target), (ButtonAction.PAGE, child_key))
            labels = {item.language_code: item.text for item in generated.translations}
            self.assertEqual(labels[LanguageCode.RU], "Отчёты")
            self.assertEqual(labels[LanguageCode.KK], "Есептер")
            parent = await PageRepository(session).by_id(parent_id)
            self.assertEqual(parent.published_revision_id, published_parent_revision)
            self.assertEqual(parent.draft_revision_id, draft_parent_revision)
            await PageService(session).publish(child_id, 42)
            await session.commit()

        async with session_factory() as session:
            self.assertEqual(len(await PageRepository(session).buttons(parent_id)), 2)
            link = await session.scalar(sa.select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.child_page_id == child_id))
            generated = await session.get(type(after[-1]), link.button_id)
            await session.delete(generated)
            await session.delete(link)
            await session.commit()

        async with session_factory() as session:
            repaired = await PageService(session).repair_published_auto_draft_buttons(42)
            await session.commit()
            self.assertEqual(repaired, 1)

        async with session_factory() as session:
            recovered = await PageRepository(session).buttons(parent_id)
            self.assertEqual(len(recovered), 2)
            self.assertEqual(sum(1 for item in recovered if item.action_type == ButtonAction.PAGE and item.target == child_key), 1)

    async def test_auto_publish_processes_source_and_exposes_one_parent_button(self) -> None:
        self.alembic("upgrade", "head")
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            pages = PageService(session)
            parent = await pages.create("Практика", "Описание", 42)
            await pages.update_translation(parent.id, 42, LanguageCode.KK, title="Практика", text="Сипаттама")
            await pages.publish(parent.id, 42)
            session.add(TelegramImportSettings(
                chat_id=-1001, auto_draft_enabled=True,
                auto_draft_enabled_at=now - timedelta(minutes=1), settle_seconds=30,
            ))
            session.add(TelegramTopicPageMapping(
                chat_id=-1001, message_thread_id=2, parent_page_id=parent.id,
                is_active=True, auto_draft_enabled=True,
                processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
                processing_mode_enabled_at=now - timedelta(seconds=30),
                auto_attach_materials=False,
            ))
            source = TelegramContentPost(
                chat_id=-1001, message_id=501, message_thread_id=2,
                chat_type="supergroup", status=TelegramContentStatus.NEW,
                text=BILINGUAL, telegram_created_at=now,
            )
            session.add(source)
            await session.flush()
            result = await TelegramAutoDraftService(session, -1001, 42).process_claimed(source)
            self.assertIsNotNone(result)
            self.assertTrue(result.published)
            child_id = result.page_id
            parent_id = parent.id
            await session.commit()

        async with session_factory() as session:
            child = await PageRepository(session).by_id(child_id)
            self.assertEqual(child.status, PageStatus.PUBLISHED)
            buttons = await PageRepository(session).buttons(parent_id)
            generated = [button for button in buttons if button.action_type == ButtonAction.PAGE and button.target == child.key]
            self.assertEqual(len(generated), 1)
            labels = {item.language_code: item.text for item in generated[0].translations}
            self.assertTrue(labels[LanguageCode.RU])
            self.assertTrue(labels[LanguageCode.KK])
            link = await session.scalar(sa.select(TelegramAutoDraftLink).where(TelegramAutoDraftLink.child_page_id == child_id))
            self.assertEqual(link.button_id, generated[0].id)
            job = await session.scalar(sa.select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.page_id == child_id))
            self.assertEqual((job.revision_id, job.status), (child.published_revision_id, "PENDING"))

    async def test_auto_edit_sync_preserves_history_and_is_idempotent(self) -> None:
        self.alembic("upgrade", "head")
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            pages = PageService(session)
            parent = await pages.create("Практика", "Описание", 42)
            await pages.update_translation(parent.id, 42, LanguageCode.KK, title="Практика", text="Сипаттама")
            await pages.publish(parent.id, 42)
            session.add(TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(minutes=1)))
            mapping = TelegramTopicPageMapping(
                chat_id=-1001, message_thread_id=2, parent_page_id=parent.id,
                processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
                processing_mode_enabled_at=now - timedelta(seconds=30),
                edit_sync_mode=TelegramEditSyncMode.AUTO,
                is_active=True, auto_attach_materials=False,
            )
            session.add(mapping)
            source = TelegramContentPost(chat_id=-1001, message_id=601, message_thread_id=2, chat_type="supergroup", status=TelegramContentStatus.NEW, text=BILINGUAL, telegram_created_at=now)
            session.add(source)
            await session.flush()
            created = await TelegramAutoDraftService(session, -1001, 42).process_claimed(source)
            await session.commit()
            child_id, source_id = created.page_id, source.id

        async with session_factory() as session:
            child = await PageRepository(session).by_id(child_id)
            original_revision = child.published_revision_id
            result = await TelegramEditSyncService(session, 42).process(source_id, BILINGUAL_EDITED)
            self.assertEqual(result.status, "APPLIED")
            await session.commit()

        async with session_factory() as session:
            child = await PageRepository(session).by_id(child_id)
            self.assertNotEqual(child.published_revision_id, original_revision)
            self.assertIsNotNone(await session.get(BotPageRevision, original_revision))
            revision_count = await session.scalar(sa.select(sa.func.count(BotPageRevision.id)).where(BotPageRevision.page_id == child_id))
            duplicate = await TelegramEditSyncService(session, 42).process(source_id, BILINGUAL_EDITED)
            self.assertEqual(duplicate.status, "UNCHANGED")
            self.assertEqual(await session.scalar(sa.select(sa.func.count(BotPageRevision.id)).where(BotPageRevision.page_id == child_id)), revision_count)
            self.assertIsNone(await session.scalar(sa.select(TelegramEditPending).where(TelegramEditPending.source_post_id == source_id)))

    async def test_publish_job_indexes_only_published_ru_kk_and_replaces_stale_chunks(self) -> None:
        self.alembic("upgrade", "head")
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        class Provider:
            async def embed_documents(self, texts):
                return [[float(index + 1)] * 768 for index, _ in enumerate(texts)]

        index = KnowledgeIndexService(Provider(), "test-embedding")  # type: ignore[arg-type]
        async with session_factory() as session:
            pages = PageService(session)
            page = await pages.create("Докторантура", "Программы докторантуры", 42)
            await pages.update_translation(page.id, 42, LanguageCode.KK, title="Докторантура", text="Докторантура бағдарламалары")
            await pages.publish(page.id, 42)
            page_id, first_revision = page.id, page.draft_revision_id
            await session.commit()
        self.assertTrue(await process_knowledge_job(session_factory, index, 42))
        async with session_factory() as session:
            chunks = list((await session.scalars(sa.select(AIKnowledgeChunk).where(AIKnowledgeChunk.source_type == "page", AIKnowledgeChunk.source_id == page_id))).all())
            self.assertEqual({item.language_code for item in chunks}, {"ru", "kk"})
            self.assertEqual({item.revision_id for item in chunks}, {first_revision})
            page_service = PageService(session)
            await page_service.update_draft(page_id, 42, text="Обновлённые программы докторантуры")
            page = await PageRepository(session).by_id(page_id)
            await page_service.publish(page_id, 42)
            second_revision = page.draft_revision_id
            await session.commit()
        self.assertTrue(await process_knowledge_job(session_factory, index, 42))
        async with session_factory() as session:
            chunks = list((await session.scalars(sa.select(AIKnowledgeChunk).where(AIKnowledgeChunk.source_type == "page", AIKnowledgeChunk.source_id == page_id))).all())
            self.assertEqual({item.revision_id for item in chunks}, {second_revision})
            self.assertNotIn(first_revision, {item.revision_id for item in chunks})
            job = await session.scalar(sa.select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.page_id == page_id))
            self.assertEqual(job.status, "COMPLETED")

    async def _orm_smoke_test(self, message_id: int) -> None:
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with session_factory() as session:
            post = TelegramContentPost(
                chat_id=-100999,
                message_id=message_id,
                message_thread_id=42,
                chat_type="supergroup",
                chat_title="Development forum",
                status=TelegramContentStatus.NEW,
                text="ORM smoke test",
            )
            post.media.append(TelegramContentMedia(
                source_message_id=message_id,
                type=MediaType.PHOTO,
                telegram_file_id=f"file-{message_id}",
                position=0,
            ))
            session.add(post)
            await session.commit()
            loaded = await session.scalar(
                sa.select(TelegramContentPost).where(TelegramContentPost.message_id == message_id)
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.chat_title, "Development forum")
            self.assertEqual(loaded.message_thread_id, 42)
            self.assertEqual(len(loaded.media), 1)
            self.assertEqual(loaded.media[0].telegram_file_id, f"file-{message_id}")

    @staticmethod
    def _inspect_universal_schema(sync_connection) -> dict[str, object]:
        inspector = sa.inspect(sync_connection)
        post_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("telegram_content_posts")
        }
        media_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("telegram_content_media")
        }
        post_indexes = {
            tuple(item["column_names"])
            for item in inspector.get_indexes("telegram_content_posts")
        }
        media_fk = inspector.get_foreign_keys("telegram_content_media")[0]
        return {
            "post_uniques": post_uniques,
            "media_uniques": media_uniques,
            "post_indexes": post_indexes,
            "media_fk": (
                media_fk["referred_table"],
                media_fk.get("options", {}).get("ondelete"),
            ),
        }

    @staticmethod
    def _assert_model_column_parity(sync_connection) -> None:
        inspector = sa.inspect(sync_connection)
        for model in (TelegramContentPost, TelegramContentMedia):
            table = model.__table__
            actual = {column["name"]: column for column in inspector.get_columns(table.name)}
            expected = {column.name: column for column in table.columns}
            if set(actual) != set(expected):
                raise AssertionError(
                    f"{table.name} columns differ: actual={sorted(actual)}, expected={sorted(expected)}"
                )
            for name, model_column in expected.items():
                db_column = actual[name]
                if db_column["nullable"] != model_column.nullable:
                    raise AssertionError(
                        f"{table.name}.{name} nullable={db_column['nullable']}, expected={model_column.nullable}"
                    )
                actual_type = TelegramContentMigrationTests._type_signature(db_column["type"])
                expected_type = TelegramContentMigrationTests._type_signature(model_column.type)
                if actual_type != expected_type:
                    raise AssertionError(
                        f"{table.name}.{name} type={actual_type}, expected={expected_type}"
                    )
                if model_column.primary_key:
                    if db_column.get("default") is None:
                        raise AssertionError(f"{table.name}.{name} has no autoincrement default")
                else:
                    actual_has_default = db_column.get("default") is not None
                    expected_has_default = model_column.server_default is not None
                    if actual_has_default != expected_has_default:
                        raise AssertionError(
                            f"{table.name}.{name} server default mismatch: {db_column.get('default')}"
                        )

    @staticmethod
    def _type_signature(type_: sa.types.TypeEngine) -> tuple[object, ...]:
        if isinstance(type_, sa.Enum):
            return ("enum", type_.name, tuple(type_.enums))
        if isinstance(type_, sa.Text):
            return ("text",)
        if isinstance(type_, sa.String):
            return ("string", type_.length)
        if isinstance(type_, sa.DateTime):
            return ("datetime", bool(type_.timezone))
        if isinstance(type_, sa.BigInteger):
            return ("bigint",)
        if isinstance(type_, sa.Integer):
            return ("integer",)
        return (type(type_).__name__,)
