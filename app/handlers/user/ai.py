import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import AIProviderError
from app.ai.service import AIRateLimitError, AIService, MAX_QUESTION_LENGTH
from app.callbacks import UserAICallback, UserNavigationCallback
from app.config import Settings
from app.database.repositories.users import upsert_user
from app.states.user import AskAI
from app.utils.i18n import normalize_language, t
from app.utils.presentation import html_text

logger = logging.getLogger(__name__)
router = Router(name="user_ai")
def cancel_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("cancel", language), callback_data=UserAICallback(action="cancel").pack())]])


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
    user = await upsert_user(session, callback.from_user)
    language = normalize_language(user.content_language_code)
    await state.set_state(None)
    if callback.message:
        await callback.message.answer(t("main_menu", language), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("main_menu", language), callback_data=UserNavigationCallback(action="home").pack())]]))
    await callback.answer()


@router.message(AskAI.question, F.text)
async def answer_ai(message: Message, session: AsyncSession, settings: Settings, state: FSMContext, ai_service: AIService | None) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    language = normalize_language(user.content_language_code)
    question = (message.text or "").strip()
    if not question:
        return await message.answer(t("ai_text_only", language), reply_markup=cancel_keyboard(language))
    if len(question) > MAX_QUESTION_LENGTH:
        return await message.answer(t("ai_too_long", language), reply_markup=cancel_keyboard(language))
    if not settings.ai_available or ai_service is None:
        await state.set_state(None)
        return await message.answer(t("ai_unavailable", language))
    await state.set_state(None)
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await ai_service.answer(session, user_id=message.from_user.id, question=question, language=language)
    except AIRateLimitError:
        await message.answer(t("ai_cooldown", language))
        return
    except (AIProviderError, TimeoutError, OSError):
        await message.answer(t("ai_unavailable", language))
        return
    except Exception:
        logger.exception("Unexpected AI handler failure user_id=%s language=%s", message.from_user.id, language)
        await message.answer(t("ai_unavailable", language))
        return
    if result is None:
        await message.answer(t("ai_no_info", language))
        return
    await message.answer(f"{html_text(result.text)}\n\n{t('ai_source', language)}")


@router.message(AskAI.question)
async def ai_text_only(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    language = normalize_language(user.content_language_code)
    await message.answer(t("ai_text_only", language), reply_markup=cancel_keyboard(language))
