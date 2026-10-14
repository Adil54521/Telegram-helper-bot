import logging
from datetime import date

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminAICallback, AdminMenuCallback
from app.config import Settings
from app.database.models.entities import TelegramUser
from app.database.repositories.ai_history import AIHistoryRepository
from app.filters.admin import HasPermission
from app.services.permissions import Permission, PermissionService
from app.services.ai_history_csv import generate_ai_history_csv, AIHistoryExportError
from app.utils.pagination import paginate_count
from app.utils.presentation import html_text, text_preview
from app.utils.telegram import safe_edit_text

router = Router(name="admin_ai_history")
logger = logging.getLogger(__name__)

router.callback_query.filter(
    HasPermission(Permission.AI_HISTORY_VIEW)
)

HISTORY_PAGE_SIZE = 8
HISTORY_FILTERS = {
    "all": None,
    "ok": "COMPLETED",
    "none": "NO_INFO",
    "fail": "FAILED",
}

def history_keyboard(records: list, page: int, pages: int, filter_key: str = "all", can_export: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if filter_key == key else "") + label,
                callback_data=AdminAICallback(
                    action=f"h_{key}_0"
                ).pack(),
            )
            for key, label in [
                ("all", "Все"),
                ("ok", "Ответы"),
            ]
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if filter_key == key else "") + label,
                callback_data=AdminAICallback(
                    action=f"h_{key}_0"
                ).pack(),
            )
            for key, label in [
                ("none", "Нет информации"),
                ("fail", "Ошибки"),
            ]
        ],
    ]

    for record in records:
        rows.append([
            InlineKeyboardButton(
                text=f"💬 #{record.id} {text_preview(record.question, 35)}",
                callback_data=AdminAICallback(
                    action=f"hist_{filter_key}_{page}_{record.id}"
                ).pack(),
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=AdminAICallback(
                    action=f"h_{filter_key}_{page - 1}"
                ).pack(),
            )
        )

    if page < pages - 1:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminAICallback(
                    action=f"h_{filter_key}_{page + 1}"
                ).pack(),
            )
        )

    if navigation:
        rows.append(navigation)

    if can_export:
        rows.append([
            InlineKeyboardButton(
                text="📥 Выгрузить CSV",
                callback_data=AdminAICallback(
                    action=f"csv_{filter_key}"
                ).pack(),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ ИИ-ассистент",
            callback_data=AdminMenuCallback(
                section="ai"
            ).pack(),
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(AdminAICallback.filter((F.action == "history") | F.action.startswith("h_")))
async def show_ai_history(callback: CallbackQuery, callback_data: AdminAICallback, session: AsyncSession, settings: Settings) -> None:
    can_export = await PermissionService(session, settings).has(callback.from_user.id, Permission.AI_HISTORY_EXPORT)
    action = callback_data.action

    filter_key = "all"
    page = 0

    # Например: h_ok_2
    # ok — успешные ответы, 2 — номер страницы.
    if action.startswith("h_"):
        try:
            _, filter_key, page_text = action.split("_", 2)
            page = max(0, int(page_text))
        except ValueError:
            await callback.answer(
                "Неверный формат фильтра",
                show_alert=True,
            )
            return

    if filter_key not in HISTORY_FILTERS:
        await callback.answer(
            "Неизвестный фильтр",
            show_alert=True,
        )
        return

    status = HISTORY_FILTERS[filter_key]

    repo = AIHistoryRepository(session)

    # Считаем количество записей только выбранного статуса.
    total = await repo.count(status=status)

    page, pages = paginate_count(total, page, HISTORY_PAGE_SIZE)

    records = await repo.list_recent(
        limit=HISTORY_PAGE_SIZE,
        offset=page * HISTORY_PAGE_SIZE,
        status=status,
    )

    filter_labels = {
        "all": "Все вопросы",
        "ok": "Успешные ответы",
        "none": "Без информации",
        "fail": "Ошибки",
    }

    text = (
        "📜 <b>История вопросов ИИ</b>\n\n"
        f"🔎 Фильтр: {filter_labels[filter_key]}\n"
        f"📊 Найдено: {total}\n"
        f"📄 Страница: {page + 1} из {pages}\n\n"
        "Выберите вопрос для просмотра."
    )

    if not records:
        text += "\n\nИстория по выбранному фильтру пуста."

    if callback.message:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=history_keyboard(
                records,
                page,
                pages,
                filter_key,
                can_export
            ),
        )

    await callback.answer()

@router.callback_query(AdminAICallback.filter(F.action.startswith("hist_")))
async def show_ai_history_detail(callback: CallbackQuery, callback_data: AdminAICallback, session: AsyncSession) -> None:
    try:
        _, filter_key, page_text, record_text = (
            callback_data.action.split("_", 3)
        )

        if filter_key not in HISTORY_FILTERS:
            raise ValueError("Invalid filter")

        page = max(0, int(page_text))
        record_id = int(record_text)

    except ValueError:
        await callback.answer(
            "Некорректные параметры",
            show_alert=True,
        )
        return

    record = await AIHistoryRepository(session).get_by_id(record_id)

    if record is None:
        await callback.answer(
            "Запись не найдена",
            show_alert=True,
        )
        return

    user = await session.get(TelegramUser, record.user_id)
    
    if user:
        full_name = " ".join(
            part for part in (user.first_name, user.last_name)
            if part
        )

        username = (
            f"@{html_text(user.username)}"
            if user.username
            else "Не указан"
        )

        telegram_id = str(user.telegram_id)
        full_name = html_text(full_name) or "Не указано"
    else:
        username = "Не указан"
        telegram_id = "Неизвестен"
        full_name = "Не указано"

    question = html_text(text_preview(record.question, 1200))
    answer = html_text(text_preview(record.answer or "Нет ответа", 2200))

    text = (
        f"📜 <b>Вопрос #{record.id}</b>\n\n"
        f"👤 ФИО: {full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 Telegram ID: <code>{telegram_id}</code>\n"
        f"🔑 ID в базе: {record.user_id}\n"
        f"🌐 Язык: {html_text(record.language_code.upper())}\n"
        f"📌 Статус: {html_text(record.status)}\n"
        f"🕒 Дата: {record.created_at:%d.%m.%Y %H:%M}\n\n"
        f"❓ <b>Вопрос:</b>\n{question}\n\n"
        f"🤖 <b>Ответ:</b>\n{answer}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К истории",callback_data=AdminAICallback(action=f"h_{filter_key}_{page}").pack())]])

    if callback.message:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=keyboard,
        )

    await callback.answer()

@router.callback_query(AdminAICallback.filter(F.action.startswith("csv_")))
async def export_ai_history(callback: CallbackQuery, callback_data: AdminAICallback, session: AsyncSession, settings: Settings) -> None:
    permissions = PermissionService(session, settings)

    if not await permissions.has(
        callback.from_user.id,
        Permission.AI_HISTORY_EXPORT,
    ):
        await callback.answer(
            "Нет прав на экспорт истории ИИ",
            show_alert=True,
        )
        return

    filter_key = callback_data.action.removeprefix("csv_")

    if filter_key not in HISTORY_FILTERS:
        await callback.answer(
            "Неизвестный фильтр",
            show_alert=True,
        )
        return

    try:
        content = await generate_ai_history_csv(
            session,
            status=HISTORY_FILTERS[filter_key],
        )
    except AIHistoryExportError as error:
        await callback.answer(
            str(error)[:200],
            show_alert=True,
        )
        return
    except Exception:
        logger.exception(
            "AI history CSV export failed admin_id=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "Не удалось сформировать CSV.",
            show_alert=True,
        )
        return

    await callback.message.answer_document(
        BufferedInputFile(
            content,
            filename=(
                f"ai_history_{filter_key}_"
                f"{date.today().isoformat()}.csv"
            ),
        )
    )

    await callback.answer()