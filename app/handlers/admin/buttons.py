from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminPageCallback, ButtonCallback
from app.config import Settings
from app.database.models.enums import ButtonAction, LanguageCode
from app.database.repositories.cms import CMSRepository
from app.database.repositories.pages import PageRepository
from app.services.cms import ButtonService
from app.services.permissions import Permission, PermissionService
from app.states.admin import AddButton, EditButton
from app.utils.telegram import safe_edit_text
from app.utils.wizard import consume_input, edit_wizard, finish_wizard, remember_wizard
from app.ai.service import AIService
from app.database.repositories.users import upsert_user
from app.utils.presentation import button_action_details, button_action_icon, html_text, text_preview
from app.utils.pagination import paginate
from app.filters.admin import HasPermission


router = Router(name="admin_buttons")
router.callback_query.filter(HasPermission(Permission.CONTENT_VIEW))
router.message.filter(HasPermission(Permission.CONTENT_VIEW))

MAX_BUTTON_TEXT_LENGTH = 128


async def allowed(
    uid: int,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    return await PermissionService(
        session,
        settings,
    ).has(uid, Permission.CONTENT_VIEW)


async def guard(
    cb: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    if await allowed(cb.from_user.id, session, settings):
        return True

    await cb.answer("Доступ запрещён", show_alert=True)
    return False

async def can_modify_buttons(
    user_id: int,
    page_id: int,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    permissions = PermissionService(session, settings)

    if not await permissions.has(
        user_id,
        Permission.CONTENT_MANAGE,
    ):
        return False

    page = await PageRepository(session).by_id(page_id)

    if page is None:
        return False

    if page.published_revision_id is None:
        return True

    return await permissions.has(
        user_id,
        Permission.CONTENT_PUBLISH,
    )

async def guard_modify_buttons(
    cb: CallbackQuery,
    page_id: int,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    if await can_modify_buttons(
        cb.from_user.id,
        page_id,
        session,
        settings,
    ):
        return True

    await cb.answer(
        "Недостаточно прав для изменения кнопок этой страницы",
        show_alert=True,
    )
    return False

async def guard_button_edit(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    item = await CMSRepository(session).button(
        callback_data.item_id,
        callback_data.page_id,
    )

    if item is None:
        await cb.answer(
            "Кнопка не найдена",
            show_alert=True,
        )
        return False

    return await guard_modify_buttons(
        cb,
        item.page_id,
        session,
        settings,
    )

def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def action_choices():
    return (
        ("📄 Открыть страницу", "PAGE"),
        ("🌐 Открыть сайт", "URL"),
        ("🏠 Главное меню", "HOME"),
        ("📞 Позвонить", "PHONE"),
        ("✉️ Email", "EMAIL"),
        ("💬 Telegram", "TELEGRAM"),
    )


def validate_button_text(
    value: str,
) -> tuple[str | None, str | None]:
    value = value.strip()

    if not value:
        return None, "Название кнопки не может быть пустым."

    if len(value) > MAX_BUTTON_TEXT_LENGTH:
        return (
            None,
            "Название кнопки слишком длинное.\n\n"
            f"Максимум: {MAX_BUTTON_TEXT_LENGTH} символов.\n"
            f"Сейчас: {len(value)} символов.",
        )

    return value, None


def normalize_target(
    action: str,
    value: str,
) -> tuple[str | None, str | None]:
    value = value.strip()

    if action == "URL":
        parsed = urlparse(value)

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            return (
                None,
                "Некорректный URL.\n"
                "Например: https://kuam.edu.kz",
            )

        return value, None

    if action == "PHONE":
        cleaned = (
            value
            .replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

        # Казахстанский номер в формате:
        # 8 777 123 45 67 -> +7 777 123 45 67
        if cleaned.startswith("8") and len(cleaned) == 11:
            cleaned = "+7" + cleaned[1:]

        if (
            not cleaned.startswith("+")
            or not cleaned[1:].isdigit()
            or len(cleaned) < 8
            or len(cleaned) > 16
        ):
            return (
                None,
                "Некорректный номер телефона.\n"
                "Например: +7 7162 25-80-00",
            )

        return cleaned, None

    if action == "EMAIL":
        if (
            "@" not in value
            or value.startswith("@")
            or value.endswith("@")
        ):
            return (
                None,
                "Некорректный email.\n"
                "Например: info@kuam.kz",
            )

        local_part, domain = value.rsplit("@", 1)

        if (
            not local_part
            or not domain
            or "." not in domain
        ):
            return (
                None,
                "Некорректный email.\n"
                "Например: info@kuam.kz",
            )

        return value, None

    if action == "TELEGRAM":
        username = value

        if username.startswith("https://t.me/"):
            username = username.removeprefix(
                "https://t.me/"
            )

        if username.startswith("http://t.me/"):
            username = username.removeprefix(
                "http://t.me/"
            )

        username = username.lstrip("@").strip("/")

        if not username:
            return (
                None,
                "Введите Telegram username.\n"
                "Например: @kuam_kz",
            )

        # Telegram username:
        # буквы, цифры и underscore.
        if not all(
            ch.isalnum() or ch == "_"
            for ch in username
        ):
            return (
                None,
                "Некорректный Telegram username.\n"
                "Например: @kuam_kz",
            )

        return username, None

    return (
        None,
        "Неизвестный тип действия.",
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return kb(
        [
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=ButtonCallback(
                        action="new_save",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=ButtonCallback(
                        action="new_cancel",
                    ).pack(),
                )
            ],
        ]
    )


async def show_list(
    cb: CallbackQuery,
    page_id: int,
    session: AsyncSession,
    settings: Settings,
    page: int = 0,
) -> None:
    items = await CMSRepository(session).buttons(page_id)
    can_edit = await can_modify_buttons(
        cb.from_user.id,
        page_id,
        session,
        settings,
    )
    result=paginate(items,page)

    active_count = sum(1 for item in items if item.is_active)

    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{i + 1}. "
                    f"{'🔴 ' if not x.is_active else ''}"
                    f"{button_action_icon(x.action_type)} "
                    f"{text_preview(x.text, 40)}"
                ),
                callback_data=ButtonCallback(
                    action="open",
                    item_id=x.id,
                    page_id=page_id,
                    page=result.page,
                ).pack(),
            )
        ]
        for i, x in enumerate(result.items,start=result.page*8)
    ]

    if result.pages>1:
        rows.append([InlineKeyboardButton(text="⬅️",callback_data=ButtonCallback(action="list",page_id=page_id,page=max(0,result.page-1)).pack()),InlineKeyboardButton(text=f"{result.page+1} / {result.pages}",callback_data="noop"),InlineKeyboardButton(text="➡️",callback_data=ButtonCallback(action="list",page_id=page_id,page=min(result.pages-1,result.page+1)).pack())])

    if can_edit:
        rows.append([
            InlineKeyboardButton(
                text="➕ Добавить кнопку",
                callback_data=ButtonCallback(
                    action="add",
                    page_id=page_id,
                    page=result.page,
                ).pack(),
            )
        ])

    rows += [
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminPageCallback(
                    action="open",
                    page_id=page_id,
                ).pack(),
            )
        ],
    ]

    if cb.message:
        await safe_edit_text(
            cb.message,
            "🔘 <b>Кнопки страницы</b>\n\n"
            f"Всего: <b>{len(items)}</b>\n"
            f"🟢 Активных: <b>{active_count}</b>\n🔴 Выключено: <b>{len(items)-active_count}</b>\n\n"
            + (
                "Выберите кнопку."
                if items
                else "Кнопок пока нет."
            ),
            reply_markup=kb(rows),
        )

    await cb.answer()


@router.callback_query(
    AdminPageCallback.filter(F.action == "buttons")
)
async def page_buttons(
    cb: CallbackQuery,
    callback_data: AdminPageCallback,
    session: AsyncSession,
    settings: Settings,
):
    if await guard(cb, session, settings):
        await show_list(
            cb,
            callback_data.page_id,
            session,
            settings,
            callback_data.page,
        )


@router.callback_query(
    ButtonCallback.filter(F.action == "list")
)
async def list_buttons(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
):
    if await guard(cb, session, settings):
        await show_list(
            cb,
            callback_data.page_id,
            session,
            settings,
            callback_data.page,
        )


@router.callback_query(
    ButtonCallback.filter(F.action == "open")
)
async def open_button(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
):
    if not await guard(cb, session, settings):
        return

    item = await CMSRepository(session).button(
        callback_data.item_id, callback_data.page_id
    )

    if not item:
        return await cb.answer(
            "Кнопка не найдена",
            show_alert=True,
        )

    can_edit = await can_modify_buttons(
        cb.from_user.id,
        item.page_id,
        session,
        settings,
    )

    rows = [
        [
            InlineKeyboardButton(
                text="🇷🇺 Текст",
                callback_data=ButtonCallback(
                    action="edit_text_ru",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🇰🇿 Мәтін",
                callback_data=ButtonCallback(
                    action="edit_text_kk",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🎯 Действие",
                callback_data=ButtonCallback(
                    action="choose_action",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="⬆️ Выше",
                callback_data=ButtonCallback(
                    action="up",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⬇️ Ниже",
                callback_data=ButtonCallback(
                    action="down",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text=(
                    "🔴 Выключить"
                    if item.is_active
                    else "🟢 Включить"
                ),
                callback_data=ButtonCallback(
                    action="toggle",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=ButtonCallback(
                    action="delete_ask",
                    item_id=item.id,
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=ButtonCallback(
                    action="list",
                    page_id=item.page_id,
                    page=callback_data.page,
                ).pack(),
            )
        ],
    ]

    if not can_edit:
        rows = [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=ButtonCallback(
                        action="list",
                        page_id=item.page_id,
                        page=callback_data.page,
                    ).pack(),
                )
            ]
        ]

    ru = next((value for value in item.translations if value.language_code == LanguageCode.RU), None)
    kk = next((value for value in item.translations if value.language_code == LanguageCode.KK), None)
    ru_text = ru.text if ru else item.text
    kk_block = f"<b>Текст:</b> {html_text(kk.text)}" if kk else "⚠️ Перевод отсутствует"
    target_display = item.target
    if item.action_type == ButtonAction.PAGE and item.target:
        target_page = await PageRepository(session).by_key(item.target)
        if target_page and target_page.draft_revision:
            target_display = target_page.draft_revision.title
    text = (
        f"🔘 <b>{html_text(ru_text)}</b>\n\n"
        f"{'🟢 Активна' if item.is_active else '🔴 Выключена'}\n\n"
        f"🇷🇺 <b>Русский</b>\n<b>Текст:</b> {html_text(ru_text)}\n\n"
        f"🇰🇿 <b>Қазақша{' ⚠️' if not kk else ''}</b>\n{kk_block}\n\n"
        f"🎯 <b>Действие</b>\n{button_action_details(item.action_type, target_display)}\n\n"
        f"📍 <b>Расположение</b>\n"
        f"Позиция: {item.position + 1}\n"
        f"Ряд: {item.row + 1}"
    )

    if cb.message:
        await safe_edit_text(
            cb.message,
            text,
            reply_markup=kb(rows),
        )

    await cb.answer()


# ============================================================
# CREATE BUTTON
# ============================================================


@router.callback_query(
    ButtonCallback.filter(F.action == "add")
)
async def add(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if not await guard_modify_buttons(cb, callback_data.page_id, session, settings):
        return

    await state.set_state(AddButton.text)
    await state.update_data(page_id=callback_data.page_id,list_page=callback_data.page,text=None,text_kk=None,action=None,target=None)

    if cb.message:
        await remember_wizard(state,cb.message)
        await safe_edit_text(cb.message,"🔘 <b>Новая кнопка</b>\n\nШаг 1 из 4\n🇷🇺 <b>Текст кнопки</b>\n\nОтправьте название.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=ButtonCallback(action="new_cancel",page_id=callback_data.page_id).pack())]]))

    await cb.answer()


@router.message(AddButton.text)
async def add_text(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if (
        not message.from_user
        or not await allowed(
            message.from_user.id,
            session,
            settings,
        )
    ):
        await state.clear()
        return

    if not message.text:
        return await message.answer(
            "Отправьте текст."
        )

    text, error = validate_button_text(
        message.text
    )

    if error:
        return await message.answer(
            f"⚠️ {error}\n\n"
            "Введите название ещё раз."
        )

    await state.update_data(text=text);await consume_input(message);await state.set_state(AddButton.text_kk)
    await edit_wizard(message.bot,state,f"🔘 <b>Новая кнопка</b>\n\nШаг 2 из 4\n🇰🇿 <b>Қазақша</b>\n\n🇷🇺 Текст: {html_text(text)}",kk_choice_keyboard())


@router.message(AddButton.text_kk)
async def add_text_kk(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if (
        not message.from_user
        or not await allowed(
            message.from_user.id,
            session,
            settings,
        )
    ):
        await state.clear()
        return

    if not message.text:
        return await message.answer(
            "Отправьте текст."
        )

    text, error = validate_button_text(
        message.text
    )

    if error:
        return await message.answer(
            f"⚠️ {error}\n\n"
            "Введите название ещё раз."
        )

    await state.update_data(text_kk=text);await consume_input(message);await show_new_actions(message.bot,state)


def kk_choice_keyboard():
    return kb([[InlineKeyboardButton(text="✍️ Ввести KK",callback_data=ButtonCallback(action="new_kk_manual").pack())],[InlineKeyboardButton(text="✨ Создать KK через Gemini",callback_data=ButtonCallback(action="new_kk_ai").pack())],[InlineKeyboardButton(text="⏭ Без KK",callback_data=ButtonCallback(action="new_kk_skip").pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=ButtonCallback(action="new_back_text").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=ButtonCallback(action="new_cancel").pack())]])


async def show_new_actions(bot,state):
    await state.set_state(AddButton.action)
    rows=[[InlineKeyboardButton(text=label,callback_data=ButtonCallback(action="new_type",value=value).pack())] for label,value in action_choices()]
    rows += [[InlineKeyboardButton(text="⬅️ Назад",callback_data=ButtonCallback(action="new_back_kk").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=ButtonCallback(action="new_cancel").pack())]]
    await edit_wizard(bot,state,"🔘 <b>Новая кнопка</b>\n\nШаг 3 из 4\n🎯 <b>Действие</b>\n\nВыберите действие кнопки.",kb(rows))


@router.callback_query(ButtonCallback.filter(F.action.in_({"new_kk_manual","new_kk_skip","new_back_text","new_back_kk"})))
async def new_navigation(cb:CallbackQuery,callback_data:ButtonCallback,state:FSMContext):
    if callback_data.action=="new_kk_manual":
        await state.set_state(AddButton.text_kk);await safe_edit_text(cb.message,"🔘 <b>Новая кнопка</b>\n\nШаг 2 из 4\n🇰🇿 Отправьте текст кнопки.",reply_markup=kk_choice_keyboard())
    elif callback_data.action=="new_kk_skip":await state.update_data(text_kk=None);await show_new_actions(cb.bot,state)
    elif callback_data.action=="new_back_text":await state.set_state(AddButton.text);await safe_edit_text(cb.message,"🔘 <b>Новая кнопка</b>\n\nШаг 1 из 4\n🇷🇺 Отправьте название.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=ButtonCallback(action="new_cancel").pack())]]))
    else:d=await state.get_data();await state.set_state(AddButton.text_kk);await safe_edit_text(cb.message,f"🔘 <b>Новая кнопка</b>\n\nШаг 2 из 4\n🇰🇿 <b>Қазақша</b>\n\n🇷🇺 Текст: {html_text(d['text'])}",reply_markup=kk_choice_keyboard())
    await cb.answer()


@router.callback_query(ButtonCallback.filter(F.action=="new_kk_ai"))
async def new_kk_ai(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext,ai_service:AIService|None):
    if not settings.ai_available or ai_service is None:return await cb.answer("Gemini недоступен",show_alert=True)
    d=await state.get_data();user=await upsert_user(session,cb.from_user)
    try:value=await ai_service.translate_short_text(session,user_id=user.id,text=d["text"],request_type="CMS_TRANSLATION",max_length=128)
    except Exception:return await cb.answer("Не удалось создать перевод",show_alert=True)
    await state.update_data(text_kk=value);await show_new_actions(cb.bot,state);await cb.answer()


@router.callback_query(
    AddButton.action,
    ButtonCallback.filter(
        F.action == "new_type"
    ),
)
async def new_type(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if not await guard(cb, session, settings):
        await state.clear()
        return

    action = callback_data.value

    allowed_actions = {
        "PAGE",
        "URL",
        "HOME",
        "PHONE",
        "EMAIL",
        "TELEGRAM",
    }

    if action not in allowed_actions:
        return await cb.answer(
            "Неизвестное действие.",
            show_alert=True,
        )

    await state.update_data(action=action)

    if action == "PAGE":
        pages = await PageRepository(session).all()

        rows = [
            [
                InlineKeyboardButton(
                    text=(
                        page.draft_revision.title
                        if page.draft_revision
                        else page.key
                    ),
                    callback_data=ButtonCallback(
                        action="new_target",
                        item_id=page.id,
                    ).pack(),
                )
            ]
            for page in pages
            if page.is_active
        ]

        if cb.message:
            await safe_edit_text(
                cb.message,
                "📄 Выберите страницу:",
                reply_markup=kb(rows),
            )

    elif action in {"BACK", "HOME"}:
        await preview_new(
            cb,
            state,
            None,
        )

    else:
        await state.set_state(AddButton.target)

        prompts = {
            "URL": (
                "🌐 Введите URL:\n\n"
                "Например:\n"
                "https://kuam.edu.kz"
            ),
            "PHONE": (
                "📞 Введите номер телефона:\n\n"
                "Например:\n"
                "+7 7162 25-80-00"
            ),
            "EMAIL": (
                "✉️ Введите email:\n\n"
                "Например:\n"
                "info@kuam.kz"
            ),
            "TELEGRAM": (
                "💬 Введите Telegram username:\n\n"
                "Например:\n"
                "@kuam_kz"
            ),
        }

        if cb.message:
            await safe_edit_text(cb.message,"🔘 <b>Новая кнопка</b>\n\nШаг 4 из 4\n"+prompts[action],reply_markup=kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=ButtonCallback(action="new_back_action").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=ButtonCallback(action="new_cancel").pack())]]))

    await cb.answer()


@router.callback_query(
    AddButton.action,
    ButtonCallback.filter(
        F.action == "new_target"
    ),
)
async def new_target(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if not await guard(
        cb,
        session,
        settings,
    ):
        await state.clear()
        return

    page = await PageRepository(session).by_id(callback_data.item_id)
    if not page or not page.is_active:
        return await cb.answer("Страница недоступна", show_alert=True)
    await preview_new(cb, state, page.key)

    await cb.answer()


@router.message(AddButton.target)
async def new_target_value(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if (
        not message.from_user
        or not await allowed(
            message.from_user.id,
            session,
            settings,
        )
    ):
        await state.clear()
        return

    if not message.text:
        return await message.answer(
            "Отправьте текстовое значение."
        )

    data = await state.get_data()
    action = data.get("action")

    target, error = normalize_target(
        action,
        message.text,
    )

    if error:
        return await message.answer(
            f"⚠️ {error}"
        )

    await state.update_data(
        target=target
    )
    await consume_input(message)
    await state.set_state(
        AddButton.confirm
    )

    await edit_wizard(message.bot,state,
        "<b>Новая кнопка</b>\n\n"
        f"🇷🇺 {html_text(data['text'])}\n"
        f"🇰🇿 {html_text(data.get('text_kk', '—'))}\n"
        f"🎯 {button_action_details(action, target)}",
        confirm_keyboard(),
    )


async def preview_new(
    cb: CallbackQuery,
    state: FSMContext,
    target: str | None,
):
    await state.update_data(
        target=target
    )
    await state.set_state(
        AddButton.confirm
    )

    data = await state.get_data()

    if cb.message:
        await safe_edit_text(
            cb.message,
            "<b>Новая кнопка</b>\n\n"
            f"🇷🇺 {html_text(data['text'])}\n"
            f"🇰🇿 {html_text(data.get('text_kk', '—'))}\n"
            f"🎯 {button_action_details(data['action'], target)}",
            reply_markup=confirm_keyboard(),
        )



@router.callback_query(
    AddButton.confirm,
    ButtonCallback.filter(F.action == "new_save"),
)
async def new_save(
    cb: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await guard(cb, session, settings):
        await state.clear()
        return

    data = await state.get_data()
    page_id = data.get("page_id")

    if not isinstance(page_id, int) or page_id <= 0:
        await cb.answer(
            "Некорректная страница",
            show_alert=True,
        )
        return

    if not await guard_modify_buttons(
        cb,
        page_id,
        session,
        settings,
    ):
        return

    item = await ButtonService(session).create(
        cb.from_user.id,
        page_id,
        data["text"],
        ButtonAction(data["action"]),
        data.get("target"),
        data.get("text_kk"),
    )

    await finish_wizard(state,"page_id","list_page","text","text_kk","action","target")

    await show_list(
        cb,
        item.page_id,
        session,
        settings,
        data.get("list_page",0),
    )


@router.callback_query(
    ButtonCallback.filter(
        F.action == "new_cancel"
    )
)
async def new_cancel(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
):
    data = await state.get_data()
    page_id = data.get("page_id")
    page = data.get("list_page",0)
    await finish_wizard(state, "page_id", "list_page", "text", "text_kk", "action", "target")
    
    if page_id:
        await show_list(cb, page_id, session, settings, page)
    else:
        await cb.answer()

@router.callback_query(ButtonCallback.filter(F.action=="new_back_action"))
async def new_back_action(cb:CallbackQuery,state:FSMContext):
    await show_new_actions(cb.bot,state)
    await cb.answer()

@router.callback_query(
    ButtonCallback.filter(
        F.action.in_({
            "up",
            "down",
            "toggle",
            "delete_ask",
            "delete_yes",
        })
    )
)
async def mutate(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await guard(cb, session, settings):
        return

    item = await CMSRepository(session).button(
        callback_data.item_id,
        callback_data.page_id,
    )

    if not item:
        await cb.answer(
            "Кнопка не найдена",
            show_alert=True,
        )
        return

    if not await guard_modify_buttons(
        cb,
        item.page_id,
        session,
        settings,
    ):
        return

    service = ButtonService(session)
    action = callback_data.action

    if action in {"up", "down"}:
        delta = -1 if action == "up" else 1

        await service.move(
            cb.from_user.id,
            item,
            delta,
        )

        moved_items = await CMSRepository(session).buttons(
            item.page_id
        )

        new_page = next(
            (
                index // 8
                for index, value in enumerate(moved_items)
                if value.id == item.id
            ),
            callback_data.page,
        )

        await open_button(
            cb,
            ButtonCallback(
                action="open",
                item_id=item.id,
                page_id=item.page_id,
                page=new_page,
            ),
            session,
            settings,
        )
        return

    if action == "toggle":
        await service.update(
            cb.from_user.id,
            item,
            is_active=not item.is_active,
        )

        await open_button(
            cb,
            ButtonCallback(
                action="open",
                item_id=item.id,
                page_id=item.page_id,
                page=callback_data.page,
            ),
            session,
            settings,
        )
        return

    if action == "delete_ask":
        if cb.message:
            await safe_edit_text(
                cb.message,
                (
                    f"⚠️ <b>Удалить кнопку "
                    f"«{html_text(item.text)}»?</b>\n\n"
                    "Это действие нельзя отменить."
                ),
                reply_markup=kb([
                    [
                        InlineKeyboardButton(
                            text="🔴 Да, удалить",
                            callback_data=ButtonCallback(
                                action="delete_yes",
                                item_id=item.id,
                                page_id=item.page_id,
                                page=callback_data.page,
                            ).pack(),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ Отмена",
                            callback_data=ButtonCallback(
                                action="open",
                                item_id=item.id,
                                page_id=item.page_id,
                                page=callback_data.page,
                            ).pack(),
                        )
                    ],
                ]),
            )

        await cb.answer()
        return

    if action == "delete_yes":
        page_id = item.page_id

        await service.delete(
            cb.from_user.id,
            item,
        )

        await show_list(
            cb,
            page_id,
            session,
            settings,
            callback_data.page,
        )
        return


@router.callback_query(
    ButtonCallback.filter(
        F.action.in_(
            {
                "edit_text",
                "edit_text_ru",
                "edit_text_kk",
            }
        )
    )
)
async def edit_text(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if not await guard_button_edit(
        cb,
        callback_data,
        session,
        settings,
    ):
        return

    language = (
        callback_data.action.rsplit(
            "_",
            1,
        )[-1]
        if callback_data.action != "edit_text"
        else "ru"
    )

    await state.set_state(
        EditButton.value
    )

    await state.update_data(
        item_id=callback_data.item_id,
        page_id=callback_data.page_id,
        mode="text",
        language=language,
        list_page=callback_data.page,
    )

    if cb.message:
        await cb.message.answer(
            "Введите новый текст кнопки:"
        )

    await cb.answer()


@router.callback_query(
    ButtonCallback.filter(
        F.action == "choose_action"
    )
)
async def choose_existing_action(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
):
    if not await guard_button_edit(
        cb,
        callback_data,
        session,
        settings,
    ):
        return

    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=ButtonCallback(
                    action="set_action",
                    item_id=callback_data.item_id,
                    page_id=callback_data.page_id,
                    value=value,
                    page=callback_data.page,
                ).pack(),
            )
        ]
        for label, value in action_choices()
    ]

    await safe_edit_text(
        cb.message,
        "🎯 <b>Выберите действие кнопки</b>\n\n"
        "📄 Навигация\n"
        "🔗 Ссылки и контакты",
        reply_markup=kb(rows + [[InlineKeyboardButton(text="❌ Отмена", callback_data=ButtonCallback(action="open", item_id=callback_data.item_id, page_id=callback_data.page_id,page=callback_data.page).pack())]]),
    )

    await cb.answer()


@router.callback_query(
    ButtonCallback.filter(
        F.action == "set_action"
    )
)
async def set_existing_action(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if not await guard(
        cb,
        session,
        settings,
    ):
        return

    item = await CMSRepository(
        session
    ).button(
        callback_data.item_id, callback_data.page_id
    )

    if not item:
        return await cb.answer(
            "Кнопка не найдена",
            show_alert=True,
        )

    if not await guard_modify_buttons(
        cb,
        item.page_id,
        session,
        settings,
    ):
        return

    action = callback_data.value

    if action not in {
        "PAGE",
        "URL",
        "BACK",
        "HOME",
        "PHONE",
        "EMAIL",
        "TELEGRAM",
    }:
        return await cb.answer(
            "Неизвестное действие.",
            show_alert=True,
        )

    if action in {"BACK", "HOME"}:
        await ButtonService(
            session
        ).update(
            cb.from_user.id,
            item,
            action_type=ButtonAction(action),
            target=None,
        )

        return await open_button(
            cb,
            ButtonCallback(
                action="open",
                item_id=item.id,
                page_id=item.page_id,
                page=callback_data.page,
            ),
            session,
            settings,
        )

    if action == "PAGE":
        pages = await PageRepository(
            session
        ).all()

        rows = [
            [
                InlineKeyboardButton(
                    text=(
                        page.draft_revision.title
                        if page.draft_revision
                        else page.key
                    ),
                    callback_data=ButtonCallback(
                        action="set_page",
                        item_id=item.id,
                        page_id=item.page_id,
                        value=str(page.id),
                    ).pack(),
                )
            ]
            for page in pages
            if page.is_active
        ]

        await safe_edit_text(
            cb.message,
            "📄 Выберите страницу:",
            reply_markup=kb(rows),
        )

        return await cb.answer()

    await state.set_state(
        EditButton.value
    )

    await state.update_data(
        item_id=item.id,
        page_id=item.page_id,
        mode="target",
        action=action,
    )

    prompts = {
        "URL": (
            "🌐 Введите новый URL:\n\n"
            "Например: https://kuam.edu.kz"
        ),
        "PHONE": (
            "📞 Введите новый номер:\n\n"
            "Например: +7 7162 25-80-00"
        ),
        "EMAIL": (
            "✉️ Введите новый email:\n\n"
            "Например: info@kuam.kz"
        ),
        "TELEGRAM": (
            "💬 Введите Telegram username:\n\n"
            "Например: @kuam_kz"
        ),
    }

    if cb.message:
        await cb.message.answer(
            prompts[action]
        )

    await cb.answer()


@router.callback_query(
    ButtonCallback.filter(
        F.action == "set_page"
    )
)
async def set_existing_page(
    cb: CallbackQuery,
    callback_data: ButtonCallback,
    session: AsyncSession,
    settings: Settings,
):
    if not await guard(
        cb,
        session,
        settings,
    ):
        return

    item = await CMSRepository(
        session
    ).button(
        callback_data.item_id, callback_data.page_id
    )

    try:
        target_page_id = int(callback_data.value)
    except ValueError:
        return await cb.answer("Страница недоступна", show_alert=True)
    page = await PageRepository(session).by_id(target_page_id)

    if (
        not item
        or not page
        or not page.is_active
    ):
        return await cb.answer(
            "Страница недоступна",
            show_alert=True,
        )

    if not await guard_modify_buttons(
        cb,
        item.page_id,
        session,
        settings,
    ):
        return

    await ButtonService(
        session
    ).update(
        cb.from_user.id,
        item,
        action_type=ButtonAction.PAGE,
        target=page.key,
    )

    await open_button(
        cb,
        ButtonCallback(
            action="open",
            item_id=item.id,
            page_id=item.page_id,
        ),
        session,
        settings,
    )

@router.message(EditButton.value)
async def save_edit(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
):
    if (
        not message.from_user
        or not await allowed(
            message.from_user.id,
            session,
            settings,
        )
    ):
        await state.clear()
        return

    data = await state.get_data()

    item = await CMSRepository(
        session
    ).button(
        data["item_id"], data.get("page_id")
    )

    if not item:
        await state.clear()
        return await message.answer(
            "⚠️ Кнопка не найдена."
        )

    if not await can_modify_buttons(
        message.from_user.id,
        item.page_id,
        session,
        settings,
    ):
        await state.clear()
        await message.answer(
            "Недостаточно прав для изменения кнопок этой страницы."
        )
        return

    if not message.text:
        return await message.answer(
            "Отправьте текстовое значение."
        )

    mode = data.get("mode")

    # Изменение RU / KK текста кнопки
    if mode == "text":
        text, error = validate_button_text(
            message.text
        )

        if error:
            return await message.answer(
                f"⚠️ {error}\n\n"
                "Введите название ещё раз."
            )

        await ButtonService(
            session
        ).update_translation(
            message.from_user.id,
            item,
            LanguageCode(
                data.get(
                    "language",
                    "ru",
                )
            ),
            text,
        )

        await state.clear()

        return await message.answer(
            "✅ Текст кнопки обновлён."
        )

    # Изменение URL / PHONE / EMAIL / TELEGRAM
    if mode == "target":
        action = data.get("action")

        target, error = normalize_target(
            action,
            message.text,
        )

        if error:
            return await message.answer(
                f"⚠️ {error}"
            )

        await ButtonService(
            session
        ).update(
            message.from_user.id,
            item,
            action_type=ButtonAction(action),
            target=target,
        )

        await state.clear()

        return await message.answer(
            "✅ Действие кнопки обновлено."
        )

    await state.clear()

    await message.answer(
        "⚠️ Неизвестный режим редактирования."
    )
