from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import UserAttachmentCallback
from app.config import Settings
from app.database.models.enums import MediaType
from app.database.repositories.pages import PageRepository
from app.database.repositories.users import upsert_user
from app.handlers.user.pages import send_page
from app.utils.i18n import attachment_caption, normalize_language, t, translated
from app.utils.pagination import PageSlice, paginate
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text

router = Router(name="user_attachments")


def materials_keyboard(items: list, language: str, page_id: int, page: int) -> tuple[InlineKeyboardMarkup, PageSlice]:
    result = paginate(items, page)
    rows = []
    for index, item in enumerate(result.items, start=result.page * 8 + 1):
        fallback = t("material_photo", language) if item.type == MediaType.PHOTO else t("material_document", language)
        label = text_preview(item.file_name or fallback, 48)
        icon = "🖼" if item.type == MediaType.PHOTO else "📄"
        rows.append([InlineKeyboardButton(text=f"{index}. {icon} {label}", callback_data=UserAttachmentCallback(action="open", page_id=page_id, item_id=item.id, page=result.page).pack())])
    if result.pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅️", callback_data=UserAttachmentCallback(action="list", page_id=page_id, page=max(0, result.page - 1)).pack()),
            InlineKeyboardButton(text=f"{result.page + 1} / {result.pages}", callback_data="noop"),
            InlineKeyboardButton(text="➡️", callback_data=UserAttachmentCallback(action="list", page_id=page_id, page=min(result.pages - 1, result.page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(text=t("back_to_page", language), callback_data=UserAttachmentCallback(action="back", page_id=page_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows), result


async def current_page_materials(session: AsyncSession, page_id: int):
    repo = PageRepository(session)
    page = await repo.by_id(page_id)
    if not page or not page.is_active or not page.published_revision_id or not page.published_revision:
        return page, []
    items = await repo.attachments(page.published_revision_id)
    return page, [item for item in items if item.is_active]


@router.callback_query(UserAttachmentCallback.filter(F.action == "list"))
async def show_materials(callback: CallbackQuery, callback_data: UserAttachmentCallback, session: AsyncSession) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)
    page, items = await current_page_materials(session, callback_data.page_id)
    if not page or not page.is_active or not page.published_revision:
        await callback.answer(t("unavailable", language), show_alert=True)
        return
    translation = translated(page.published_revision.translations, language)
    title = translation.title if translation else page.published_revision.title
    keyboard, _ = materials_keyboard(items, language, page.id, callback_data.page)
    body = (f"📎 <b>{t('materials', language)}</b>\n\n<b>{html_text(title)}</b>\n\n{t('materials_total', language)}: <b>{len(items)}</b>\n\n{t('materials_choose', language)}:" if items else f"📎 <b>{t('materials', language)}</b>\n\n{t('materials_empty', language)}")
    if callback.message:
        await safe_edit_text(callback.message, body, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(UserAttachmentCallback.filter(F.action == "open"))
async def open_material(callback: CallbackQuery, callback_data: UserAttachmentCallback, session: AsyncSession) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)
    repo = PageRepository(session)
    page = await repo.by_id(callback_data.page_id)
    if not page or not page.is_active or not page.published_revision_id:
        return await callback.answer(t("unavailable", language), show_alert=True)
    item = await repo.attachment(page.published_revision_id, callback_data.item_id)
    if not item or not item.is_active or item.revision_id != page.published_revision_id or not callback.message:
        return await callback.answer(t("unavailable", language), show_alert=True)
    caption = attachment_caption(item, language)
    if item.type == MediaType.PHOTO:
        await callback.message.answer_photo(item.telegram_file_id, caption=caption)
    else:
        await callback.message.answer_document(item.telegram_file_id, caption=caption)
    await callback.answer()


@router.callback_query(UserAttachmentCallback.filter(F.action == "back"))
async def back_to_page(callback: CallbackQuery, callback_data: UserAttachmentCallback, session: AsyncSession, settings: Settings) -> None:
    page = await PageRepository(session).by_id(callback_data.page_id)
    if not page or not page.is_active or not page.published_revision_id:
        user = await upsert_user(session, callback.from_user)
        return await callback.answer(t("unavailable", user.content_language_code), show_alert=True)
    await send_page(callback, session, page.key, ai_available=settings.ai_available, track_view=False)
