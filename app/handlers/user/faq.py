from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import UserFAQCallback, UserPageCallback
from app.database.repositories.cms import CMSRepository
from app.database.repositories.users import upsert_user
from app.utils.i18n import t, translated
from app.utils.pagination import PageSlice, paginate
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text


router = Router(name="user_faq")


def faq_list_keyboard(items: list, language: str, page: int) -> tuple[InlineKeyboardMarkup, PageSlice]:
    result = paginate(items, page)
    rows = []
    for item in result.items:
        translation = translated(item.translations, language)
        question = translation.question if translation else item.question
        rows.append([InlineKeyboardButton(text=text_preview(question, 50), callback_data=UserFAQCallback(item_id=item.id, page=result.page).pack())])
    if result.pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅️", callback_data=UserFAQCallback(item_id=0, page=max(0, result.page - 1)).pack()),
            InlineKeyboardButton(text=f"{result.page + 1} / {result.pages}", callback_data="noop"),
            InlineKeyboardButton(text="➡️", callback_data=UserFAQCallback(item_id=0, page=min(result.pages - 1, result.page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(text=t("back", language), callback_data=UserPageCallback(key="main").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows), result


async def show_faq(cb: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await upsert_user(session, cb.from_user)
    language = user.content_language_code or "ru"
    items = await CMSRepository(session).faqs(active_only=True)

    keyboard, _ = faq_list_keyboard(items, language, page)

    if cb.message:
        await safe_edit_text(
            cb.message,
            t("faq", language),
            reply_markup=keyboard,
        )

    await cb.answer()


# =========================================================
# FAQ MENU
# =========================================================


@router.callback_query(UserPageCallback.filter(F.key == "faq"))
async def menu(cb: CallbackQuery, session: AsyncSession) -> None:
    await show_faq(cb, session, 0)


# =========================================================
# FAQ ANSWER
# =========================================================


@router.callback_query(UserFAQCallback.filter(F.item_id > 0))
async def answer(cb: CallbackQuery, callback_data: UserFAQCallback, session: AsyncSession) -> None:
    item = await CMSRepository(session).faq(callback_data.item_id)
    user = await upsert_user(session, cb.from_user)
    language = user.content_language_code or "ru"

    if not item or not item.is_active:
        await cb.answer(
            t("faq_unavailable", language),
            show_alert=True,
        )
        return

    translation = translated(item.translations, language)

    if translation:
        question = translation.question
        body = translation.answer
    else:
        question = item.question
        body = item.answer

    text = (
        f"❓ <b>{html_text(question)}</b>\n\n"
        f"{html_text(body)}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("back", language),
                    callback_data=UserFAQCallback(
                        item_id=0,
                        page=callback_data.page,
                    ).pack(),
                )
            ]
        ]
    )

    if cb.message:
        await safe_edit_text(cb.message, text, reply_markup=keyboard)

    await cb.answer()


# =========================================================
# BACK TO FAQ LIST
# =========================================================


@router.callback_query(UserFAQCallback.filter(F.item_id == 0))
async def back(cb: CallbackQuery, callback_data: UserFAQCallback, session: AsyncSession) -> None:
    await show_faq(cb, session, callback_data.page)
