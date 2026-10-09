import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIService
from app.callbacks import AdminMenuCallback, ChannelPostCallback
from app.config import Settings
from app.database.models import AuditLog, BotPage, BotPageRevision, FAQItem, PageAttachment, TelegramAttachmentReplacement, TelegramContentPost, TelegramEditPending, TelegramFAQEditPending, TelegramTopicPageMapping
from app.database.models.enums import LanguageCode, MediaType, TelegramContentStatus, TelegramContentTarget, TelegramEditSyncMode, TelegramFileSyncMode, TelegramProcessingMode
from app.database.repositories.channel_posts import TelegramContentRepository
from app.database.repositories.pages import PageRepository
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.database.repositories.users import upsert_user
from app.filters.admin import HasPermission
from app.services.channel_import import MAX_TRANSFER_DOCUMENT_SIZE, TelegramContentImportService, source_text, suggested_title
from app.services.telegram_content_parser import (
    ParsedTelegramContent,
    TelegramContentConfidence,
    TelegramContentParseKind,
    TelegramContentParser,
)
from app.services.telegram_faq_parser import TelegramFAQParser
from app.services.telegram_edit_sync import TelegramEditSyncService
from app.services.telegram_faq_edit_sync import TelegramFAQEditSyncService
from app.services.faq_revisions import faq_snapshot
from app.services.telegram_attachment_sync import TelegramAttachmentSyncService
from app.services.telegram_publication_grouper import (
    PublicationGroupingResult,
    SelectedPublicationMaterial,
    TelegramPublicationGrouper,
)
from app.services.knowledge_sync import queue_faq_knowledge_sync
from app.services.permissions import Permission, PermissionService
from app.states.admin import ModerateChannelPost
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text
from app.utils.wizard import consume_input, finish_wizard, remember_wizard

router = Router(name="admin_channel_posts")
logger = logging.getLogger(__name__)
router.callback_query.filter(HasPermission(Permission.CONTENT))
router.message.filter(HasPermission(Permission.CONTENT))
PAGE_SIZE = 8
DATA_KEYS = (
    "channel_post_id", "channel_page", "channel_filter", "channel_type", "channel_reason",
    "channel_title_ru", "channel_text_ru", "channel_title_kk", "channel_text_kk",
    "channel_question_ru", "channel_answer_ru", "channel_question_kk", "channel_answer_kk",
    "channel_edit_field", "channel_input_truncated",
    "channel_material_source_id", "channel_materials",
)
CONTENT_PARSER = TelegramContentParser()
FAQ_PARSER = TelegramFAQParser()


def parse_post(post: TelegramContentPost) -> ParsedTelegramContent:
    try:
        parsed = CONTENT_PARSER.parse(source_text(post))
    except Exception:
        logger.exception("Telegram content parse failed post_id=%s", post.id)
        return ParsedTelegramContent(TelegramContentParseKind.UNKNOWN)
    logger.debug(
        "Telegram content parsed post_id=%s parse_kind=%s confidence=%s has_ru=%s has_kk=%s",
        post.id,
        parsed.kind.value,
        parsed.confidence.value,
        bool(parsed.title_ru or parsed.text_ru),
        bool(parsed.title_kk or parsed.text_kk),
    )
    return parsed


def parsed_preview(parsed: ParsedTelegramContent) -> str:
    labels = {
        TelegramContentParseKind.BILINGUAL: "🔎 <b>Распознано:</b> 🇰🇿 Қазақша + 🇷🇺 Русский",
        TelegramContentParseKind.RU_ONLY: "🔎 <b>Распознано:</b> только 🇷🇺 Русский",
        TelegramContentParseKind.KK_ONLY: "🔎 <b>Распознано:</b> только 🇰🇿 Қазақша",
        TelegramContentParseKind.UNKNOWN: "⚠️ <b>Структура не распознана</b>",
    }
    parts = [labels[parsed.kind]]
    confidence_labels = {
        TelegramContentConfidence.HIGH: "Высокая",
        TelegramContentConfidence.MEDIUM: "Средняя",
        TelegramContentConfidence.LOW: "Низкая",
    }
    if parsed.kind != TelegramContentParseKind.UNKNOWN:
        parts.append(f"Уверенность: {confidence_labels[parsed.confidence]}")
    if parsed.title_kk or parsed.text_kk:
        kk = "\n\n".join(value for value in (parsed.title_kk, parsed.text_kk) if value)
        parts.append(f"🇰🇿\n{html_text(text_preview(kk, 300))}")
    if parsed.title_ru or parsed.text_ru:
        ru = "\n\n".join(value for value in (parsed.title_ru, parsed.text_ru) if value)
        parts.append(f"🇷🇺\n{html_text(text_preview(ru, 300))}")
    return "\n\n".join(parts)


def cb(action: str, item_id: int = 0, page: int = 0, filter_: str = "NEW", thread_id: int = 0) -> str:
    return ChannelPostCallback(action=action, item_id=item_id, page=page, status=filter_, thread_id=thread_id).pack()


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_label(thread_id: int, topic_name: str | None) -> str:
    return topic_name.strip() if topic_name and topic_name.strip() else f"📂 Тема #{thread_id}"


def mode_label(mode: TelegramProcessingMode) -> str:
    return {
        TelegramProcessingMode.MANUAL: "👤 Ручной",
        TelegramProcessingMode.AUTO_DRAFT: "📝 Авточерновик",
        TelegramProcessingMode.AUTO_PUBLISH: "🚀 Автопубликация",
    }[mode]


def edit_mode_label(mode: TelegramEditSyncMode) -> str:
    return {
        TelegramEditSyncMode.OFF: "🔒 Выключены",
        TelegramEditSyncMode.REVIEW: "📝 На проверку",
        TelegramEditSyncMode.AUTO: "🔄 Автоматически",
    }[mode]


def file_mode_label(mode: TelegramFileSyncMode) -> str:
    return {
        TelegramFileSyncMode.OFF: "🔒 Выключена",
        TelegramFileSyncMode.REVIEW: "📝 На проверку",
        TelegramFileSyncMode.AUTO: "🔄 Автоматически",
    }[mode]


async def allowed(user_id: int, session: AsyncSession, settings: Settings) -> bool:
    return await PermissionService(session, settings).has(user_id, Permission.CONTENT)


def status_value(value: str) -> TelegramContentStatus:
    try:
        return TelegramContentStatus(value)
    except ValueError:
        return TelegramContentStatus.NEW


def status_label(status: TelegramContentStatus) -> str:
    return {
        TelegramContentStatus.NEW: "🆕 Новая",
        TelegramContentStatus.DRAFT_CREATED: "✅ Обработана",
        TelegramContentStatus.IGNORED: "🚫 Игнорирована",
    }[status]


async def render_list(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    *,
    page: int = 0,
    filter_: str = "NEW",
) -> None:
    if settings.content_chat_id is None:
        if callback.message:
            await safe_edit_text(
                callback.message,
                "📥 <b>Telegram-публикации</b>\n\n⚪ Источник не настроен.\n\n"
                "Добавьте бота в официальный чат и укажите CONTENT_CHAT_ID в конфигурации.",
                reply_markup=keyboard([[InlineKeyboardButton(text="⬅️ Панель", callback_data=AdminMenuCallback(section="dashboard").pack())]]),
            )
        await callback.answer()
        return
    status = status_value(filter_)
    repo = TelegramContentRepository(session)
    total = await repo.count(settings.content_chat_id, status)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    items = await repo.listing(settings.content_chat_id, status, page * PAGE_SIZE, PAGE_SIZE)
    counts = {value: await repo.count(settings.content_chat_id, value) for value in TelegramContentStatus}
    last = await repo.last_received(settings.content_chat_id)
    rows = [
        [
            InlineKeyboardButton(text=f"🆕 {counts[TelegramContentStatus.NEW]}", callback_data=cb("list", page=0, filter_="NEW")),
            InlineKeyboardButton(text=f"✅ {counts[TelegramContentStatus.DRAFT_CREATED]}", callback_data=cb("list", page=0, filter_="DRAFT_CREATED")),
            InlineKeyboardButton(text=f"🚫 {counts[TelegramContentStatus.IGNORED]}", callback_data=cb("list", page=0, filter_="IGNORED")),
        ]
    ]
    for index, item in enumerate(items, start=page * PAGE_SIZE + 1):
        title = text_preview(suggested_title(source_text(item)), 42)
        rows.append([InlineKeyboardButton(text=f"{index}. {title}", callback_data=cb("open", item.id, page, status.value))])
    if pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅️", callback_data=cb("list", page=max(0, page - 1), filter_=status.value)),
            InlineKeyboardButton(text=f"{page + 1} / {pages}", callback_data="noop"),
            InlineKeyboardButton(text="➡️", callback_data=cb("list", page=min(pages - 1, page + 1), filter_=status.value)),
        ])
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=cb("list", page=page, filter_=status.value))],
        [InlineKeyboardButton(text="⚙️ Настройки импорта", callback_data=cb("settings"))],
        [InlineKeyboardButton(text="🧪 Проверить подключение", callback_data=cb("diagnostic"))],
        [InlineKeyboardButton(text="⬅️ Панель", callback_data=AdminMenuCallback(section="dashboard").pack())],
    ])
    body = "Выберите публикацию:" if items else "В этом разделе публикаций нет."
    text = (
        "📥 <b>Telegram-публикации</b>\n\n🟢 Источник подключён\n"
        f"💬 Тип: {html_text(last.chat_type) if last else '—'}\n"
        f"Название: {html_text(last.chat_title) if last and last.chat_title else '—'}\n\n"
        f"🆕 Новых: {counts[TelegramContentStatus.NEW]}\n"
        f"✅ Обработано: {counts[TelegramContentStatus.DRAFT_CREATED]}\n"
        f"🚫 Игнорировано: {counts[TelegramContentStatus.IGNORED]}\n\n{body}"
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(AdminMenuCallback.filter(F.section == "channel_posts"))
async def menu(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await finish_wizard(state, *DATA_KEYS)
    await render_list(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action == "list"))
async def listing(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    await render_list(callback, session, settings, page=callback_data.page, filter_=callback_data.status)


async def render_import_settings(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        await callback.answer("Источник не настроен", show_alert=True)
        return
    repo = TelegramAutoDraftRepository(session)
    config = await repo.settings(
        settings.content_chat_id,
        create=True,
        notify_admins_default=settings.content_channel_notify_admins,
    )
    mappings = await repo.mappings(settings.content_chat_id)
    assert config is not None
    text = (
        "⚙️ <b>Telegram-импорт</b>\n\nИсточник: 🟢 Подключён\n"
        f"Автоматическая обработка: {'🟢 Разрешена' if config.auto_draft_enabled else '🔴 Выключена'}\n"
        f"⏱ Ожидание материалов: {config.settle_seconds} сек\n"
        f"🔔 Уведомления: {'включены' if config.notify_admins else 'выключены'}\n"
        f"📂 Привязано Topics: {len(mappings)}"
    )
    rows = [
        [InlineKeyboardButton(text="📂 Связи Topics", callback_data=cb("maps"))],
        [InlineKeyboardButton(text=("🔴 Выключить" if config.auto_draft_enabled else "🟢 Включить") + " автоматизацию", callback_data=cb("auto_toggle"))],
        [InlineKeyboardButton(text="30 сек", callback_data=cb("settle", 30)), InlineKeyboardButton(text="60 сек", callback_data=cb("settle", 60)), InlineKeyboardButton(text="120 сек", callback_data=cb("settle", 120))],
        [InlineKeyboardButton(text="🔔 Переключить уведомления", callback_data=cb("notify_toggle"))],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("list"))],
    ]
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "settings"))
async def import_settings(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await render_import_settings(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"auto_toggle", "notify_toggle", "settle"})))
async def change_import_settings(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        return await callback.answer("Источник не настроен", show_alert=True)
    config = await TelegramAutoDraftRepository(session).settings(
        settings.content_chat_id,
        create=True,
        notify_admins_default=settings.content_channel_notify_admins,
    )
    assert config is not None
    old = {"auto_draft_enabled": config.auto_draft_enabled, "settle_seconds": config.settle_seconds, "notify_admins": config.notify_admins}
    if callback_data.action == "auto_toggle":
        config.auto_draft_enabled = not config.auto_draft_enabled
        config.auto_draft_enabled_at = datetime.now(timezone.utc) if config.auto_draft_enabled else None
    elif callback_data.action == "notify_toggle":
        config.notify_admins = not config.notify_admins
    else:
        config.settle_seconds = max(15, min(300, callback_data.item_id))
    session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_IMPORT_SETTINGS_UPDATED", entity_type="TelegramImportSettings", entity_id=str(config.id), old_data=old, new_data={"auto_draft_enabled": config.auto_draft_enabled, "settle_seconds": config.settle_seconds, "notify_admins": config.notify_admins}))
    await render_import_settings(callback, session, settings)


async def render_mappings(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        return await callback.answer("Источник не настроен", show_alert=True)
    rows_data = await TelegramAutoDraftRepository(session).mappings(settings.content_chat_id)
    lines = ["📂 <b>Связи Telegram → CMS</b>"]
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(rows_data, 1):
        label = topic_label(item.message_thread_id, item.topic_name)
        destination = "❓ FAQ" if item.content_target == TelegramContentTarget.FAQ else f"📄 {html_text(item.parent_page_title or '—')}"
        lines.append(f"\n{'🟢' if item.is_active else '🔴'} {html_text(label)}\n↳ {destination}\n⚙️ {mode_label(item.processing_mode)}")
        rows.append([InlineKeyboardButton(text=f"{text_preview(label, 28)} → {'FAQ' if item.content_target == TelegramContentTarget.FAQ else text_preview(item.parent_page_title or '—', 22)}", callback_data=cb("map_open", item.mapping_id))])
    if not rows_data:
        lines.append("\n\nСвязей пока нет.")
    rows.extend([[InlineKeyboardButton(text="➕ Добавить связь", callback_data=cb("map_add"))], [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("settings"))]])
    if callback.message:
        await safe_edit_text(callback.message, "\n".join(lines), reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "maps"))
async def mappings_list(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await render_mappings(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"edit_open", "edit_apply", "edit_reject"})))
async def edit_review_action(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    pending = await session.get(TelegramEditPending, callback_data.item_id)
    if not pending:
        return await callback.answer("Изменение не найдено", show_alert=True)
    if pending.status == "MATERIAL_REVIEW":
        file_pending = await session.scalar(
            select(TelegramAttachmentReplacement).where(
                TelegramAttachmentReplacement.source_post_id == pending.source_post_id
            )
        )
        if not file_pending:
            return await callback.answer(
                "Файловая замена для этого старого изменения не найдена. Повторно отредактируйте файл в Telegram.",
                show_alert=True,
            )
        action = {
            "edit_open": "open",
            "edit_apply": "apply",
            "edit_reject": "reject",
        }[callback_data.action]
        await _handle_file_pending(callback, session, file_pending, action, legacy_text_pending=pending)
        return
    if callback_data.action == "edit_apply":
        try:
            await TelegramEditSyncService(session, callback.from_user.id).apply_pending(pending.id)
        except ValueError as error:
            return await callback.answer(str(error), show_alert=True)
        await callback.answer("Изменение опубликовано")
        if callback.message:
            await safe_edit_text(callback.message, "✅ <b>Изменение применено и опубликовано</b>")
        return
    if callback_data.action == "edit_reject":
        await TelegramEditSyncService(session, callback.from_user.id).reject_pending(pending.id)
        await callback.answer("Изменение отклонено")
        if callback.message:
            await safe_edit_text(callback.message, "🚫 <b>Изменение отклонено</b>")
        return
    text = (
        f"📝 <b>Изменение Telegram-публикации</b>\n\nСтатус: {html_text(pending.status)}"
        f"\n\n🇷🇺 <b>{html_text(pending.title_ru or '—')}</b>\n{html_text(text_preview(pending.text_ru, 500))}"
        f"\n\n🇰🇿 <b>{html_text(pending.title_kk or '—')}</b>\n{html_text(text_preview(pending.text_kk, 500))}"
    )
    rows = [[InlineKeyboardButton(text="✅ Применить", callback_data=cb("edit_apply", pending.id))], [InlineKeyboardButton(text="🚫 Отклонить", callback_data=cb("edit_reject", pending.id))]]
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"faq_edit_open", "faq_edit_apply", "faq_edit_reject"})))
async def faq_edit_review_action(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession) -> None:
    pending = await session.get(TelegramFAQEditPending, callback_data.item_id)
    if not pending:
        return await callback.answer("Изменение FAQ не найдено", show_alert=True)
    service = TelegramFAQEditSyncService(session, callback.from_user.id)
    if callback_data.action == "faq_edit_apply":
        try:
            await service.apply_pending(pending.id)
        except ValueError as error:
            return await callback.answer(str(error), show_alert=True)
        await callback.answer("FAQ обновлён")
        if callback.message:
            await safe_edit_text(callback.message, "✅ <b>Изменение FAQ применено</b>")
        return
    if callback_data.action == "faq_edit_reject":
        try:
            await service.reject_pending(pending.id)
        except ValueError as error:
            return await callback.answer(str(error), show_alert=True)
        await callback.answer("Изменение отклонено")
        if callback.message:
            await safe_edit_text(callback.message, "🚫 <b>Изменение FAQ отклонено</b>")
        return
    faq = await session.get(FAQItem, pending.faq_id)
    if not faq:
        return await callback.answer("Связанный FAQ удалён", show_alert=True)
    old = await faq_snapshot(session, faq)
    text = (
        "📝 <b>Изменение Telegram FAQ</b>\n\n"
        f"❓ FAQ: {html_text(text_preview(old.question_ru, 120))}\n"
        f"Статус: {html_text(pending.status)}\n\n"
        f"🇷🇺 <b>Было:</b>\n{html_text(text_preview(old.answer_ru, 450))}\n\n"
        f"🇷🇺 <b>Стало:</b>\n{html_text(text_preview(pending.answer_ru, 450))}\n\n"
        f"🇰🇿 <b>Было:</b>\n{html_text(text_preview(old.answer_kk, 450))}\n\n"
        f"🇰🇿 <b>Стало:</b>\n{html_text(text_preview(pending.answer_kk, 450))}"
    )
    rows = []
    if pending.status == "REVIEW" and all((pending.question_ru, pending.answer_ru, pending.question_kk, pending.answer_kk)):
        rows.append([InlineKeyboardButton(text="✅ Применить", callback_data=cb("faq_edit_apply", pending.id))])
    if pending.status not in {"APPLIED", "REJECTED"}:
        rows.append([InlineKeyboardButton(text="🚫 Отклонить", callback_data=cb("faq_edit_reject", pending.id))])
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows) if rows else None)
    await callback.answer()


async def _render_file_pending(callback: CallbackQuery, session: AsyncSession, pending: TelegramAttachmentReplacement) -> None:
    old = await session.get(PageAttachment, pending.attachment_id) if pending.attachment_id else None
    page = await PageRepository(session).by_id(pending.page_id)
    title = page.draft_revision.title if page and page.draft_revision else (page.key if page else "—")
    change_type = "Замена файла в существующем Telegram-сообщении"
    if pending.conflict_reason == "manual new-message replacement":
        change_type = "Ручная замена файлом из нового Telegram-сообщения"
    text = (
        "📎 <b>Изменение Telegram-материала</b>\n\n"
        f"Страница: «{html_text(text_preview(title, 120))}»\n"
        f"Тип изменения: {html_text(change_type)}\n\n"
        f"Старый файл: {html_text(text_preview(old.file_name or 'без имени', 120)) if old else 'недоступен'}\n"
        f"Новый файл: {html_text(text_preview(pending.new_file_name or 'без имени', 120))}\n\n"
        f"Статус: {html_text(pending.status)}"
    )
    rows = []
    if pending.status == "REVIEW" and pending.attachment_id:
        rows.append([InlineKeyboardButton(text="✅ Применить замену", callback_data=cb("file_apply", pending.id))])
    if pending.status not in {"APPLIED", "REJECTED"}:
        rows.append([InlineKeyboardButton(text="❌ Отклонить", callback_data=cb("file_reject", pending.id))])
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows) if rows else None)
    await callback.answer()


async def _handle_file_pending(
    callback: CallbackQuery,
    session: AsyncSession,
    pending: TelegramAttachmentReplacement,
    action: str,
    *,
    legacy_text_pending: TelegramEditPending | None = None,
) -> None:
    service = TelegramAttachmentSyncService(session, callback.from_user.id)
    if action == "apply":
        try:
            await service.apply_pending(pending.id)
        except ValueError as error:
            return await callback.answer(str(error), show_alert=True)
        if legacy_text_pending:
            legacy_text_pending.status = "APPLIED"
        await callback.answer("Файл заменён и опубликован")
        if callback.message:
            await safe_edit_text(callback.message, "✅ <b>Замена применена и опубликована</b>")
        return
    if action == "reject":
        try:
            await service.reject_pending(pending.id)
        except ValueError as error:
            return await callback.answer(str(error), show_alert=True)
        if legacy_text_pending:
            legacy_text_pending.status = "REJECTED"
        await callback.answer("Замена отклонена")
        if callback.message:
            await safe_edit_text(callback.message, "🚫 <b>Замена отклонена. Вложение не изменено.</b>")
        return
    await _render_file_pending(callback, session, pending)


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"file_open", "file_apply", "file_reject"})))
async def file_review_action(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession) -> None:
    pending = await session.get(TelegramAttachmentReplacement, callback_data.item_id)
    if not pending:
        return await callback.answer("Замена не найдена", show_alert=True)
    action = {"file_open": "open", "file_apply": "apply", "file_reject": "reject"}[callback_data.action]
    await _handle_file_pending(callback, session, pending, action)


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"replace_pick", "replace_confirm"})))
async def manual_file_replacement(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession) -> None:
    if callback_data.action == "replace_pick":
        rows_data = (await session.execute(
            select(PageAttachment, BotPage, BotPageRevision)
            .join(BotPage, BotPage.published_revision_id == PageAttachment.revision_id)
            .join(BotPageRevision, BotPageRevision.id == PageAttachment.revision_id)
            .where(PageAttachment.is_active.is_(True))
            .order_by(BotPage.position, PageAttachment.position).limit(40)
        )).all()
        rows = [[InlineKeyboardButton(
            text=f"📎 {text_preview(page_revision.title, 22)} · {text_preview(attachment.file_name or 'файл', 22)}",
            callback_data=cb("replace_confirm", callback_data.item_id, thread_id=attachment.id),
        )] for attachment, _page, page_revision in rows_data]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("open", callback_data.item_id))])
        if callback.message:
            await safe_edit_text(callback.message, "📎 <b>Выберите существующий материал</b>\n\nНовый Telegram-файл заменит выбранное вложение только после отдельного подтверждения.", reply_markup=keyboard(rows))
        await callback.answer()
        return
    result = await TelegramAttachmentSyncService(session, callback.from_user.id).prepare_manual(
        callback_data.item_id, callback_data.thread_id,
    )
    if callback.message and result.pending_id:
        await safe_edit_text(callback.message, "⚠️ <b>Подтвердите замену существующего материала</b>", reply_markup=keyboard([[InlineKeyboardButton(text="✅ Применить замену", callback_data=cb("file_apply", result.pending_id))], [InlineKeyboardButton(text="❌ Отмена", callback_data=cb("open", callback_data.item_id))]]))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "map_add"))
async def mapping_add(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        return await callback.answer("Источник не настроен", show_alert=True)
    repo = TelegramAutoDraftRepository(session)
    used = {item.message_thread_id for item in await repo.mappings(settings.content_chat_id)}
    threads = [value for value in await repo.seen_threads(settings.content_chat_id) if value not in used]
    names = await repo.topic_names(settings.content_chat_id, threads)
    rows = [[InlineKeyboardButton(text=text_preview(topic_label(thread, names.get(thread)), 52), callback_data=cb("map_topic", thread))] for thread in threads[:30]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("maps"))])
    if callback.message:
        await safe_edit_text(callback.message, "📂 <b>Выберите Telegram-раздел</b>\n\nGeneral Topic без thread ID в V1 не привязывается.", reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "map_topic"))
async def mapping_select_page(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    pages = [page for page in await PageRepository(session).all() if page.is_active]
    topic_name = None
    if settings.content_chat_id is not None:
        topic_name = (await TelegramAutoDraftRepository(session).topic_names(settings.content_chat_id, [callback_data.item_id])).get(callback_data.item_id)
    rows = [[InlineKeyboardButton(text=f"📄 {text_preview(page.draft_revision.title if page.draft_revision else page.key, 40)}", callback_data=cb("map_page", page.id, thread_id=callback_data.item_id))] for page in pages[:39]]
    rows.append([InlineKeyboardButton(text="❓ Использовать Topic для FAQ", callback_data=cb("map_faq_confirm", thread_id=callback_data.item_id))])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("map_add"))])
    if callback.message:
        await safe_edit_text(callback.message, f"📄 <b>Выберите страницу CMS</b>\n\nTelegram:\n{html_text(topic_label(callback_data.item_id, topic_name))}", reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "map_page"))
async def mapping_confirm(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        return await callback.answer("Источник не настроен", show_alert=True)
    thread_id = callback_data.thread_id
    if thread_id <= 0:
        return await callback.answer("Некорректный Topic", show_alert=True)
    page = await PageRepository(session).by_id(callback_data.item_id)
    if not page or not page.is_active:
        return await callback.answer("Страница недоступна", show_alert=True)
    topic_name = (await TelegramAutoDraftRepository(session).topic_names(settings.content_chat_id, [thread_id])).get(thread_id)
    page_title = page.draft_revision.title if page.draft_revision else page.key
    text = (
        "🔗 <b>Подтверждение связи</b>\n\n"
        f"<b>Telegram:</b>\n{html_text(topic_label(thread_id, topic_name))}\n\n"
        f"<b>CMS:</b>\n📄 {html_text(page_title)}"
    )
    rows = [
        [InlineKeyboardButton(text="✅ Привязать", callback_data=cb("map_confirm", page.id, thread_id=thread_id))],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("map_topic", thread_id))],
    ]
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "map_confirm"))
async def mapping_create(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        return await callback.answer("Источник не настроен", show_alert=True)
    thread_id = callback_data.thread_id
    if thread_id <= 0:
        return await callback.answer("Некорректный Topic", show_alert=True)
    repo = TelegramAutoDraftRepository(session)
    if await repo.mapping(settings.content_chat_id, thread_id):
        return await callback.answer("Связь уже существует", show_alert=True)
    page = await PageRepository(session).by_id(callback_data.item_id)
    if not page or not page.is_active:
        return await callback.answer("Страница недоступна", show_alert=True)
    item = TelegramTopicPageMapping(
        chat_id=settings.content_chat_id, message_thread_id=thread_id, parent_page_id=page.id,
        processing_mode=TelegramProcessingMode.AUTO_DRAFT, processing_mode_enabled_at=datetime.now(timezone.utc),
        auto_draft_enabled=True, created_by=callback.from_user.id, updated_by=callback.from_user.id,
    )
    session.add(item); await session.flush()
    session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_MAPPING_CREATED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), new_data={"chat_id": settings.content_chat_id, "message_thread_id": thread_id, "parent_page_id": page.id}))
    await render_mappings(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action == "map_faq_confirm"))
async def mapping_create_faq(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None or callback_data.thread_id <= 0:
        return await callback.answer("Некорректный Topic", show_alert=True)
    repo = TelegramAutoDraftRepository(session)
    if await repo.mapping(settings.content_chat_id, callback_data.thread_id):
        return await callback.answer("Связь уже существует", show_alert=True)
    item = TelegramTopicPageMapping(
        chat_id=settings.content_chat_id, message_thread_id=callback_data.thread_id,
        parent_page_id=None, content_target=TelegramContentTarget.FAQ,
        processing_mode=TelegramProcessingMode.AUTO_DRAFT,
        processing_mode_enabled_at=datetime.now(timezone.utc), auto_draft_enabled=True,
        created_by=callback.from_user.id, updated_by=callback.from_user.id,
    )
    session.add(item)
    await session.flush()
    session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_MAPPING_CREATED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), new_data={"chat_id": item.chat_id, "message_thread_id": item.message_thread_id, "content_target": "FAQ"}))
    await render_mappings(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action == "target_page_pick"))
async def mapping_target_page_pick(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    item = await session.get(TelegramTopicPageMapping, callback_data.item_id)
    page = await PageRepository(session).by_id(callback_data.thread_id)
    if not item or item.chat_id != settings.content_chat_id or not page or not page.is_active:
        return await callback.answer("Связь или страница недоступна", show_alert=True)
    await TelegramAutoDraftRepository(session).cancel_pending_for_topic(item.chat_id, item.message_thread_id)
    old_target = item.content_target
    item.parent_page_id = page.id
    item.content_target = TelegramContentTarget.PAGE
    item.processing_mode_enabled_at = datetime.now(timezone.utc)
    item.updated_by = callback.from_user.id
    session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_TARGET_CHANGED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"target": old_target.value}, new_data={"target": "PAGE", "parent_page_id": page.id}))
    await render_mappings(callback, session, settings)


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"map_open", "map_active", "map_attach", "map_delete", "map_delete_yes", "mode_manual", "mode_draft", "mode_publish_ask", "mode_publish_yes", "edit_off", "edit_review", "edit_auto_ask", "edit_auto_yes", "file_off", "file_review", "file_auto_ask", "file_auto_yes", "target_page", "target_faq"})))
async def mapping_action(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    item = await session.get(TelegramTopicPageMapping, callback_data.item_id)
    if not item or item.chat_id != settings.content_chat_id:
        return await callback.answer("Связь не найдена", show_alert=True)
    if callback_data.action == "map_delete_yes":
        session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_MAPPING_DELETED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"message_thread_id": item.message_thread_id, "parent_page_id": item.parent_page_id}))
        await session.delete(item)
        await render_mappings(callback, session, settings)
        return
    if callback_data.action == "mode_publish_ask":
        if callback.message:
            await safe_edit_text(
                callback.message,
                "⚠️ <b>Включение автопубликации</b>\n\nНовые подходящие публикации из этой Telegram-темы будут автоматически доступны пользователям бота.\n\nАдминистратор не будет подтверждать каждую публикацию.",
                reply_markup=keyboard([[InlineKeyboardButton(text="✅ Включить", callback_data=cb("mode_publish_yes", item.id))], [InlineKeyboardButton(text="❌ Отмена", callback_data=cb("map_open", item.id))]]),
            )
        await callback.answer()
        return
    if callback_data.action == "edit_auto_ask":
        if callback.message:
            await safe_edit_text(
                callback.message,
                "⚠️ <b>Автоматическая синхронизация изменений</b>\n\nРедактирование исходного Telegram-сообщения создаст новую безопасную ревизию связанного объекта.\n\nПри обнаружении ручных CMS-изменений автоматическое применение будет заблокировано.",
                reply_markup=keyboard([[InlineKeyboardButton(text="✅ Включить", callback_data=cb("edit_auto_yes", item.id))], [InlineKeyboardButton(text="❌ Отмена", callback_data=cb("map_open", item.id))]]),
            )
        await callback.answer()
        return
    if callback_data.action == "file_auto_ask":
        if callback.message:
            await safe_edit_text(
                callback.message,
                "⚠️ <b>Автоматическая замена файлов</b>\n\nНовый file_id из edited_message будет опубликован новой ревизией только при точной source-связи и отсутствии ручных изменений CMS.",
                reply_markup=keyboard([[InlineKeyboardButton(text="✅ Включить", callback_data=cb("file_auto_yes", item.id))], [InlineKeyboardButton(text="❌ Отмена", callback_data=cb("map_open", item.id))]]),
            )
        await callback.answer()
        return
    if callback_data.action in {"mode_manual", "mode_draft", "mode_publish_yes"}:
        new_mode = {
            "mode_manual": TelegramProcessingMode.MANUAL,
            "mode_draft": TelegramProcessingMode.AUTO_DRAFT,
            "mode_publish_yes": TelegramProcessingMode.AUTO_PUBLISH,
        }[callback_data.action]
        old_mode = item.processing_mode
        if old_mode != new_mode:
            await TelegramAutoDraftRepository(session).cancel_pending_for_topic(item.chat_id, item.message_thread_id)
            item.processing_mode = new_mode
            item.processing_mode_enabled_at = datetime.now(timezone.utc)
            item.auto_draft_enabled = new_mode != TelegramProcessingMode.MANUAL
            item.updated_by = callback.from_user.id
            if old_mode == TelegramProcessingMode.AUTO_PUBLISH:
                session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_AUTO_PUBLISH_DISABLED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"mode": old_mode.value}, new_data={"mode": new_mode.value}))
            if new_mode == TelegramProcessingMode.AUTO_PUBLISH:
                session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_AUTO_PUBLISH_ENABLED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"mode": old_mode.value}, new_data={"mode": new_mode.value}))
            session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_MAPPING_UPDATED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"processing_mode": old_mode.value}, new_data={"processing_mode": new_mode.value}))
    if callback_data.action in {"target_page", "target_faq"}:
        new_target = TelegramContentTarget.PAGE if callback_data.action == "target_page" else TelegramContentTarget.FAQ
        if new_target == TelegramContentTarget.PAGE and item.parent_page_id is None:
            pages = [page for page in await PageRepository(session).all() if page.is_active]
            rows = [[InlineKeyboardButton(text=f"📄 {text_preview(page.draft_revision.title if page.draft_revision else page.key, 40)}", callback_data=cb("target_page_pick", item.id, thread_id=page.id))] for page in pages[:40]]
            rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data=cb("map_open", item.id))])
            if callback.message:
                await safe_edit_text(callback.message, "📄 <b>Выберите родительскую страницу</b>", reply_markup=keyboard(rows))
            await callback.answer()
            return
        if item.content_target != new_target:
            await TelegramAutoDraftRepository(session).cancel_pending_for_topic(item.chat_id, item.message_thread_id)
            old_target = item.content_target
            item.content_target = new_target
            item.processing_mode_enabled_at = datetime.now(timezone.utc)
            item.updated_by = callback.from_user.id
            session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_TARGET_CHANGED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={"target": old_target.value}, new_data={"target": new_target.value}))
    if callback_data.action in {"edit_off", "edit_review", "edit_auto_yes"}:
        new_edit_mode = {
            "edit_off": TelegramEditSyncMode.OFF,
            "edit_review": TelegramEditSyncMode.REVIEW,
            "edit_auto_yes": TelegramEditSyncMode.AUTO,
        }[callback_data.action]
        old_edit_mode = item.edit_sync_mode
        if old_edit_mode != new_edit_mode:
            item.edit_sync_mode = new_edit_mode
            item.updated_by = callback.from_user.id
            session.add(AuditLog(
                admin_id=callback.from_user.id, action="TELEGRAM_EDIT_SYNC_MODE_CHANGED",
                entity_type="TelegramTopicPageMapping", entity_id=str(item.id),
                old_data={"mode": old_edit_mode.value}, new_data={"mode": new_edit_mode.value},
            ))
    if callback_data.action in {"file_off", "file_review", "file_auto_yes"}:
        new_file_mode = {
            "file_off": TelegramFileSyncMode.OFF,
            "file_review": TelegramFileSyncMode.REVIEW,
            "file_auto_yes": TelegramFileSyncMode.AUTO,
        }[callback_data.action]
        old_file_mode = item.file_sync_mode
        if old_file_mode != new_file_mode:
            item.file_sync_mode = new_file_mode
            item.updated_by = callback.from_user.id
            session.add(AuditLog(
                admin_id=callback.from_user.id, action="TELEGRAM_FILE_SYNC_MODE_CHANGED",
                entity_type="TelegramTopicPageMapping", entity_id=str(item.id),
                old_data={"mode": old_file_mode.value}, new_data={"mode": new_file_mode.value},
            ))
    if callback_data.action in {"map_active", "map_attach"}:
        field = {"map_active": "is_active", "map_attach": "auto_attach_materials"}[callback_data.action]
        old = getattr(item, field); setattr(item, field, not old); item.updated_by = callback.from_user.id
        session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_TOPIC_MAPPING_UPDATED", entity_type="TelegramTopicPageMapping", entity_id=str(item.id), old_data={field: old}, new_data={field: getattr(item, field)}))
    page = await PageRepository(session).by_id(item.parent_page_id) if item.parent_page_id else None
    title = page.draft_revision.title if page and page.draft_revision else "—"
    import_config = await TelegramAutoDraftRepository(session).settings(item.chat_id)
    settle_seconds = import_config.settle_seconds if import_config else 60
    topic_name = (await TelegramAutoDraftRepository(session).topic_names(item.chat_id, [item.message_thread_id])).get(item.message_thread_id)
    label = topic_label(item.message_thread_id, topic_name)
    if callback_data.action == "map_delete":
        text = f"⚠️ <b>Удалить связь «{html_text(label)}»?</b>\n\nCMS-страницы и уже созданные черновики удалены не будут."
        rows = [[InlineKeyboardButton(text="🔴 Да, удалить", callback_data=cb("map_delete_yes", item.id))], [InlineKeyboardButton(text="⬅️ Отмена", callback_data=cb("map_open", item.id))]]
    else:
        target_label = "❓ FAQ" if item.content_target == TelegramContentTarget.FAQ else f"📄 Страница: {html_text(title)}"
        text = f"📂 <b>{html_text(label)}</b>\n<code>thread #{item.message_thread_id}</code>\n\n<b>Тип контента:</b>\n{target_label}\n\nСвязь: {'🟢 активна' if item.is_active else '🔴 выключена'}\n\n<b>Обработка:</b>\n{mode_label(item.processing_mode)}\n\n<b>Изменения текста:</b>\n{edit_mode_label(item.edit_sync_mode)}\n\n<b>Замена файлов:</b>\n{file_mode_label(item.file_sync_mode)}\n\n📎 Материалы: {'автоматически' if item.auto_attach_materials else 'не добавлять'}\n⏱ Ожидание: {settle_seconds} секунд"
        rows = [[InlineKeyboardButton(text="📄 Страницы", callback_data=cb("target_page", item.id)), InlineKeyboardButton(text="❓ FAQ", callback_data=cb("target_faq", item.id))], [InlineKeyboardButton(text="👤 Ручной", callback_data=cb("mode_manual", item.id))], [InlineKeyboardButton(text="📝 Авточерновик", callback_data=cb("mode_draft", item.id))], [InlineKeyboardButton(text="🚀 Автопубликация", callback_data=cb("mode_publish_ask", item.id))], [InlineKeyboardButton(text="🔒 Текст: выключить", callback_data=cb("edit_off", item.id))], [InlineKeyboardButton(text="📝 Текст: на проверку", callback_data=cb("edit_review", item.id))], [InlineKeyboardButton(text="🔄 Текст: автоматически", callback_data=cb("edit_auto_ask", item.id))], [InlineKeyboardButton(text="🔒 Файлы: выключить", callback_data=cb("file_off", item.id))], [InlineKeyboardButton(text="📝 Файлы: на проверку", callback_data=cb("file_review", item.id))], [InlineKeyboardButton(text="🔄 Файлы: автоматически", callback_data=cb("file_auto_ask", item.id))], [InlineKeyboardButton(text="Переключить материалы", callback_data=cb("map_attach", item.id))], [InlineKeyboardButton(text="Включить/выключить связь", callback_data=cb("map_active", item.id))], [InlineKeyboardButton(text="🗑 Удалить связь", callback_data=cb("map_delete", item.id))], [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("maps"))]]
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard(rows))
    await callback.answer()


def post_card(
    post: TelegramContentPost,
    page: int,
    filter_: str,
    grouping: PublicationGroupingResult | None = None,
    faq_parsed=None,
) -> tuple[str, InlineKeyboardMarkup]:
    value = source_text(post)
    parsed = parse_post(post)
    when = post.telegram_created_at or post.created_at
    local = when.astimezone(ZoneInfo("Asia/Qyzylorda")) if when else None
    oversized = sum(1 for item in post.media if item.type == MediaType.DOCUMENT and item.file_size and item.file_size > MAX_TRANSFER_DOCUMENT_SIZE)
    warning = f"\n⚠️ Слишком больших документов: {oversized}" if oversized else ""
    auto_error = f"\n⚠️ Автоматическая обработка: {html_text(post.auto_last_error)}" if post.auto_last_error else ""
    grouping_summary = ""
    if grouping and grouping.materials:
        material_lines = [
            f"{index}. {_language_icon(candidate.suggested_language.value if candidate.suggested_language else None)} "
            f"{html_text(text_preview(candidate.display_name, 55))}"
            for index, candidate in enumerate(grouping.materials[:3], start=1)
        ]
        grouping_summary = (
            f"💡 Найдены связанные материалы: {len(grouping.materials)}\n"
            + "\n".join(material_lines)
            + "\n"
        )
    parsed_block = parsed_preview(parsed)
    heading = "📥 <b>Telegram-публикация</b>"
    if faq_parsed is not None:
        heading = "❓ <b>Telegram FAQ</b>"
        confidence = "высокая" if faq_parsed.confidence == TelegramContentConfidence.HIGH else "низкая"
        parsed_block = (
            f"🔎 {'RU + KK распознаны' if faq_parsed.is_complete else html_text(faq_parsed.diagnostic)}\n"
            f"Уверенность: {confidence}\n\n"
            f"🇰🇿 <b>Сұрақ:</b>\n{html_text(faq_parsed.question_kk or '—')}\n\n"
            f"🇰🇿 <b>Жауап:</b>\n{html_text(text_preview(faq_parsed.answer_kk, 300))}\n\n"
            f"🇷🇺 <b>Вопрос:</b>\n{html_text(faq_parsed.question_ru or '—')}\n\n"
            f"🇷🇺 <b>Ответ:</b>\n{html_text(text_preview(faq_parsed.answer_ru, 300))}"
        )
    text = (
        heading + "\n\n"
        f"📅 {local.strftime('%d.%m.%Y %H:%M') if local else '—'}\n\n"
        + (f"🧵 Topic/thread: {post.message_thread_id}\n\n" if post.message_thread_id is not None else "")
        +
        f"{parsed_block}\n\n"
        f"📝 <b>Исходный текст:</b>\n{html_text(text_preview(value, 450)) if value else '—'}\n\n"
        f"📎 В самой публикации: {len(post.media)}{warning}\n"
        + grouping_summary
        + "\n"
        f"Статус: {status_label(post.status)}{auto_error}"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if post.status == TelegramContentStatus.NEW:
        if grouping and grouping.materials:
            rows.append([InlineKeyboardButton(text="📎 Настроить материалы", callback_data=cb("materials", post.id, page, filter_))])
        if post.media:
            rows.append([InlineKeyboardButton(text="🔁 Заменить существующий материал", callback_data=cb("replace_pick", post.id, page, filter_))])
        rows.extend([
            [InlineKeyboardButton(text="📄 Создать страницу", callback_data=cb("manual_page", post.id, page, filter_))],
            [InlineKeyboardButton(text="❓ Создать FAQ", callback_data=cb("manual_faq", post.id, page, filter_))],
            [InlineKeyboardButton(text="✨ Подготовить через ИИ", callback_data=cb("ai", post.id, page, filter_))],
            [InlineKeyboardButton(text="🚫 Игнорировать", callback_data=cb("ignore_ask", post.id, page, filter_))],
        ])
    elif post.status == TelegramContentStatus.IGNORED:
        rows.append([InlineKeyboardButton(text="↩️ Вернуть в новые", callback_data=cb("restore", post.id, page, filter_))])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("list", page=page, filter_=filter_))])
    return text, keyboard(rows)


async def load_post(item_id: int, session: AsyncSession, settings: Settings, *, lock: bool = False) -> TelegramContentPost | None:
    if settings.content_chat_id is None:
        return None
    return await TelegramContentRepository(session).by_id(item_id, settings.content_chat_id, lock=lock)


async def group_post(post: TelegramContentPost, session: AsyncSession) -> PublicationGroupingResult:
    return await TelegramPublicationGrouper(TelegramContentRepository(session), CONTENT_PARSER).group(post)


@router.callback_query(ChannelPostCallback.filter(F.action == "open"))
async def open_post(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post:
        await callback.answer("Публикация не найдена", show_alert=True)
        return
    grouping = await group_post(post, session)
    mapping = await TelegramAutoDraftRepository(session).mapping(post.chat_id, post.message_thread_id) if post.message_thread_id is not None else None
    faq_parsed = FAQ_PARSER.parse(source_text(post)) if mapping and mapping.content_target == TelegramContentTarget.FAQ else None
    text, markup = post_card(post, callback_data.page, callback_data.status, grouping, faq_parsed)
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=markup)
    await callback.answer()


def _language_icon(value: str | None) -> str:
    return {LanguageCode.RU.value: "🇷🇺", LanguageCode.KK.value: "🇰🇿"}.get(value, "🌐")


def _merge_material_state(
    grouping: PublicationGroupingResult,
    current: list[dict] | None = None,
) -> list[dict]:
    current_by_id = {int(item["content_post_id"]): item for item in (current or [])}
    result = []
    for candidate in grouping.materials:
        saved = current_by_id.get(candidate.content_post_id)
        language = LanguageCode(saved["selected_language"]) if saved and saved.get("selected_language") else None
        result.append(candidate.to_state(selected=bool(saved.get("selected", True)) if saved else True, language=language))
    return result


async def render_material_selection(
    callback: CallbackQuery,
    grouping: PublicationGroupingResult,
    materials: list[dict],
    page: int,
    filter_: str,
    source_title: str,
) -> None:
    candidates = {candidate.content_post_id: candidate for candidate in grouping.materials}
    lines = [
        "📎 <b>Связанные материалы</b>",
        "",
        f"<b>Публикация:</b>\n{html_text(text_preview(source_title, 120))}",
        "",
        "Найдены:",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(materials, start=1):
        candidate = candidates[int(item["content_post_id"])]
        check = "☑️" if item["selected"] else "⬜"
        language = _language_icon(item.get("selected_language"))
        name = text_preview(candidate.display_name, 55)
        lines.append(f"{check} {index}. {language} {html_text(name)}")
        rows.append([InlineKeyboardButton(
            text=f"{check} {index}. {name}",
            callback_data=cb("m_toggle", candidate.content_post_id, page, filter_),
        )])
        rows.append([
            InlineKeyboardButton(text="🇷🇺 RU", callback_data=cb("m_ru", candidate.content_post_id, page, filter_)),
            InlineKeyboardButton(text="🇰🇿 KK", callback_data=cb("m_kk", candidate.content_post_id, page, filter_)),
            InlineKeyboardButton(text="🌐 —", callback_data=cb("m_none", candidate.content_post_id, page, filter_)),
        ])
    rows.extend([
        [InlineKeyboardButton(text="✅ Использовать выбранные", callback_data=cb("m_use", grouping.source_post_id, page, filter_))],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("m_back", grouping.source_post_id, page, filter_))],
    ])
    if callback.message:
        await safe_edit_text(callback.message, "\n".join(lines), reply_markup=keyboard(rows))


@router.callback_query(ChannelPostCallback.filter(F.action == "materials"))
async def materials_open(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post or post.status != TelegramContentStatus.NEW:
        await callback.answer("Публикация уже обработана", show_alert=True)
        return
    grouping = await group_post(post, session)
    if not grouping.materials:
        await callback.answer("Связанные материалы не найдены", show_alert=True)
        return
    data = await state.get_data()
    current = data.get("channel_materials") if data.get("channel_material_source_id") == post.id else None
    materials = _merge_material_state(grouping, current)
    await state.update_data(
        channel_material_source_id=post.id,
        channel_materials=materials,
        channel_page=callback_data.page,
        channel_filter=callback_data.status,
    )
    await render_material_selection(callback, grouping, materials, callback_data.page, callback_data.status, suggested_title(source_text(post)))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"m_toggle", "m_ru", "m_kk", "m_none"})))
async def material_change(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    source_id = int(data.get("channel_material_source_id", 0))
    post = await load_post(source_id, session, settings)
    if not post or post.status != TelegramContentStatus.NEW:
        await callback.answer("Публикация уже обработана", show_alert=True)
        return
    grouping = await group_post(post, session)
    valid_ids = {candidate.content_post_id for candidate in grouping.materials}
    if callback_data.item_id not in valid_ids:
        await callback.answer("Материал больше недоступен", show_alert=True)
        return
    materials = _merge_material_state(grouping, data.get("channel_materials"))
    for item in materials:
        if int(item["content_post_id"]) != callback_data.item_id:
            continue
        if callback_data.action == "m_toggle":
            item["selected"] = not item["selected"]
        else:
            item["selected_language"] = {
                "m_ru": LanguageCode.RU.value,
                "m_kk": LanguageCode.KK.value,
                "m_none": None,
            }[callback_data.action]
    await state.update_data(channel_materials=materials)
    await render_material_selection(callback, grouping, materials, callback_data.page, callback_data.status, suggested_title(source_text(post)))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "m_back"))
async def materials_back(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post:
        await callback.answer("Публикация не найдена", show_alert=True)
        return
    grouping = await group_post(post, session)
    text, markup = post_card(post, callback_data.page, callback_data.status, grouping)
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "m_use"))
async def materials_use(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await prepare_manual(callback, callback_data, session, settings, state, "PAGE")


@router.callback_query(ChannelPostCallback.filter(F.action == "diagnostic"))
async def diagnostic(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if settings.content_chat_id is None:
        await callback.answer("Канал не настроен", show_alert=True)
        return
    repo = TelegramContentRepository(session)
    last = await repo.last_received(settings.content_chat_id)
    count = await repo.count(settings.content_chat_id, TelegramContentStatus.NEW)
    text = (
        "🧪 <b>Проверка подключения</b>\n\nConfigured channel ID: да\n"
        f"Тип последнего source: {html_text(last.chat_type) if last else '—'}\n"
        f"Последняя публикация: {last.created_at.astimezone(ZoneInfo('Asia/Qyzylorda')).strftime('%d.%m.%Y %H:%M') if last else 'нет'}\n"
        f"Новых: {count}\n\nИстория канала через Bot API не запрашивается."
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=keyboard([[InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("list"))]]))
    await callback.answer()


async def show_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data["channel_type"]
    media_count = int(data.get("channel_media_count", 0))
    if kind == "PAGE":
        body = (
            "📄 <b>Черновик страницы</b>\n\n"
            f"🇷🇺 <b>Название:</b>\n{html_text(data.get('channel_title_ru') or '—')}\n\n"
            f"<b>Текст:</b>\n{html_text(text_preview(data.get('channel_text_ru'), 700))}\n\n"
            f"🇰🇿 <b>Название:</b>\n{html_text(data.get('channel_title_kk') or '—')}\n\n"
            f"<b>Текст:</b>\n{html_text(text_preview(data.get('channel_text_kk'), 500))}\n\n📎 Вложений: {media_count}"
        )
        fields = [("Название RU", "title_ru"), ("Текст RU", "text_ru"), ("Название KK", "title_kk"), ("Текст KK", "text_kk")]
    else:
        body = (
            "❓ <b>Черновик FAQ</b>\n\n"
            f"🇷🇺 <b>Вопрос:</b>\n{html_text(data.get('channel_question_ru') or '—')}\n\n"
            f"<b>Ответ:</b>\n{html_text(text_preview(data.get('channel_answer_ru'), 700))}\n\n"
            f"🇰🇿 <b>Сұрақ:</b>\n{html_text(data.get('channel_question_kk') or '—')}\n\n"
            f"<b>Жауап:</b>\n{html_text(text_preview(data.get('channel_answer_kk'), 500))}"
            + (f"\n\n⚠️ {media_count} вложений не будут добавлены в FAQ." if media_count else "")
        )
        fields = [("Вопрос RU", "question_ru"), ("Ответ RU", "answer_ru"), ("Сұрақ KK", "question_kk"), ("Жауап KK", "answer_kk")]
    reason = data.get("channel_reason")
    if reason:
        body = f"🤖 <b>Предложение ИИ</b>\n\nРекомендуемый тип: {kind}\nПричина: {html_text(reason)}\n\n" + body
    if data.get("channel_input_truncated"):
        body += "\n\n⚠️ Исходный текст был сокращён до 12 000 символов для AI-запроса. Проверьте результат."
    rows = [[InlineKeyboardButton(text=f"✏️ {label}", callback_data=cb(f"edit_{field}", data["channel_post_id"]))] for label, field in fields]
    rows.extend([
        [InlineKeyboardButton(text="✅ Создать черновик", callback_data=cb("create", data["channel_post_id"]))],
        *([[InlineKeyboardButton(text="🔄 Создать заново", callback_data=cb("regen_ask", data["channel_post_id"]))]] if reason else []),
        [InlineKeyboardButton(text="🚫 Игнорировать", callback_data=cb("ignore_ask", data["channel_post_id"]))],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("cancel", data["channel_post_id"]))],
    ])
    await state.set_state(ModerateChannelPost.preview)
    if callback.message:
        await safe_edit_text(callback.message, body, reply_markup=keyboard(rows))


async def prepare_manual(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext, kind: str) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post or post.status != TelegramContentStatus.NEW:
        await callback.answer("Публикация уже обработана", show_alert=True)
        return
    value = source_text(post)
    parsed = parse_post(post)
    title = suggested_title(value)
    grouping = await group_post(post, session) if kind == "PAGE" else PublicationGroupingResult(post.id, ())
    state_data = await state.get_data()
    existing_materials = state_data.get("channel_materials", []) if state_data.get("channel_material_source_id") == post.id else []
    existing_by_id = {int(item["content_post_id"]): item for item in existing_materials}
    material_state = []
    for candidate in grouping.materials:
        current = existing_by_id.get(candidate.content_post_id)
        material_state.append(
            candidate.to_state(
                selected=bool(current.get("selected", True)) if current else True,
                language=LanguageCode(current["selected_language"]) if current and current.get("selected_language") else None,
            )
        )
    selected_file_count = sum(len(item["files"]) for item in material_state if item["selected"])
    await remember_wizard(state, callback.message)
    common = {
        "channel_post_id": post.id,
        "channel_page": callback_data.page,
        "channel_filter": callback_data.status,
        "channel_type": kind,
        "channel_media_count": len(post.media) + selected_file_count,
        "channel_reason": None,
        "channel_input_truncated": False,
        "channel_material_source_id": post.id,
        "channel_materials": material_state,
    }
    if kind == "PAGE":
        if parsed.kind == TelegramContentParseKind.UNKNOWN:
            common.update(channel_title_ru=title, channel_text_ru=value, channel_title_kk=None, channel_text_kk=None)
        else:
            common.update(
                channel_title_ru=parsed.title_ru,
                channel_text_ru=parsed.text_ru,
                channel_title_kk=parsed.title_kk,
                channel_text_kk=parsed.text_kk,
            )
    else:
        faq = FAQ_PARSER.parse(value)
        common.update(
            channel_question_ru=faq.question_ru or "",
            channel_answer_ru=faq.answer_ru or "",
            channel_question_kk=faq.question_kk or "",
            channel_answer_kk=faq.answer_kk or "",
        )
    await state.update_data(**common)
    await show_draft(callback, state)
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "manual_page"))
async def manual_page(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await prepare_manual(callback, callback_data, session, settings, state, "PAGE")


@router.callback_query(ChannelPostCallback.filter(F.action == "manual_faq"))
async def manual_faq(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await prepare_manual(callback, callback_data, session, settings, state, "FAQ")


@router.callback_query(ChannelPostCallback.filter(F.action.startswith("edit_")))
async def edit_field(callback: CallbackQuery, callback_data: ChannelPostCallback, state: FSMContext) -> None:
    field = callback_data.action.removeprefix("edit_")
    allowed_fields = {"title_ru", "text_ru", "title_kk", "text_kk", "question_ru", "answer_ru", "question_kk", "answer_kk"}
    if field not in allowed_fields:
        await callback.answer("Поле недоступно", show_alert=True)
        return
    await state.update_data(channel_edit_field=field)
    await state.set_state(ModerateChannelPost.edit)
    if callback.message:
        await safe_edit_text(
            callback.message,
            f"✏️ <b>Редактирование</b>\n\nТекущее значение:\n{html_text(text_preview((await state.get_data()).get('channel_' + field), 500))}\n\nОтправьте новое значение.",
            reply_markup=keyboard([[InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("preview", callback_data.item_id))], [InlineKeyboardButton(text="❌ Отмена", callback_data=cb("cancel", callback_data.item_id))]]),
        )
    await callback.answer()


@router.message(ModerateChannelPost.edit, F.text)
async def receive_field(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not message.from_user or not await allowed(message.from_user.id, session, settings):
        return
    data = await state.get_data()
    field = data.get("channel_edit_field")
    value = (message.text or "").strip()
    if not field or not value:
        return
    limits = {"title_ru": 255, "title_kk": 255, "question_ru": 500, "question_kk": 500, "answer_ru": 10_000, "answer_kk": 10_000, "text_ru": 20_000, "text_kk": 20_000}
    if len(value) > limits[field]:
        await message.answer(f"Текст слишком длинный. Максимум: {limits[field]} символов.")
        return
    await state.update_data(**{"channel_" + field: value})
    await consume_input(message)
    await state.set_state(ModerateChannelPost.preview)
    data = await state.get_data()
    kind = data["channel_type"]
    await _render_draft_to_wizard(message.bot, state, kind)


async def _render_draft_to_wizard(bot, state: FSMContext, kind: str) -> None:
    data = await state.get_data()
    if kind == "PAGE":
        body = f"📄 <b>Черновик страницы</b>\n\n🇷🇺 <b>Название:</b>\n{html_text(data.get('channel_title_ru'))}\n\n<b>Текст:</b>\n{html_text(text_preview(data.get('channel_text_ru'),700))}\n\n🇰🇿 <b>Название:</b>\n{html_text(data.get('channel_title_kk') or '—')}\n\n<b>Текст:</b>\n{html_text(text_preview(data.get('channel_text_kk'),500))}\n\n📎 Вложений: {data.get('channel_media_count',0)}"
        fields=[("Название RU","title_ru"),("Текст RU","text_ru"),("Название KK","title_kk"),("Текст KK","text_kk")]
    else:
        body=f"❓ <b>Черновик FAQ</b>\n\n🇷🇺 <b>Вопрос:</b>\n{html_text(data.get('channel_question_ru'))}\n\n<b>Ответ:</b>\n{html_text(text_preview(data.get('channel_answer_ru'),700))}\n\n🇰🇿 <b>Сұрақ:</b>\n{html_text(data.get('channel_question_kk') or '—')}\n\n<b>Жауап:</b>\n{html_text(text_preview(data.get('channel_answer_kk'),500))}"
        fields=[("Вопрос RU","question_ru"),("Ответ RU","answer_ru"),("Сұрақ KK","question_kk"),("Жауап KK","answer_kk")]
    if data.get("channel_reason"):body=f"🤖 <b>Предложение ИИ</b>\n\nПричина: {html_text(data['channel_reason'])}\n\n"+body
    if data.get("channel_input_truncated"):body+="\n\n⚠️ Исходный текст был сокращён для AI-запроса."
    rows=[[InlineKeyboardButton(text=f"✏️ {label}",callback_data=cb(f"edit_{field}",data["channel_post_id"]))] for label,field in fields]
    rows.extend([
        [InlineKeyboardButton(text="✅ Создать черновик",callback_data=cb("create",data["channel_post_id"]))],
        *([[InlineKeyboardButton(text="🔄 Создать заново",callback_data=cb("regen_ask",data["channel_post_id"]))]] if data.get("channel_reason") else []),
        [InlineKeyboardButton(text="🚫 Игнорировать",callback_data=cb("ignore_ask",data["channel_post_id"]))],
        [InlineKeyboardButton(text="⬅️ Назад",callback_data=cb("cancel",data["channel_post_id"]))],
    ])
    from app.utils.wizard import edit_wizard
    await edit_wizard(bot,state,body,keyboard(rows))


@router.callback_query(ChannelPostCallback.filter(F.action == "preview"))
async def back_preview(callback: CallbackQuery, state: FSMContext) -> None:
    await show_draft(callback, state)
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"ai", "regen"})))
async def ai_prepare(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext, ai_service: AIService | None) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post or post.status != TelegramContentStatus.NEW:
        await callback.answer("Публикация уже обработана", show_alert=True)
        return
    if not ai_service or not settings.ai_available:
        await callback.answer("Gemini недоступен", show_alert=True)
        return
    value = source_text(post)
    if not value:
        await callback.answer("В публикации нет текста для ИИ", show_alert=True)
        return
    await remember_wizard(state, callback.message)
    media_summary = ", ".join(f"{item.type.value}: {item.file_name or 'без имени'}" for item in post.media)
    user = await upsert_user(session, callback.from_user)
    await session.commit()
    if callback.message:
        await safe_edit_text(callback.message, "✨ <b>Подготавливаю предложение…</b>")
    try:
        result = await ai_service.prepare_channel_content(session, user_id=user.id, source=value, media_summary=media_summary)
    except Exception as error:
        logger.warning("Channel AI preparation failed post_id=%s error=%s", post.id, type(error).__name__)
        if callback.message:
            await safe_edit_text(callback.message, "⚠️ <b>Не удалось подготовить публикацию.</b>", reply_markup=keyboard([[InlineKeyboardButton(text="🔄 Попробовать", callback_data=cb("ai", post.id))], [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("open", post.id))]]))
        await callback.answer()
        return
    base = {"channel_post_id": post.id, "channel_page": callback_data.page, "channel_filter": callback_data.status, "channel_media_count": len(post.media), "channel_reason": result.reason_short, "channel_input_truncated": len(value) > 12_000}
    if result.recommended_type == "IGNORE":
        await state.update_data(**base, channel_type="IGNORE")
        if callback.message:
            await safe_edit_text(callback.message, f"🤖 <b>ИИ считает, что публикация не подходит для Page/FAQ.</b>\n\nПричина:\n{html_text(result.reason_short)}", reply_markup=keyboard([[InlineKeyboardButton(text="📄 Всё равно создать страницу", callback_data=cb("manual_page", post.id))], [InlineKeyboardButton(text="❓ Создать FAQ", callback_data=cb("manual_faq", post.id))], [InlineKeyboardButton(text="🚫 Игнорировать", callback_data=cb("ignore_ask", post.id))], [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("open", post.id))]]))
        await callback.answer()
        return
    if result.recommended_type == "PAGE":
        if not result.title_ru or not result.text_ru:
            await callback.answer("Gemini вернул неполную страницу", show_alert=True); return
        await state.update_data(**base, channel_type="PAGE", channel_title_ru=result.title_ru, channel_text_ru=result.text_ru, channel_title_kk=result.title_kk, channel_text_kk=result.text_kk)
    else:
        if not result.question_ru or not result.answer_ru:
            await callback.answer("Gemini вернул неполный FAQ", show_alert=True); return
        await state.update_data(**base, channel_type="FAQ", channel_question_ru=result.question_ru, channel_answer_ru=result.answer_ru, channel_question_kk=result.question_kk, channel_answer_kk=result.answer_kk)
    await show_draft(callback, state)
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "regen_ask"))
async def regenerate_ask(callback: CallbackQuery, callback_data: ChannelPostCallback) -> None:
    if callback.message:
        await safe_edit_text(callback.message, "⚠️ Будет выполнен новый AI-запрос.", reply_markup=keyboard([[InlineKeyboardButton(text="🔄 Выполнить", callback_data=cb("regen", callback_data.item_id))], [InlineKeyboardButton(text="⬅️ Отмена", callback_data=cb("preview", callback_data.item_id))]]))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action == "create"))
async def create_draft(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    item_id = int(data.get("channel_post_id", 0))
    if item_id != callback_data.item_id:
        await callback.answer("Черновик устарел", show_alert=True); return
    if settings.content_chat_id is None:
        await callback.answer("Канал не настроен", show_alert=True); return
    service = TelegramContentImportService(session)
    kind = data.get("channel_type")
    if kind == "PAGE" and (not str(data.get("channel_title_ru", "")).strip() or not str(data.get("channel_text_ru", "")).strip()):
        await callback.answer("Заполните название и текст RU", show_alert=True); return
    if kind == "FAQ" and (not str(data.get("channel_question_ru", "")).strip() or not str(data.get("channel_answer_ru", "")).strip()):
        await callback.answer("Заполните вопрос и ответ RU", show_alert=True); return
    try:
        if kind == "PAGE":
            selected_materials = tuple(
                SelectedPublicationMaterial(
                    content_post_id=int(item["content_post_id"]),
                    selected_language=LanguageCode(item["selected_language"]) if item.get("selected_language") else None,
                )
                for item in data.get("channel_materials", [])
                if item.get("selected")
            )
            await service.create_page(item_id, settings.content_chat_id, callback.from_user.id, title_ru=data["channel_title_ru"], text_ru=data["channel_text_ru"], title_kk=data.get("channel_title_kk"), text_kk=data.get("channel_text_kk"), materials=selected_materials)
        else:
            faq = await service.create_faq(item_id, settings.content_chat_id, callback.from_user.id, question_ru=data["channel_question_ru"], answer_ru=data["channel_answer_ru"], question_kk=data.get("channel_question_kk"), answer_kk=data.get("channel_answer_kk"))
            session.add(AuditLog(admin_id=callback.from_user.id, action="TELEGRAM_FAQ_MANUALLY_CREATED", entity_type="FAQItem", entity_id=str(faq.id), new_data={"source_post_id": item_id}))
            await queue_faq_knowledge_sync(session, faq.id, callback.from_user.id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True); return
    await finish_wizard(state, *DATA_KEYS, "channel_media_count")
    if callback.message:
        await safe_edit_text(callback.message, "✅ Черновик создан. Публикация отмечена как обработанная.", reply_markup=keyboard([[InlineKeyboardButton(text="⬅️ К публикациям", callback_data=cb("list"))]]))
    await callback.answer()


@router.callback_query(ChannelPostCallback.filter(F.action.in_({"ignore_ask", "ignore_yes", "restore"})))
async def status_action(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post:
        await callback.answer("Публикация не найдена", show_alert=True); return
    if callback_data.action == "ignore_ask":
        if post.status != TelegramContentStatus.NEW:
            await callback.answer("Публикация уже обработана", show_alert=True); return
        if callback.message:
            await safe_edit_text(callback.message, "⚠️ Публикация будет исключена из очереди новых.", reply_markup=keyboard([[InlineKeyboardButton(text="🚫 Игнорировать", callback_data=cb("ignore_yes", post.id, callback_data.page, callback_data.status))], [InlineKeyboardButton(text="⬅️ Отмена", callback_data=cb("open", post.id, callback_data.page, callback_data.status))]]))
        await callback.answer(); return
    service = TelegramContentImportService(session)
    try:
        if callback_data.action == "restore": await service.restore(post.id, settings.content_chat_id, callback.from_user.id)  # type: ignore[arg-type]
        else: await service.ignore(post.id, settings.content_chat_id, callback.from_user.id)  # type: ignore[arg-type]
    except ValueError as error:
        await callback.answer(str(error), show_alert=True); return
    await finish_wizard(state, *DATA_KEYS, "channel_media_count")
    await render_list(callback, session, settings, page=callback_data.page, filter_="NEW" if callback_data.action == "restore" else callback_data.status)


@router.callback_query(ChannelPostCallback.filter(F.action == "cancel"))
async def cancel(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    page, filter_ = int(data.get("channel_page", 0)), str(data.get("channel_filter", "NEW"))
    await finish_wizard(state, *DATA_KEYS, "channel_media_count")
    post = await load_post(callback_data.item_id, session, settings)
    if post and callback.message:
        grouping = await group_post(post, session)
        text, markup = post_card(post, page, filter_, grouping)
        await safe_edit_text(callback.message, text, reply_markup=markup)
    await callback.answer()
