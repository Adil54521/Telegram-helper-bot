import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.knowledge import MAX_CONTEXT_CHARS, MAX_RESULTS, KnowledgeService
from app.ai.service import AIService, MAX_QUESTION_LENGTH
from app.callbacks import AdminAICallback, AdminMenuCallback
from app.config import Settings
from app.database.models import BotPage, FAQItem
from app.database.models.enums import PageStatus
from app.services.permissions import Permission, PermissionService
from app.states.admin import TestAIRetrieval
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text

logger = logging.getLogger(__name__)
router = Router(name="admin_ai")
def ai_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Проверить Gemini", callback_data=AdminAICallback(action="health").pack())],
            [InlineKeyboardButton(text="🔎 Проверить поиск", callback_data=AdminAICallback(action="search").pack())],
            [InlineKeyboardButton(text="⬅️ Панель", callback_data=AdminMenuCallback(section="dashboard").pack())],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ ИИ-ассистент", callback_data=AdminMenuCallback(section="ai").pack())]
        ]
    )


async def allowed(user_id: int, session: AsyncSession, settings: Settings) -> bool:
    return await PermissionService(session, settings).has(user_id, Permission.STATISTICS)


async def panel_text(session: AsyncSession, settings: Settings) -> str:
    pages = await session.scalar(
        select(func.count(BotPage.id)).where(
            BotPage.is_active.is_(True),
            BotPage.status == PageStatus.PUBLISHED,
            BotPage.published_revision_id.is_not(None),
        )
    ) or 0
    faqs = await session.scalar(
        select(func.count(FAQItem.id)).where(FAQItem.is_active.is_(True))
    ) or 0
    status = "🟢 Статус: включён" if settings.ai_available else "🔴 Статус: выключен"
    model = html_text(settings.gemini_model) if settings.gemini_model else "—"
    return (
        "🤖 <b>ИИ-ассистент</b>\n\n"
        f"{status}\n"
        "🧠 Провайдер: Google Gemini\n"
        f"⚡ Модель: {model}\n\n"
        "📚 <b>База знаний</b>\n"
        f"📄 Опубликованные страницы: {pages}\n"
        f"❓ Активные FAQ: {faqs}\n\n"
        "⚙️ <b>Параметры</b>\n"
        f"Источников на запрос: {MAX_RESULTS}\n"
        f"Макс. контекст: {MAX_CONTEXT_CHARS} символов\n"
        f"Cooldown: {settings.ai_cooldown_seconds:g} сек.\n"
        f"Макс. вопрос: {MAX_QUESTION_LENGTH} символов"
    )


@router.callback_query(AdminMenuCallback.filter(F.section == "ai"))
async def show_panel(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.clear()
    if callback.message:
        await safe_edit_text(
            callback.message,
            await panel_text(session, settings),
            reply_markup=ai_keyboard(),
        )
    await callback.answer()


@router.callback_query(AdminAICallback.filter(F.action == "health"))
async def health_check(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    ai_service: AIService | None,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    if not settings.ai_available or ai_service is None:
        await callback.answer("Gemini не настроен или выключен", show_alert=True)
        return
    try:
        duration_ms = await ai_service.health_check()
    except Exception:
        logger.exception(
            "Gemini health check failed admin_id=%s provider=gemini model=%s",
            callback.from_user.id,
            settings.gemini_model,
        )
        if callback.message:
            await safe_edit_text(
                callback.message,
                "🧪 <b>Gemini</b>\n\n❌ Gemini недоступен",
                reply_markup=back_keyboard(),
            )
        await callback.answer()
        return
    if callback.message:
        await safe_edit_text(
            callback.message,
            "🧪 <b>Gemini</b>\n\n"
            "✅ Соединение работает\n"
            f"Модель: {html_text(settings.gemini_model)}\n"
            f"Время: {duration_ms} ms",
            reply_markup=back_keyboard(),
        )
    await callback.answer()


@router.callback_query(AdminAICallback.filter(F.action == "search"))
async def begin_search(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(TestAIRetrieval.query)
    if callback.message:
        await callback.message.answer(
            "🔎 <b>Проверка поиска</b>\n\nВведите тестовый запрос:",
            reply_markup=back_keyboard(),
        )
    await callback.answer()


@router.message(TestAIRetrieval.query, F.text)
async def run_search(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not message.from_user or not await allowed(message.from_user.id, session, settings):
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите непустой текстовый запрос.")
        return
    items = await KnowledgeService().search(session, query, "ru")
    await state.clear()
    lines = ["🔎 <b>Результаты поиска</b>", "", f"Найдено: {len(items)}", ""]
    for index, item in enumerate(items, 1):
        icon = "📄" if item.kind == "page" else "❓"
        lines.append(
            f"{index}. {icon} {html_text(text_preview(item.title, 80))} — score {item.score}"
        )
    if not items:
        lines.append("Совпадений не найдено.")
    lines.extend(["", "Gemini не вызывался."])
    await message.answer("\n".join(lines), reply_markup=back_keyboard())


@router.message(TestAIRetrieval.query)
async def search_text_only(message: Message) -> None:
    await message.answer("Отправьте тестовый запрос текстом.")
