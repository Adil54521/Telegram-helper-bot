from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database.base import Base, TimestampMixin
from app.database.models.enums import AdminRole, BroadcastStatus, ButtonAction, LanguageCode, MediaType, PageStatus, TelegramContentStatus, TelegramContentTarget, TelegramEditSyncMode, TelegramFileSyncMode, TelegramProcessingMode


class TelegramUser(Base):
    __tablename__ = "telegram_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str | None] = mapped_column(String(16))
    content_language_code: Mapped[str | None] = mapped_column(String(8), index=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Admin(Base, TimestampMixin):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[AdminRole] = mapped_column(Enum(AdminRole, name="admin_role"))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BotPage(Base, TimestampMixin):
    __tablename__ = "bot_pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[PageStatus] = mapped_column(Enum(PageStatus, name="page_status"), default=PageStatus.DRAFT)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    published_revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", use_alter=True))
    draft_revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", use_alter=True))
    revisions: Mapped[list["BotPageRevision"]] = relationship(foreign_keys="BotPageRevision.page_id", back_populates="page", cascade="all, delete-orphan")
    published_revision: Mapped["BotPageRevision | None"] = relationship(foreign_keys=[published_revision_id], post_update=True)
    draft_revision: Mapped["BotPageRevision | None"] = relationship(foreign_keys=[draft_revision_id], post_update=True)


class BotPageRevision(Base):
    __tablename__ = "bot_page_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    media_type: Mapped[MediaType | None] = mapped_column(Enum(MediaType, name="media_type"))
    media_file_id: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    page: Mapped[BotPage] = relationship(foreign_keys=[page_id], back_populates="revisions")
    translations: Mapped[list["BotPageRevisionTranslation"]] = relationship(back_populates="revision", cascade="all, delete-orphan", lazy="selectin")
    attachments: Mapped[list["PageAttachment"]] = relationship(back_populates="revision", cascade="all, delete-orphan", lazy="selectin")


class BotPageRevisionTranslation(Base):
    __tablename__ = "bot_page_revision_translations"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), index=True)
    language_code: Mapped[LanguageCode] = mapped_column(Enum(LanguageCode, name="language_code"))
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    revision: Mapped[BotPageRevision] = relationship(back_populates="translations")
    __table_args__ = (UniqueConstraint("revision_id", "language_code", name="uq_page_revision_language"),)


class PageAttachment(Base, TimestampMixin):
    __tablename__ = "page_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), index=True)
    type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="attachment_type"))
    telegram_file_id: Mapped[str] = mapped_column(String(512))
    file_name: Mapped[str | None] = mapped_column(String(255))
    caption_ru: Mapped[str | None] = mapped_column(String(1024))
    caption_kk: Mapped[str | None] = mapped_column(String(1024))
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_source_post_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="SET NULL"), index=True)
    telegram_source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_source_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    revision: Mapped[BotPageRevision] = relationship(back_populates="attachments")


class BotButton(Base, TimestampMixin):
    __tablename__ = "bot_buttons"
    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[ButtonAction] = mapped_column(Enum(ButtonAction, name="button_action"))
    target: Mapped[str | None] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, default=0)
    row: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_buttons_page_order", "page_id", "row", "position"),)
    translations: Mapped[list["BotButtonTranslation"]] = relationship(back_populates="button", cascade="all, delete-orphan", lazy="selectin")


class BotButtonTranslation(Base):
    __tablename__ = "bot_button_translations"
    id: Mapped[int] = mapped_column(primary_key=True)
    button_id: Mapped[int] = mapped_column(ForeignKey("bot_buttons.id", ondelete="CASCADE"), index=True)
    language_code: Mapped[LanguageCode] = mapped_column(Enum(LanguageCode, name="language_code"))
    text: Mapped[str] = mapped_column(String(128))
    button: Mapped[BotButton] = relationship(back_populates="translations")
    __table_args__ = (UniqueConstraint("button_id", "language_code", name="uq_button_language"),)


class FAQItem(Base, TimestampMixin):
    __tablename__ = "faq_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_revision_id: Mapped[int | None] = mapped_column(ForeignKey("faq_revisions.id", ondelete="SET NULL"), nullable=True, index=True)
    translations: Mapped[list["FAQItemTranslation"]] = relationship(back_populates="faq", cascade="all, delete-orphan", lazy="selectin")


class FAQItemTranslation(Base):
    __tablename__ = "faq_item_translations"
    id: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_items.id", ondelete="CASCADE"), index=True)
    language_code: Mapped[LanguageCode] = mapped_column(Enum(LanguageCode, name="language_code"))
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    faq: Mapped[FAQItem] = relationship(back_populates="translations")
    __table_args__ = (UniqueConstraint("faq_id", "language_code", name="uq_faq_language"),)


class FAQRevision(Base):
    __tablename__ = "faq_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_items.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_ru: Mapped[str] = mapped_column(String(500))
    answer_ru: Mapped[str] = mapped_column(Text)
    question_kk: Mapped[str | None] = mapped_column(String(500))
    answer_kk: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_post_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="SET NULL"), index=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    __table_args__ = (UniqueConstraint("faq_id", "version", name="uq_faq_revision_version"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PageView(Base):
    __tablename__ = "page_views"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"), index=True)
    request_type: Mapped[str] = mapped_column(String(32), index=True)
    language: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    retrieval_count: Mapped[int] = mapped_column(Integer, default=0)
    gemini_called: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_price_per_1m: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("0"))
    output_price_per_1m: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("0"))
    input_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("0"))
    output_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_ai_usage_type_created", "request_type", "created_at"),
    )


class AIKnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "ai_knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    revision_id: Mapped[int | None] = mapped_column(Integer)
    language_code: Mapped[str] = mapped_column(String(8), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding: Mapped[list[float]] = mapped_column(Vector(768))

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "revision_id",
            "language_code",
            "chunk_index",
            name="uq_ai_knowledge_chunk_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_ai_knowledge_chunk_source", "source_type", "source_id"),
    )


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    media_file_id: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[BroadcastStatus] = mapped_column(Enum(BroadcastStatus, name="broadcast_status"), default=BroadcastStatus.DRAFT)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)


class TelegramContentPost(Base, TimestampMixin):
    __tablename__ = "telegram_content_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    media_group_id: Mapped[str | None] = mapped_column(String(128), index=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer)
    chat_type: Mapped[str] = mapped_column(String(16))
    chat_title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[TelegramContentStatus] = mapped_column(
        Enum(TelegramContentStatus, name="telegram_content_status"),
        default=TelegramContentStatus.NEW,
        index=True,
    )
    text: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    telegram_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_by: Mapped[int | None] = mapped_column(BigInteger)
    created_page_id: Mapped[int | None] = mapped_column(ForeignKey("bot_pages.id", ondelete="SET NULL"))
    created_faq_id: Mapped[int | None] = mapped_column(ForeignKey("faq_items.id", ondelete="SET NULL"))
    auto_process_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    auto_processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    auto_last_error: Mapped[str | None] = mapped_column(String(255))
    media: Mapped[list["TelegramContentMedia"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_telegram_content_post_message"),
        Index("ix_telegram_content_posts_status_created", "status", "created_at"),
        Index("ix_telegram_content_posts_group", "chat_id", "media_group_id", "message_thread_id"),
    )


class TelegramContentMedia(Base):
    __tablename__ = "telegram_content_media"
    id: Mapped[int] = mapped_column(primary_key=True)
    content_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), index=True)
    source_message_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="telegram_content_media_type"))
    telegram_file_id: Mapped[str] = mapped_column(String(512))
    file_unique_id: Mapped[str | None] = mapped_column(String(512))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    post: Mapped[TelegramContentPost] = relationship(back_populates="media")

    __table_args__ = (
        UniqueConstraint("content_post_id", "source_message_id", name="uq_telegram_content_media_message"),
    )


class TelegramImportSettings(Base, TimestampMixin):
    __tablename__ = "telegram_import_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    auto_draft_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    auto_draft_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settle_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    notify_admins: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class TelegramForumTopic(Base, TimestampMixin):
    __tablename__ = "telegram_forum_topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (UniqueConstraint("chat_id", "message_thread_id", name="uq_telegram_forum_topic"),)


class TelegramTopicPageMapping(Base, TimestampMixin):
    __tablename__ = "telegram_topic_page_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_page_id: Mapped[int | None] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=True, index=True)
    content_target: Mapped[TelegramContentTarget] = mapped_column(
        Enum(TelegramContentTarget, name="telegram_content_target"),
        default=TelegramContentTarget.PAGE,
        server_default=TelegramContentTarget.PAGE.value,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    auto_draft_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    processing_mode: Mapped[TelegramProcessingMode] = mapped_column(
        Enum(TelegramProcessingMode, name="telegram_processing_mode"),
        default=TelegramProcessingMode.AUTO_DRAFT,
        server_default=TelegramProcessingMode.AUTO_DRAFT.value,
        nullable=False,
    )
    processing_mode_enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    edit_sync_mode: Mapped[TelegramEditSyncMode] = mapped_column(
        Enum(TelegramEditSyncMode, name="telegram_edit_sync_mode"),
        default=TelegramEditSyncMode.OFF,
        server_default=TelegramEditSyncMode.OFF.value,
        nullable=False,
    )
    file_sync_mode: Mapped[TelegramFileSyncMode] = mapped_column(
        Enum(TelegramFileSyncMode, name="telegram_file_sync_mode"),
        default=TelegramFileSyncMode.OFF,
        server_default=TelegramFileSyncMode.OFF.value,
        nullable=False,
    )
    auto_attach_materials: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("chat_id", "message_thread_id", name="uq_telegram_topic_page_mapping"),)


class TelegramAutoDraftLink(Base):
    __tablename__ = "telegram_auto_draft_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    child_page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), unique=True, nullable=False)
    parent_page_id: Mapped[int | None] = mapped_column(ForeignKey("bot_pages.id", ondelete="SET NULL"), index=True)
    mapping_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_topic_page_mappings.id", ondelete="SET NULL"))
    button_id: Mapped[int | None] = mapped_column(ForeignKey("bot_buttons.id", ondelete="SET NULL"), unique=True)
    last_synced_revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="SET NULL"))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramEditPending(Base, TimestampMixin):
    __tablename__ = "telegram_edit_pending"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    base_revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="SET NULL"))
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title_ru: Mapped[str | None] = mapped_column(String(255))
    text_ru: Mapped[str | None] = mapped_column(Text)
    title_kk: Mapped[str | None] = mapped_column(String(255))
    text_kk: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="REVIEW", server_default="REVIEW", nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(255))


class TelegramFAQSyncLink(Base):
    __tablename__ = "telegram_faq_sync_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_items.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    last_synced_revision_id: Mapped[int | None] = mapped_column(ForeignKey("faq_revisions.id", ondelete="SET NULL"))
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    last_edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TelegramFAQEditPending(Base, TimestampMixin):
    __tablename__ = "telegram_faq_edit_pending"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_items.id", ondelete="CASCADE"), nullable=False, index=True)
    base_revision_id: Mapped[int | None] = mapped_column(ForeignKey("faq_revisions.id", ondelete="SET NULL"))
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    question_ru: Mapped[str | None] = mapped_column(String(500))
    answer_ru: Mapped[str | None] = mapped_column(Text)
    question_kk: Mapped[str | None] = mapped_column(String(500))
    answer_kk: Mapped[str | None] = mapped_column(Text)
    source_edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="REVIEW", server_default="REVIEW", nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(255))


class AIKnowledgeSyncJob(Base, TimestampMixin):
    __tablename__ = "ai_knowledge_sync_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), unique=True, nullable=True)
    faq_id: Mapped[int | None] = mapped_column(ForeignKey("faq_items.id", ondelete="CASCADE"), unique=True, nullable=True)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), nullable=True)
    faq_revision_id: Mapped[int | None] = mapped_column(ForeignKey("faq_revisions.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", server_default="PENDING", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(String(255))


class TelegramAttachmentReplacement(Base, TimestampMixin):
    __tablename__ = "telegram_attachment_replacements"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False)
    page_id: Mapped[int] = mapped_column(ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("page_attachments.id", ondelete="SET NULL"))
    base_revision_id: Mapped[int | None] = mapped_column(ForeignKey("bot_page_revisions.id", ondelete="SET NULL"))
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="attachment_type", create_type=False), nullable=False)
    new_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    new_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    new_file_name: Mapped[str | None] = mapped_column(String(255))
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="REVIEW", server_default="REVIEW", nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(255))

class AIConversation(Base):
    __tablename__ = "ai_conversation"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=True)
    language_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)