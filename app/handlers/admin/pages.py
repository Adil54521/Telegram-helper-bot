from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback, AdminPageCallback
from app.config import Settings
from app.database.repositories.pages import PageRepository
from app.database.models.enums import LanguageCode
from app.keyboards.admin import confirm_edit, page_actions, pages_list
from app.services.pages import PageService
from app.services.permissions import Permission, PermissionService
from app.states.admin import CreatePage, EditPage
from app.utils.telegram import safe_edit_text
from app.utils.i18n import translated
from app.utils.presentation import html_text, text_preview

router = Router(name="admin_pages")


async def authorized(user_id: int, session: AsyncSession, settings: Settings) -> bool:
    return await PermissionService(session, settings).has(user_id, Permission.CONTENT)


async def deny(callback: CallbackQuery) -> None:
    await callback.answer("Доступ запрещён", show_alert=True)


def create_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать", callback_data=AdminPageCallback(action="create_save", page_id=0).pack())],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=AdminPageCallback(action="create_cancel", page_id=0).pack())],
    ])

def translation_by_language(revision, language: LanguageCode):
    return next(
        (
            item
            for item in revision.translations
            if item.language_code == language
        ),
        None,
    )


@router.callback_query(AdminMenuCallback.filter(F.section.in_({"pages", "buttons"})))
async def list_pages(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    if callback.message:
        await safe_edit_text(callback.message, "📝 <b>Страницы</b>", reply_markup=pages_list(await PageRepository(session).all()))
    await callback.answer()


@router.callback_query(AdminPageCallback.filter(F.action == "back"))
async def pages_back(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await list_pages(callback, session, settings)


@router.callback_query(AdminPageCallback.filter(F.action == "open"))
async def open_page(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)

    page = await PageRepository(session).by_id(callback_data.page_id)

    if not page or not page.draft_revision:
        return await callback.answer("Страница не найдена", show_alert=True)

    revision = page.draft_revision

    if not page.published_revision_id:
        status = "🟡 Черновик"
        changes = None

    elif (page.draft_revision_id == page.published_revision_id):
        status = "🟢 Опубликовано"
        changes = "✅ Все изменения опубликованы"

    else:
        status = "🟢 Опубликовано"
        changes = "🟡 Есть неопубликованные изменения"

    ru = translation_by_language(revision, LanguageCode.RU)
    kk = translation_by_language(revision, LanguageCode.KK)

    ru_title = (
        ru.title
        if ru
        else revision.title
    )

    ru_text = (
        ru.text
        if ru
        else revision.text
    )

    if kk:
        kk_title = kk.title
        kk_text = kk.text
        kk_status = ""
    else:
        kk_title = "—"
        kk_text = "Перевод отсутствует"
        kk_status = " ⚠️"

    attachments = [
        item
        for item in revision.attachments
        if item.is_active
    ]

    buttons = await PageRepository(session).buttons(page.id)

    active_buttons = [
        item
        for item in buttons
        if item.is_active
    ]

    text_parts = [
        f"📄 <b>{html_text(revision.title)}</b>",
        "",
        status,
    ]

    if changes:
        text_parts.append(changes)

    text_parts.extend(
        [
            "",
            f"🔑 Ключ: <code>{html_text(page.key)}</code>",
            "",
            "🇷🇺 <b>Русский</b>",
            f"<b>Название:</b> {html_text(ru_title)}",
            "<b>Текст:</b>",
            html_text(text_preview(ru_text)),
            "",
            f"🇰🇿 <b>Қазақша</b>{kk_status}",
            f"<b>Название:</b> {html_text(kk_title)}",
            "<b>Текст:</b>",
            html_text(text_preview(kk_text)),
            "",
            f"📎 Вложений: <b>{len(attachments)}</b>",
            f"🔘 Кнопок: <b>{len(active_buttons)}</b>",
        ]
    )

    text = "\n".join(text_parts)

    rows = [
        [
            InlineKeyboardButton(
                text="🇷🇺 Редактировать RU",
                callback_data=AdminPageCallback(
                    action="lang_ru",
                    page_id=page.id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🇰🇿 Редактировать KK",
                callback_data=AdminPageCallback(
                    action="lang_kk",
                    page_id=page.id,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"📎 Вложения · {len(attachments)}",
                callback_data=AdminPageCallback(
                    action="attachments",
                    page_id=page.id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"🔘 Кнопки · {len(active_buttons)}",
                callback_data=AdminPageCallback(
                    action="buttons",
                    page_id=page.id,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="👁 Предпросмотр",
                callback_data=AdminPageCallback(
                    action="preview",
                    page_id=page.id,
                ).pack(),
            )
        ],
    ]

    if (
        not page.published_revision_id
        or page.draft_revision_id
        != page.published_revision_id
    ):
        publish_label = "🚀 Опубликовать изменения"
    else:
        publish_label = "🚀 Опубликовать"

    rows.append(
        [
            InlineKeyboardButton(
                text=publish_label,
                callback_data=AdminPageCallback(
                    action="publish",
                    page_id=page.id,
                ).pack(),
            )
        ]
    )

    if page.key != "main":
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить страницу",
                    callback_data=AdminPageCallback(
                        action="delete_ask",
                        page_id=page.id,
                    ).pack(),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminPageCallback(
                    action="back",
                    page_id=page.id,
                ).pack(),
            )
        ]
    )

    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    if callback.message:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=markup,
        )

    await callback.answer()


@router.callback_query(AdminPageCallback.filter(F.action == "create"))
async def begin_create(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    await state.set_state(CreatePage.title)
    if callback.message:
        await callback.message.answer("Введите название новой страницы:")
    await callback.answer()


@router.callback_query(AdminPageCallback.filter(F.action.in_({"lang_ru", "lang_kk"})))
async def show_translation(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings): return await deny(callback)
    page = await PageRepository(session).by_id(callback_data.page_id)
    if not page or not page.draft_revision: return await callback.answer("Страница не найдена", show_alert=True)
    code = callback_data.action.removeprefix("lang_")
    value = translated(page.draft_revision.translations, code)
    exact = next((x for x in page.draft_revision.translations if x.language_code.value.lower() == code), None)
    content = f"<b>Название:</b> {html_text(exact.title)}\n\n<b>Текст:</b>\n{html_text(text_preview(exact.text))}" if exact else "⚠️ Перевод отсутствует"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить название", callback_data=AdminPageCallback(action=f"title_{code}", page_id=page.id).pack())],
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=AdminPageCallback(action=f"text_{code}", page_id=page.id).pack())],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminPageCallback(action="open", page_id=page.id).pack())],
    ])
    if callback.message: await safe_edit_text(callback.message, f"{'🇷🇺 Русский' if code == 'ru' else '🇰🇿 Қазақша'}\n\n{content}", reply_markup=markup)
    await callback.answer()


@router.message(CreatePage.title)
async def receive_create_title(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not message.from_user or not await authorized(message.from_user.id, session, settings):
        await state.clear()
        return await message.answer("⛔ Доступ запрещён.")
    title = (message.text or "").strip()
    if not title or len(title) > 255:
        return await message.answer("Название должно содержать от 1 до 255 символов.")
    await state.update_data(title=title)
    await state.set_state(CreatePage.text)
    await message.answer("Введите текст страницы:")


@router.message(CreatePage.text)
async def receive_create_text(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not message.from_user or not await authorized(message.from_user.id, session, settings):
        await state.clear()
        return await message.answer("⛔ Доступ запрещён.")
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Отправьте текстовое сообщение.")
    data = await state.get_data()
    key = await PageService(session).unique_key(data["title"])
    await state.update_data(text=text, preview_key=key)
    await state.set_state(CreatePage.confirm)
    await message.answer(
        f"<b>Предпросмотр новой страницы</b>\n\n<b>{html_text(data['title'])}</b>\n\n{html_text(text)}\n\n"
        f"Системный key: <code>{html_text(key)}</code>\nСтатус: 🟡 Черновик",
        reply_markup=create_confirm_keyboard(),
    )


@router.callback_query(CreatePage.confirm, AdminPageCallback.filter(F.action == "create_save"))
async def save_created_page(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        await state.clear()
        return await deny(callback)
    data = await state.get_data()
    if not data.get("title") or not data.get("text"):
        await state.clear()
        return await callback.answer("Сессия создания истекла", show_alert=True)
    page = await PageService(session).create(data["title"], data["text"], callback.from_user.id)
    await state.clear()
    await callback.answer("Страница создана как черновик", show_alert=True)
    await open_page(callback, AdminPageCallback(action="open", page_id=page.id), session, settings)


@router.callback_query(AdminPageCallback.filter(F.action == "create_cancel"))
async def cancel_create(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    await list_pages(callback, session, settings)


@router.callback_query(AdminPageCallback.filter(F.action.in_({"text", "title", "text_ru", "title_ru", "text_kk", "title_kk"})))
async def begin_edit(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    parts = callback_data.action.split("_")
    field, language = parts[0], parts[1] if len(parts) > 1 else "ru"
    await state.set_state(EditPage.waiting_text if field == "text" else EditPage.waiting_title)
    await state.update_data(page_id=callback_data.page_id, field=field, language=language)
    if callback.message:
        await callback.message.answer("Отправьте новый текст страницы." if field == "text" else "Отправьте новое название страницы.")
    await callback.answer()


@router.message(EditPage.waiting_text)
@router.message(EditPage.waiting_title)
async def receive_edit(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not message.from_user or not await authorized(message.from_user.id, session, settings):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return
    if not message.text:
        await message.answer("Нужен текст. Отправьте текстовое сообщение или /cancel.")
        return
    data = await state.get_data()
    field, page_id = data["field"], data["page_id"]
    await state.update_data(value=message.text)
    await state.set_state(EditPage.confirm_text if field == "text" else EditPage.confirm_title)
    await message.answer(f"<b>Предпросмотр:</b>\n\n{html_text(message.text)}", reply_markup=confirm_edit(page_id, field))


@router.callback_query(AdminPageCallback.filter(F.action.in_({"save_text", "save_title"})))
async def save_edit(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        await state.clear()
        return await deny(callback)
    data = await state.get_data()
    field = callback_data.action.removeprefix("save_")
    if data.get("page_id") != callback_data.page_id or data.get("field") != field or not data.get("value"):
        await state.clear()
        return await callback.answer("Сессия редактирования истекла", show_alert=True)
    language = LanguageCode(data.get("language", "ru"))
    await PageService(session).update_translation(callback_data.page_id, callback.from_user.id, language, **{field: data["value"]})
    await state.clear()
    await callback.answer("Черновик сохранён", show_alert=True)
    await open_page(callback, AdminPageCallback(action="open", page_id=callback_data.page_id), session, settings)


@router.callback_query(AdminPageCallback.filter(F.action == "cancel"))
async def cancel_edit(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    await open_page(callback, AdminPageCallback(action="open", page_id=callback_data.page_id), session, settings)


@router.callback_query(AdminPageCallback.filter(F.action == "preview"))
async def preview(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    page = await PageRepository(session).by_id(callback_data.page_id)
    if page and page.draft_revision and callback.message:
        await callback.message.answer(f"👁 <b>Предпросмотр черновика</b>\n\n<b>{html_text(page.draft_revision.title)}</b>\n\n{html_text(page.draft_revision.text)}")
    await callback.answer()


@router.callback_query(AdminPageCallback.filter(F.action == "publish"))
async def publish(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    page = await PageRepository(session).by_id(callback_data.page_id)
    missing_kk = bool(page and page.draft_revision and not any(x.language_code == LanguageCode.KK for x in page.draft_revision.translations))
    await PageService(session).publish(callback_data.page_id, callback.from_user.id)
    if missing_kk and callback.message:
        await callback.message.answer("⚠️ Страница опубликована, но перевод KK отсутствует. Пользователям будет показан RU fallback.")
    await open_page(callback, AdminPageCallback(action="open", page_id=callback_data.page_id), session, settings)


@router.callback_query(AdminPageCallback.filter(F.action.in_({"delete_ask", "delete_yes"})))
async def delete_page(callback: CallbackQuery, callback_data: AdminPageCallback, session: AsyncSession, settings: Settings) -> None:
    if not await authorized(callback.from_user.id, session, settings):
        return await deny(callback)
    page = await PageRepository(session).by_id(callback_data.page_id)
    if not page:
        return await callback.answer("Страница не найдена", show_alert=True)
    if page.key == "main":
        return await callback.answer("Системную страницу main удалить нельзя", show_alert=True)
    dependencies = await PageService(session).deletion_dependencies(page.id)
    if dependencies:
        references = "\n".join(f"• {html_text(source.draft_revision.title if source.draft_revision else source.key)}: кнопка «{html_text(button.text)}»" for button, source in dependencies)
        if callback.message:
            await safe_edit_text(callback.message,
                f"⚠️ <b>Страницу удалить нельзя</b>\n\nНа неё ссылаются:\n{references}\n\n"
                "Сначала измените или удалите эти PAGE-кнопки.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminPageCallback(action="open", page_id=page.id).pack())]]),
            )
        return await callback.answer()
    if callback_data.action == "delete_ask":
        title = page.draft_revision.title if page.draft_revision else page.key
        if callback.message:
            await safe_edit_text(callback.message,
                f"⚠️ <b>Удалить страницу «{html_text(title)}»?</b>\n\nЭто действие нельзя отменить.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔴 Да, удалить", callback_data=AdminPageCallback(action="delete_yes", page_id=page.id).pack())],
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data=AdminPageCallback(action="open", page_id=page.id).pack())],
                ]),
            )
        return await callback.answer()
    try:
        await PageService(session).delete(page.id, callback.from_user.id)
    except ValueError as error:
        return await callback.answer(str(error), show_alert=True)
    await callback.answer("Страница удалена", show_alert=True)
    await list_pages(callback, session, settings)
