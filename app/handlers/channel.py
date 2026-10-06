import logging

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import ChannelPostCallback
from app.config import Settings
from app.database.models import Admin
from app.services.channel_import import IngestResult, TelegramContentImportService, message_media, source_text, suggested_title
from app.services.permissions import Permission, ROLE_PERMISSIONS
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


async def _notify_admins(bot: Bot, session: AsyncSession, settings: Settings, item_id: int, title: str) -> None:
    if not settings.content_channel_notify_admins:
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


async def _ingest(message: Message, session: AsyncSession, settings: Settings, bot: Bot, *, edited: bool) -> IngestResult | None:
    if settings.content_chat_id is None or message.chat.id != settings.content_chat_id or not supported_content(message):
        return None
    result = await TelegramContentImportService(session).ingest(message, edited=edited)
    logger.info("Telegram content received chat_id=%s message_id=%s thread_id=%s chat_type=%s media_count=%s status=%s edited=%s", message.chat.id, message.message_id, message.message_thread_id, message.chat.type, len(result.post.media), result.post.status.value, edited)
    if result.created and not edited:
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
