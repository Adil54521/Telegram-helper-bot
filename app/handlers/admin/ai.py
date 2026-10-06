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
from app.database.models import AIKnowledgeChunk, AuditLog, BotPage, FAQItem
from app.database.models.enums import PageStatus
from app.database.repositories.users import upsert_user
from app.services.ai_analytics import AIAnalyticsPeriod, AIAnalyticsService
from app.services.permissions import Permission, PermissionService
from app.states.admin import TestAIRetrieval
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text
from app.filters.admin import HasPermission

logger = logging.getLogger(__name__)
router = Router(name="admin_ai")
router.callback_query.filter(HasPermission(Permission.STATISTICS))
router.message.filter(HasPermission(Permission.STATISTICS))
def ai_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Проверить Gemini", callback_data=AdminAICallback(action="health").pack())],
            [InlineKeyboardButton(text="🔎 Проверить поиск", callback_data=AdminAICallback(action="search").pack())],
            [InlineKeyboardButton(text="📚 Индекс знаний", callback_data=AdminAICallback(action="index").pack())],
            [InlineKeyboardButton(text="📊 Аналитика", callback_data=AdminAICallback(action="analytics_today").pack())],
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
    user = await upsert_user(session, callback.from_user)
    try:
        duration_ms = await ai_service.health_check(session, user_id=user.id)
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


def analytics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data=AdminAICallback(action="analytics_today").pack()),
                InlineKeyboardButton(text="7 дней", callback_data=AdminAICallback(action="analytics_7d").pack()),
            ],
            [
                InlineKeyboardButton(text="30 дней", callback_data=AdminAICallback(action="analytics_30d").pack()),
                InlineKeyboardButton(text="Всё время", callback_data=AdminAICallback(action="analytics_all").pack()),
            ],
            [InlineKeyboardButton(text="⬅️ ИИ-ассистент", callback_data=AdminMenuCallback(section="ai").pack())],
        ]
    )


def money(value) -> str:
    rendered = f"{value:.10f}".rstrip("0").rstrip(".")
    return rendered if "." in rendered else f"{rendered}.0"


@router.callback_query(AdminAICallback.filter(F.action.startswith("analytics_")))
async def analytics(
    callback: CallbackQuery,
    callback_data: AdminAICallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    periods = {
        "analytics_today": (AIAnalyticsPeriod.TODAY, "Сегодня"),
        "analytics_7d": (AIAnalyticsPeriod.SEVEN_DAYS, "7 дней"),
        "analytics_30d": (AIAnalyticsPeriod.THIRTY_DAYS, "30 дней"),
        "analytics_all": (AIAnalyticsPeriod.ALL, "Всё время"),
    }
    period, label = periods.get(
        callback_data.action,
        (AIAnalyticsPeriod.TODAY, "Сегодня"),
    )
    summary = await AIAnalyticsService().summary(session, period)
    text = (
        "📊 <b>Аналитика ИИ</b>\n"
        f"Период: {label}\n\n"
        f"👥 Пользователей: {summary.users:,}\n"
        f"💬 Вопросов: {summary.questions:,}\n"
        f"🤖 Gemini-вызовов: {summary.gemini_calls:,}\n"
        f"🔎 Без результата: {summary.no_results:,}\n\n"
        "⚡ <b>Надёжность</b>\n"
        f"✅ Успешно: {summary.successful:,}\n"
        f"❌ Ошибок: {summary.errors:,}\n"
        f"⏱ Среднее время: {summary.average_duration_ms / 1000:.2f} сек.\n\n"
        "📚 <b>Retrieval</b>\n"
        f"Среднее источников: {summary.average_retrieval:.1f}\n"
        f"Без результатов: {summary.no_results_percent:.1f}%\n\n"
        "🪙 <b>Токены</b>\n"
        f"📥 Input: {summary.input_tokens:,}\n"
        f"📤 Output: {summary.output_tokens:,}\n"
        f"Σ Всего: {summary.total_tokens:,}\n\n"
        "💰 <b>Расчётная стоимость</b>\n"
        f"📥 Input: ${money(summary.input_cost_usd)}\n"
        f"📤 Output: ${money(summary.output_cost_usd)}\n"
        f"Σ Всего: ${money(summary.total_cost_usd)}\n\n"
        f"Средняя / Gemini-вызов: ${money(summary.average_cost_per_call)}"
    )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=analytics_keyboard())
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
    ai_service: AIService | None,
) -> None:
    if not message.from_user or not await allowed(message.from_user.id, session, settings):
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите непустой текстовый запрос.")
        return
    knowledge = ai_service.knowledge if ai_service else KnowledgeService()
    items, mode = await knowledge.search_with_mode(session, query, "ru")
    await state.clear()
    lines = ["🔎 <b>Результаты поиска</b>", "", f"Режим: {html_text(mode)}", f"Найдено: {len(items)}", ""]
    for index, item in enumerate(items, 1):
        icon = "📄" if item.kind == "page" else "❓"
        lines.append(
            f"{index}. {icon} {html_text(text_preview(item.title, 80))} — score {item.score:.3f}"
        )
    if not items:
        lines.append("Совпадений не найдено.")
    lines.extend(["", "Gemini не вызывался."])
    await message.answer("\n".join(lines), reply_markup=back_keyboard())


def index_keyboard(confirm: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if confirm:
        rows.append([InlineKeyboardButton(text="🔴 Да, синхронизировать", callback_data=AdminAICallback(action="index_sync").pack())])
    else:
        rows.append([InlineKeyboardButton(text="🔄 Синхронизировать", callback_data=AdminAICallback(action="index_preview").pack())])
    rows.append([InlineKeyboardButton(text="⬅️ ИИ-ассистент", callback_data=AdminMenuCallback(section="ai").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(AdminAICallback.filter(F.action.in_({"index", "index_preview"})))
async def knowledge_index(
    callback: CallbackQuery,
    callback_data: AdminAICallback,
    session: AsyncSession,
    settings: Settings,
    ai_service: AIService | None,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    chunks = await session.scalar(select(func.count(AIKnowledgeChunk.id))) or 0
    ru = await session.scalar(select(func.count(AIKnowledgeChunk.id)).where(AIKnowledgeChunk.language_code == "ru")) or 0
    kk = await session.scalar(select(func.count(AIKnowledgeChunk.id)).where(AIKnowledgeChunk.language_code == "kk")) or 0
    last_sync = await session.scalar(select(func.max(AIKnowledgeChunk.updated_at)))
    preview = None
    if callback_data.action == "index_preview" and ai_service and ai_service.knowledge_index:
        preview = await ai_service.knowledge_index.preview(session)
    text = (
        "📚 <b>Индекс знаний</b>\n\n"
        f"Режим поиска: {'hybrid' if settings.ai_semantic_search_enabled else 'lexical'}\n"
        f"Модель: {html_text(settings.ai_embedding_model)}\n"
        f"Размерность: {settings.ai_embedding_dimension}\n\n"
        f"Всего чанков: {chunks}\n🇷🇺 RU: {ru}\n🇰🇿 KK: {kk}\n"
        f"Последняя синхронизация: {last_sync.strftime('%Y-%m-%d %H:%M UTC') if last_sync else '—'}"
    )
    if preview:
        text += (
            "\n\n⚠️ <b>Подтвердите синхронизацию</b>\n"
            f"Новых/изменённых: {preview.created_or_updated}\n"
            f"Без изменений: {preview.unchanged}\nУдалить устаревших: {preview.deleted}\n\n"
            "Для новых и изменённых чанков будет вызван Gemini Embeddings."
        )
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=index_keyboard(bool(preview)))
    await callback.answer()


@router.callback_query(AdminAICallback.filter(F.action == "index_sync"))
async def sync_knowledge_index(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    ai_service: AIService | None,
) -> None:
    if not await allowed(callback.from_user.id, session, settings):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    if not ai_service or not ai_service.knowledge_index:
        await callback.answer("Gemini Embeddings не настроен", show_alert=True)
        return
    await callback.answer("Синхронизация запущена")
    try:
        result = await ai_service.knowledge_index.sync_all(session)
    except Exception as error:
        logger.warning("Knowledge index sync failed admin_id=%s error=%s", callback.from_user.id, type(error).__name__)
        if callback.message:
            await safe_edit_text(callback.message, "📚 <b>Индекс знаний</b>\n\n❌ Синхронизация не выполнена.", reply_markup=index_keyboard())
        return
    session.add(AuditLog(
        admin_id=callback.from_user.id,
        action="AI_KNOWLEDGE_INDEX_SYNCED",
        entity_type="AIKnowledgeChunk",
        entity_id="all",
        new_data={"total": result.total, "updated": result.created_or_updated, "deleted": result.deleted},
    ))
    if callback.message:
        await safe_edit_text(
            callback.message,
            "📚 <b>Индекс знаний</b>\n\n✅ Синхронизация завершена\n"
            f"Всего: {result.total}\nОбновлено: {result.created_or_updated}\n"
            f"Без изменений: {result.unchanged}\nУдалено: {result.deleted}",
            reply_markup=index_keyboard(),
        )


@router.message(TestAIRetrieval.query)
async def search_text_only(message: Message) -> None:
    await message.answer("Отправьте тестовый запрос текстом.")
