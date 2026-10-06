import os
import subprocess
import unittest

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import TelegramContentMedia, TelegramContentPost
from app.database.models.enums import MediaType, TelegramContentStatus


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

        self.alembic("upgrade", "20261006_08")
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
