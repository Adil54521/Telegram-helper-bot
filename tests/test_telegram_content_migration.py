import os
import subprocess
import unittest

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


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
                    channel_title VARCHAR(255),
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
                    (channel_id, message_id, channel_title, status, text)
                    VALUES (-100123, 77, 'Legacy source', 'NEW', 'Preserve me')
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

        self.alembic("upgrade", "20261006_07")
        async with self.engine.connect() as connection:
            post = (await connection.execute(sa.text(
                "SELECT id, chat_id, message_thread_id, chat_type, text FROM telegram_content_posts"
            ))).mappings().one()
            media = (await connection.execute(sa.text(
                "SELECT content_post_id, source_message_id, telegram_file_id FROM telegram_content_media"
            ))).mappings().one()
            self.assertEqual(post["chat_id"], -100123)
            self.assertIsNone(post["message_thread_id"])
            self.assertEqual(post["chat_type"], "channel")
            self.assertEqual(post["text"], "Preserve me")
            self.assertEqual(media["content_post_id"], post["id"])
            self.assertEqual(media["telegram_file_id"], "telegram-file")
            schema = await connection.run_sync(self._inspect_universal_schema)

        self.assertIn(("chat_id", "message_id"), schema["post_uniques"])
        self.assertIn(("content_post_id", "source_message_id"), schema["media_uniques"])
        self.assertIn(("status", "created_at"), schema["post_indexes"])
        self.assertIn(("chat_id", "media_group_id", "message_thread_id"), schema["post_indexes"])
        self.assertEqual(schema["media_fk"], ("telegram_content_posts", "CASCADE"))

        self.alembic("downgrade", "20261006_06")
        async with self.engine.connect() as connection:
            post = (await connection.execute(sa.text(
                "SELECT id, channel_id, channel_title, text FROM channel_posts"
            ))).mappings().one()
            media = (await connection.execute(sa.text(
                "SELECT channel_post_id, source_message_id, telegram_file_id FROM channel_post_media"
            ))).mappings().one()
            self.assertEqual(post["channel_id"], -100123)
            self.assertEqual(post["text"], "Preserve me")
            self.assertEqual(media["channel_post_id"], post["id"])
            self.assertEqual(media["telegram_file_id"], "telegram-file")

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
