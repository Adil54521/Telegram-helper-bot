import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import ChannelPostCallback
from app.config import Settings
from app.database.models import Admin
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.channel_import import IngestResult, TelegramContentImportService, message_media, source_text, suggested_title
from app.services.permissions import Permission, ROLE_PERMISSIONS
from app.services.telegram_auto_drafts import TelegramAutoDraftService, processing_mode
from app.database.models.enums import TelegramProcessingMode
from app.services.telegram_edit_sync import TelegramEditSyncService
from app.services.telegram_attachment_sync import TelegramAttachmentSyncService
from app.utils.presentation import html_text, text_preview

logger = logging.getLogger(__name__)
router = Router(name="telegram_content_ingestion")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


def supported_content(message: Message) -> bool:
    return bool((message.text or message.caption or "").strip() or message_media(message))


class ConfiguredGroupFilter(BaseFilter):
    async def __call__(self, message: Message, settings: Settings) -> bool:
        return bool(settings.content_chat_id is not None and message.chat.id == settings.content_chat_id and message.chat.type in GROUP_TYPES)


class OfficialGroupContentFilter(ConfiguredGroupFilter):
    async def __call__(self, message: Message, settings: Settings, bot: Bot) -> bool:
        if not await super().__call__(message, settings) or not supported_content(message):
            return False
        if message.sender_chat is not None:
            return message.sender_chat.id == message.chat.id
        sender = message.from_user
        if sender is None or sender.is_bot:
            return False
        try:
            member = await bot.get_chat_member(message.chat.id, sender.id)
        except TelegramAPIError as error:
            logger.warning("Telegram content admin check failed chat_id=%s message_id=%s error=%s", message.chat.id, message.message_id, type(error).__name__)
            return False
        allowed = member.status in ADMIN_STATUSES
        if not allowed:
            logger.debug("Telegram content ignored reason=non_admin chat_id=%s message_id=%s", message.chat.id, message.message_id)
        return allowed


@router.message(ConfiguredGroupFilter(), F.forum_topic_created | F.forum_topic_edited)
async def capture_forum_topic_metadata(message: Message, session: AsyncSession) -> None:
    thread_id = message.message_thread_id
    event = message.forum_topic_created or message.forum_topic_edited
    topic_name = (getattr(event, "name", None) or "").strip()
    if thread_id is None or not topic_name:
        return
    await TelegramAutoDraftRepository(session).upsert_topic_name(message.chat.id, thread_id, topic_name[:255])
    logger.info("Telegram forum topic metadata updated chat_id=%s thread_id=%s", message.chat.id, thread_id)


async def _notify_admins(bot: Bot, session: AsyncSession, settings: Settings, item_id: int, title: str) -> None:
    if settings.content_chat_id is None or not await TelegramAutoDraftRepository(session).notifications_enabled(
        settings.content_chat_id,
        settings.content_channel_notify_admins,
    ):
        return
    admins = list((await session.scalars(select(Admin).where(Admin.is_active.is_(True)))).all())
    recipients = {admin.telegram_id for admin in admins if Permission.CONTENT in ROLE_PERMISSIONS.get(admin.role, set())}
    recipients.add(settings.super_admin_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👁 Открыть в CMS", callback_data=ChannelPostCallback(action="open", item_id=item_id).pack())]])
    for telegram_id in recipients:
        try:
            await bot.send_message(telegram_id, f"🔔 <b>Новая Telegram-публикация</b>\n\n{html_text(text_preview(title, 100))}", reply_markup=markup)
        except TelegramAPIError as error:
            logger.warning("Telegram content notification failed admin_id=%s error=%s", telegram_id, type(error).__name__)


async def _notify_edit_review(bot: Bot, session: AsyncSession, settings: Settings, pending_id: int, status: str) -> None:
    if settings.content_chat_id is None or not await TelegramAutoDraftRepository(session).notifications_enabled(
        settings.content_chat_id, settings.content_channel_notify_admins,
    ):
        return
    admins = list((await session.scalars(select(Admin).where(Admin.is_active.is_(True)))).all())
    recipients = {admin.telegram_id for admin in admins if Permission.CONTENT in ROLE_PERMISSIONS.get(admin.role, set())}
    recipients.add(settings.super_admin_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Проверить изменение", callback_data=ChannelPostCallback(action="edit_open", item_id=pending_id).pack())]
    ])
    for telegram_id in recipients:
        try:
            await bot.send_message(
                telegram_id,
                "⚠️ <b>Изменение Telegram-публикации требует проверки</b>\n\n"
                f"Статус: {html_text(status)}",
                reply_markup=markup,
            )
        except TelegramAPIError as error:
            logger.warning("Telegram edit review notification failed admin_id=%s error=%s", telegram_id, type(error).__name__)


async def _notify_attachment_review(bot: Bot, session: AsyncSession, settings: Settings, pending_id: int, status: str) -> None:
    if settings.content_chat_id is None or not await TelegramAutoDraftRepository(session).notifications_enabled(
        settings.content_chat_id, settings.content_channel_notify_admins,
    ):
        return
    admins = list((await session.scalars(select(Admin).where(Admin.is_active.is_(True)))).all())
    recipients = {admin.telegram_id for admin in admins if Permission.CONTENT in ROLE_PERMISSIONS.get(admin.role, set())}
    recipients.add(settings.super_admin_id)
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👁 Проверить замену", callback_data=ChannelPostCallback(action="file_open", item_id=pending_id).pack())
    ]])
    for telegram_id in recipients:
        try:
            await bot.send_message(
                telegram_id,
                "📎 <b>Изменение Telegram-материала</b>\n\n"
                f"Статус: {html_text(status)}",
                reply_markup=markup,
            )
        except TelegramAPIError as error:
            logger.warning("Telegram attachment review notification failed admin_id=%s error=%s", telegram_id, type(error).__name__)


async def _should_notify_new_post(session: AsyncSession, post) -> bool:
    if post.message_thread_id is None:
        return True
    mapping = await TelegramAutoDraftRepository(session).mapping(post.chat_id, post.message_thread_id)
    return not mapping or not mapping.is_active or processing_mode(mapping) == TelegramProcessingMode.MANUAL


async def _ingest(message: Message, session: AsyncSession, settings: Settings, bot: Bot, *, edited: bool) -> IngestResult | None:
    if settings.content_chat_id is None or message.chat.id != settings.content_chat_id or not supported_content(message):
        return None
    result = await TelegramContentImportService(session).ingest(message, edited=edited)
    if result is None:
        logger.debug("Unknown edited Telegram message ignored chat_id=%s message_id=%s", message.chat.id, message.message_id)
        return None
    if isinstance(session, AsyncSession):
        if edited and result.post.status.value != "NEW":
            file_result = await TelegramAttachmentSyncService(session, settings.super_admin_id).process_edit(
                result.post.id, message_media(message), message.message_id,
            )
            if file_result.pending_id:
                await _notify_attachment_review(bot, session, settings, file_result.pending_id, file_result.status)
            edit_result = await TelegramEditSyncService(session, settings.super_admin_id).process(
                result.post.id,
                (message.text or message.caption or "").strip(),
            )
            if edit_result.pending_id:
                await _notify_edit_review(bot, session, settings, edit_result.pending_id, edit_result.status)
        else:
            await TelegramAutoDraftService(session, settings.content_chat_id, settings.super_admin_id).schedule(result.post, result.media_count)
    logger.info("Telegram content received chat_id=%s message_id=%s thread_id=%s chat_type=%s media_count=%s status=%s edited=%s", message.chat.id, message.message_id, message.message_thread_id, message.chat.type, result.media_count, result.post.status.value, edited)
    if result.created and not edited:
        should_notify = True
        if isinstance(session, AsyncSession):
            should_notify = await _should_notify_new_post(session, result.post)
        if should_notify:
            await _notify_admins(bot, session, settings, result.post.id, suggested_title(source_text(result.post)))
    return result


@router.channel_post()
async def channel_post(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    if settings.content_chat_id is not None and message.chat.id == settings.content_chat_id:
        await _ingest(message, session, settings, bot, edited=False)


@router.edited_channel_post()
async def edited_channel_post(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    if settings.content_chat_id is not None and message.chat.id == settings.content_chat_id:
        await _ingest(message, session, settings, bot, edited=True)


@router.message(OfficialGroupContentFilter())
async def group_post(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    await _ingest(message, session, settings, bot, edited=False)


@router.edited_message(OfficialGroupContentFilter())
async def edited_group_post(message: Message, session: AsyncSession, settings: Settings, bot: Bot) -> None:
    await _ingest(message, session, settings, bot, edited=True)


@router.message(ConfiguredGroupFilter())
async def ignore_configured_group_noise(message: Message) -> None:
    return None
