"""reconcile Telegram content tables with current ORM metadata

Revision ID: 20261006_08
Revises: 20261006_07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261006_08"
down_revision = "20261006_07"
branch_labels = None
depends_on = None


POSTS = "telegram_content_posts"
MEDIA = "telegram_content_media"


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> dict[str, dict]:
    return {column["name"]: column for column in _inspector().get_columns(table)}


def _ensure_enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    enum = postgresql.ENUM(*values, name=name, create_type=False)
    enum.create(op.get_bind(), checkfirst=True)
    return enum


def _same_type(actual: sa.types.TypeEngine, expected: sa.types.TypeEngine) -> bool:
    if isinstance(expected, postgresql.ENUM):
        return isinstance(actual, postgresql.ENUM) and actual.name == expected.name
    if isinstance(expected, sa.Text):
        return isinstance(actual, sa.Text)
    if isinstance(expected, sa.String):
        return isinstance(actual, sa.String) and actual.length == expected.length
    if isinstance(expected, sa.DateTime):
        return isinstance(actual, sa.DateTime) and bool(actual.timezone) == bool(expected.timezone)
    return type(actual) is type(expected)


def _cast_sql(column: str, expected: sa.types.TypeEngine) -> str:
    if isinstance(expected, postgresql.ENUM):
        return f'"{column}"::text::"{expected.name}"'
    return f'"{column}"::{expected.compile(dialect=postgresql.dialect())}'


def _add_missing_columns() -> None:
    status_enum = _ensure_enum("telegram_content_status", ("NEW", "DRAFT_CREATED", "IGNORED"))
    media_enum = _ensure_enum("telegram_content_media_type", ("PHOTO", "DOCUMENT"))
    post_columns = _columns(POSTS)
    optional_posts = {
        "media_group_id": sa.String(128),
        "message_thread_id": sa.Integer(),
        "chat_title": sa.String(255),
        "text": sa.Text(),
        "caption": sa.Text(),
        "telegram_created_at": sa.DateTime(timezone=True),
        "processed_at": sa.DateTime(timezone=True),
        "processed_by": sa.BigInteger(),
        "created_page_id": sa.Integer(),
        "created_faq_id": sa.Integer(),
    }
    for name, type_ in optional_posts.items():
        if name not in post_columns:
            op.add_column(POSTS, sa.Column(name, type_, nullable=True))
    if "chat_type" not in post_columns:
        op.add_column(POSTS, sa.Column("chat_type", sa.String(16), nullable=True))
        op.execute(sa.text(f"UPDATE {POSTS} SET chat_type = 'channel' WHERE chat_type IS NULL"))
    if "status" not in post_columns:
        op.add_column(POSTS, sa.Column("status", status_enum, nullable=True))
        op.execute(sa.text(f"UPDATE {POSTS} SET status = 'NEW' WHERE status IS NULL"))
    for name in ("created_at", "updated_at"):
        if name not in post_columns:
            op.add_column(POSTS, sa.Column(name, sa.DateTime(timezone=True), nullable=True))
            op.execute(sa.text(f"UPDATE {POSTS} SET {name} = now() WHERE {name} IS NULL"))

    media_columns = _columns(MEDIA)
    optional_media = {
        "file_unique_id": sa.String(512),
        "file_name": sa.String(255),
        "file_size": sa.BigInteger(),
    }
    for name, type_ in optional_media.items():
        if name not in media_columns:
            op.add_column(MEDIA, sa.Column(name, type_, nullable=True))
    if "type" not in media_columns:
        op.add_column(MEDIA, sa.Column("type", media_enum, nullable=True))
    if "position" not in media_columns:
        op.add_column(MEDIA, sa.Column("position", sa.Integer(), nullable=True))
        op.execute(sa.text(f"UPDATE {MEDIA} SET position = 0 WHERE position IS NULL"))
    if "created_at" not in media_columns:
        op.add_column(MEDIA, sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text(f"UPDATE {MEDIA} SET created_at = now() WHERE created_at IS NULL"))


def _reconcile_columns() -> None:
    status_enum = _ensure_enum("telegram_content_status", ("NEW", "DRAFT_CREATED", "IGNORED"))
    media_enum = _ensure_enum("telegram_content_media_type", ("PHOTO", "DOCUMENT"))
    expected = {
        POSTS: {
            "id": (sa.Integer(), False, sa.text("nextval")),
            "chat_id": (sa.BigInteger(), False, None),
            "message_id": (sa.Integer(), False, None),
            "media_group_id": (sa.String(128), True, None),
            "message_thread_id": (sa.Integer(), True, None),
            "chat_type": (sa.String(16), False, None),
            "chat_title": (sa.String(255), True, None),
            "status": (status_enum, False, None),
            "text": (sa.Text(), True, None),
            "caption": (sa.Text(), True, None),
            "telegram_created_at": (sa.DateTime(timezone=True), True, None),
            "processed_at": (sa.DateTime(timezone=True), True, None),
            "processed_by": (sa.BigInteger(), True, None),
            "created_page_id": (sa.Integer(), True, None),
            "created_faq_id": (sa.Integer(), True, None),
            "created_at": (sa.DateTime(timezone=True), False, sa.text("now()")),
            "updated_at": (sa.DateTime(timezone=True), False, sa.text("now()")),
        },
        MEDIA: {
            "id": (sa.Integer(), False, sa.text("nextval")),
            "content_post_id": (sa.Integer(), False, None),
            "source_message_id": (sa.Integer(), False, None),
            "type": (media_enum, False, None),
            "telegram_file_id": (sa.String(512), False, None),
            "file_unique_id": (sa.String(512), True, None),
            "file_name": (sa.String(255), True, None),
            "file_size": (sa.BigInteger(), True, None),
            "position": (sa.Integer(), False, None),
            "created_at": (sa.DateTime(timezone=True), False, sa.text("now()")),
        },
    }
    required_existing = {
        POSTS: {"id", "chat_id", "message_id"},
        MEDIA: {"id", "content_post_id", "source_message_id", "telegram_file_id"},
    }
    for table, specifications in expected.items():
        actual_columns = _columns(table)
        missing_core = required_existing[table] - actual_columns.keys()
        if missing_core:
            raise RuntimeError(f"Cannot safely reconstruct {table} columns: {sorted(missing_core)}")
        for name, (type_, nullable, server_default) in specifications.items():
            actual = actual_columns[name]
            kwargs: dict[str, object] = {
                "existing_type": actual["type"],
                "nullable": nullable,
            }
            if name not in {"id"}:
                kwargs["server_default"] = server_default
            if not _same_type(actual["type"], type_):
                if actual.get("default") is not None:
                    op.alter_column(table, name, existing_type=actual["type"], server_default=None)
                kwargs["type_"] = type_
                kwargs["postgresql_using"] = _cast_sql(name, type_)
            op.alter_column(table, name, **kwargs)


def _drop_non_primary_indexes(table: str) -> None:
    for index in _inspector().get_indexes(table):
        if not index.get("unique"):
            op.drop_index(index["name"], table_name=table)


def _drop_unique_constraints(table: str) -> None:
    for constraint in _inspector().get_unique_constraints(table):
        if constraint.get("name"):
            op.drop_constraint(constraint["name"], table, type_="unique")


def _ensure_foreign_key(table: str, column: str, target_table: str, target_column: str, ondelete: str) -> None:
    matching = False
    for foreign_key in _inspector().get_foreign_keys(table):
        if foreign_key["constrained_columns"] != [column]:
            continue
        expected = (
            foreign_key["referred_table"] == target_table
            and foreign_key["referred_columns"] == [target_column]
            and foreign_key.get("options", {}).get("ondelete", "").upper() == ondelete
        )
        if expected:
            matching = True
        elif foreign_key.get("name"):
            op.drop_constraint(foreign_key["name"], table, type_="foreignkey")
    if not matching:
        op.create_foreign_key(None, table, target_table, [column], [target_column], ondelete=ondelete)


def _reconcile_constraints_and_indexes() -> None:
    _ensure_foreign_key(POSTS, "created_page_id", "bot_pages", "id", "SET NULL")
    _ensure_foreign_key(POSTS, "created_faq_id", "faq_items", "id", "SET NULL")
    _ensure_foreign_key(MEDIA, "content_post_id", POSTS, "id", "CASCADE")
    _drop_unique_constraints(MEDIA)
    _drop_unique_constraints(POSTS)
    _drop_non_primary_indexes(MEDIA)
    _drop_non_primary_indexes(POSTS)
    op.create_unique_constraint("uq_telegram_content_post_message", POSTS, ["chat_id", "message_id"])
    op.create_unique_constraint("uq_telegram_content_media_message", MEDIA, ["content_post_id", "source_message_id"])
    op.create_index("ix_telegram_content_posts_media_group_id", POSTS, ["media_group_id"])
    op.create_index("ix_telegram_content_posts_status", POSTS, ["status"])
    op.create_index("ix_telegram_content_posts_status_created", POSTS, ["status", "created_at"])
    op.create_index("ix_telegram_content_posts_group", POSTS, ["chat_id", "media_group_id", "message_thread_id"])
    op.create_index("ix_telegram_content_media_content_post_id", MEDIA, ["content_post_id"])


def _ensure_id_sequences() -> None:
    for table in (POSTS, MEDIA):
        sequence = f"{table}_id_seq"
        op.execute(sa.text(f'CREATE SEQUENCE IF NOT EXISTS "{sequence}"'))
        op.execute(sa.text(
            f"SELECT setval('{sequence}', COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"
        ))
        op.execute(sa.text(f'ALTER SEQUENCE "{sequence}" OWNED BY {table}.id'))
        op.execute(sa.text(
            f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{sequence}'::regclass)"
        ))


def upgrade() -> None:
    tables = set(_inspector().get_table_names())
    if POSTS not in tables or MEDIA not in tables:
        raise RuntimeError("Migration 20261006_08 requires telegram_content_posts and telegram_content_media")
    _add_missing_columns()
    _reconcile_columns()
    _ensure_id_sequences()
    _reconcile_constraints_and_indexes()


def downgrade() -> None:
    # Reconciliation is intentionally non-destructive: environments at revision 07
    # had different physical schemas, so dropping repaired columns would lose data.
    pass
