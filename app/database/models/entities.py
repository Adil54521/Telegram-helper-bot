from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.database.models.enums import AdminRole, BroadcastStatus, ButtonAction, LanguageCode, MediaType, PageStatus


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
