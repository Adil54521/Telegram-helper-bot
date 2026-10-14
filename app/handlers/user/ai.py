import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import AIProviderError
from app.ai.service import AIRateLimitError, AIService, MAX_QUESTION_LENGTH
from app.callbacks import UserAICallback, UserAIHistoryCallback
from app.config import Settings
from app.database.repositories.users import upsert_user
from app.database.repositories.ai_history import AIHistoryRepository
from app.handlers.user.pages import send_page
from app.states.user import AskAI
from app.utils.i18n import normalize_language, t
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_delete_message, safe_edit_text
from app.utils.pagination import paginate_count

logger = logging.getLogger(__name__)
router = Router(name="user_ai")
def cancel_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("my_questions", language), callback_data=UserAIHistoryCallback(action="list").pack())],
                [InlineKeyboardButton(text=t("cancel", language), callback_data=UserAICallback(action="cancel").pack())],
            ]
        )


def finish_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("my_questions", language), callback_data=UserAIHistoryCallback(action="list").pack())],
            [InlineKeyboardButton(text=t("finish_ai", language), callback_data=UserAICallback(action="finish").pack())],
        ]
    )

MY_QUESTIONS_PAGE_SIZE = 5


def my_questions_keyboard(records: list, page: int, pages: int, language: str) -> InlineKeyboardMarkup:
    rows = []

    for record in records:
        rows.append([
            InlineKeyboardButton(
                text=f"💬 {text_preview(record.question, 45)}",
                callback_data=UserAIHistoryCallback(
                    action="open",
                    page=page,
                    item_id=record.id,
                ).pack(),
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=UserAIHistoryCallback(
                    action="list",
                    page=page - 1,
                ).pack(),
            )
        )

    if page < pages - 1:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=UserAIHistoryCallback(
                    action="list",
                    page=page + 1,
                ).pack(),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            text=t("back", language),
            callback_data=UserAICallback(action="back_to_ai").pack(),
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def leave_ai_state(state: FSMContext) -> None:
    data = await state.get_data()
    for key in ("last_question",):
        data.pop(key, None)
    await state.set_data(data)
    await state.set_state(None)


@router.callback_query(UserAICallback.filter(F.action == "ask"))
async def begin_ai(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext, ai_service: AIService | None) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)
    if not settings.ai_available or ai_service is None:
        await state.set_state(None)
        return await callback.answer(t("ai_unavailable", language), show_alert=True)
    await state.set_state(AskAI.question)
    if callback.message:
        await callback.message.answer(t("ai_prompt", language), reply_markup=cancel_keyboard(language))
    await callback.answer()


@router.callback_query(UserAICallback.filter(F.action == "cancel"))
async def cancel_ai(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await leave_ai_state(state)
    if callback.message:
        await safe_delete_message(callback.message)
    await callback.answer()


@router.callback_query(UserAICallback.filter(F.action == "finish"))
async def finish_ai(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    user = await upsert_user(session, callback.from_user)
    await leave_ai_state(state)
    await state.update_data(current_page="main", navigation_stack=[])
    await send_page(
        callback,
        session,
        "main",
        user.content_language_code,
        settings.ai_available,
    )


@router.message(AskAI.question, F.text)
async def answer_ai(message: Message, session: AsyncSession, settings: Settings, state: FSMContext, ai_service: AIService | None) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    language = normalize_language(user.content_language_code)
    question = (message.text or "").strip()
    if not question:
        return await message.answer(t("ai_text_only", language), reply_markup=finish_keyboard(language))
    if len(question) > MAX_QUESTION_LENGTH:
        return await message.answer(t("ai_too_long", language), reply_markup=finish_keyboard(language))
    if not settings.ai_available or ai_service is None:
        await state.set_state(None)
        return await message.answer(t("ai_unavailable", language))

    history = AIHistoryRepository(session)
    record = None

    try:
        async with session.begin_nested():
            record = await history.create(user_id=user.id, question=question, language=language)
    except Exception:
        logger.exception("Failed to create AI history user_id=%s", user.id)
    
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await ai_service.answer(session, user_id=user.id, question=question, language=language)
    except AIRateLimitError:
        if record:
            await history.fail(record)
            record.status = "RATE_LIMITED"
        
        await message.answer(t("ai_cooldown", language), reply_markup=finish_keyboard(language))
        return
    except (AIProviderError, TimeoutError, OSError):
        if record:
            await history.fail(record)

        await message.answer(t("ai_unavailable", language), reply_markup=finish_keyboard(language))
        return
    except Exception:
        logger.exception("Unexpected AI handler failure user_id=%s language=%s", message.from_user.id, language)
        
        if record:
            await history.fail(record)

        await message.answer(t("ai_unavailable", language), reply_markup=finish_keyboard(language))
        return
    
    if result is None:
        if record:
            await history.complete(record, None)

        await message.answer(t("ai_no_info", language), reply_markup=finish_keyboard(language))
        return
    
    if record:
        await history.complete(record, result.text)

    await message.answer(f"{html_text(result.text)}\n\n{t('ai_source', language)}", reply_markup=finish_keyboard(language))


@router.message(AskAI.question)
async def ai_text_only(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    language = normalize_language(user.content_language_code)
    await message.answer(t("ai_text_only", language), reply_markup=finish_keyboard(language))

@router.callback_query(UserAICallback.filter(F.action == "back_to_ai"))
async def back_to_ai(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)

    await state.set_state(AskAI.question)

    if callback.message:
        await safe_edit_text(
            callback.message,
            t("ai_prompt", language),
            reply_markup=cancel_keyboard(language),
        )

    await callback.answer()

@router.callback_query(UserAIHistoryCallback.filter(F.action == "list"))
async def my_questions(callback: CallbackQuery, callback_data: UserAIHistoryCallback, session: AsyncSession) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)

    repo = AIHistoryRepository(session)
    total = await repo.count_for_user(user.id)

    page, pages = paginate_count(
        total,
        callback_data.page,
        MY_QUESTIONS_PAGE_SIZE,
    )

    records = await repo.list_for_user(
        user_id=user.id,
        limit=MY_QUESTIONS_PAGE_SIZE,
        offset=page * MY_QUESTIONS_PAGE_SIZE,
    )

    text = (
        f"<b>{t('my_questions_title', language)}</b>\n\n"
        f"{t('my_questions_total', language)}: {total}\n"
        f"{t('my_questions_page', language)}: {page + 1}/{pages}\n\n"
        f"{t('my_questions_choose', language)}"
    )

    if not records:
        text += f"\n\n{t('my_questions_empty', language)}"

    if callback.message:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=my_questions_keyboard(
                records,
                page,
                pages,
                language,
            ),
        )

    await callback.answer()

@router.callback_query(UserAIHistoryCallback.filter(F.action == "open"))
async def open_my_question(callback: CallbackQuery, callback_data: UserAIHistoryCallback, session: AsyncSession) -> None:
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)

    record = await AIHistoryRepository(session).get_for_user(
        record_id=callback_data.item_id,
        user_id=user.id,
    )

    if record is None:
        await callback.answer(
            t("my_question_not_found", language),
            show_alert=True,
        )
        return

    question = html_text(
        text_preview(record.question, 1000)
    )

    if record.status == "COMPLETED":
        answer_text = record.answer or t("my_answer_empty", language)
    elif record.status == "NO_INFO":
        answer_text = t("ai_no_info", language)
    elif record.status == "RATE_LIMITED":
        answer_text = t("ai_cooldown", language)
    elif record.status == "FAILED":
        answer_text = t("ai_unavailable", language)
    else:
        answer_text = t("ai_processing", language)

    answer = html_text(text_preview(answer_text, 2500))

    text = (
        f"<b>{t('my_question_title', language)}</b>\n\n"
        f"❓ <b>{t('my_question_label', language)}:</b>\n"
        f"{question}\n\n"
        f"🤖 <b>{t('my_answer_label', language)}:</b>\n"
        f"{answer}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("my_questions_back", language),
                    callback_data=UserAIHistoryCallback(
                        action="list",
                        page=callback_data.page,
                    ).pack(),
                )
            ]
        ]
    )

    if callback.message:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=keyboard,
        )

    await callback.answer()