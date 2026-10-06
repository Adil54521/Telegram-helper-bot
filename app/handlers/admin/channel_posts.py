import logging
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIService
from app.callbacks import AdminMenuCallback, ChannelPostCallback
from app.config import Settings
from app.database.models import TelegramContentPost
from app.database.models.enums import MediaType, TelegramContentStatus
from app.database.repositories.channel_posts import TelegramContentRepository
from app.database.repositories.users import upsert_user
from app.filters.admin import HasPermission
from app.services.channel_import import MAX_TRANSFER_DOCUMENT_SIZE, TelegramContentImportService, source_text, suggested_title
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
)


def cb(action: str, item_id: int = 0, page: int = 0, filter_: str = "NEW") -> str:
    return ChannelPostCallback(action=action, item_id=item_id, page=page, status=filter_).pack()


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def post_card(post: TelegramContentPost, page: int, filter_: str) -> tuple[str, InlineKeyboardMarkup]:
    value = source_text(post)
    when = post.telegram_created_at or post.created_at
    local = when.astimezone(ZoneInfo("Asia/Qyzylorda")) if when else None
    oversized = sum(1 for item in post.media if item.type == MediaType.DOCUMENT and item.file_size and item.file_size > MAX_TRANSFER_DOCUMENT_SIZE)
    warning = f"\n⚠️ Слишком больших документов: {oversized}" if oversized else ""
    text = (
        "📥 <b>Telegram-публикация</b>\n\n"
        f"📅 {local.strftime('%d.%m.%Y %H:%M') if local else '—'}\n\n"
        + (f"🧵 Topic/thread: {post.message_thread_id}\n\n" if post.message_thread_id is not None else "")
        +
        f"📝 <b>Текст:</b>\n{html_text(text_preview(value, 900)) if value else '—'}\n\n"
        f"📎 Вложений: {len(post.media)}{warning}\n\n"
        f"Статус: {status_label(post.status)}"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if post.status == TelegramContentStatus.NEW:
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


@router.callback_query(ChannelPostCallback.filter(F.action == "open"))
async def open_post(callback: CallbackQuery, callback_data: ChannelPostCallback, session: AsyncSession, settings: Settings) -> None:
    post = await load_post(callback_data.item_id, session, settings)
    if not post:
        await callback.answer("Публикация не найдена", show_alert=True)
        return
    text, markup = post_card(post, callback_data.page, callback_data.status)
    if callback.message:
        await safe_edit_text(callback.message, text, reply_markup=markup)
    await callback.answer()


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
    title = suggested_title(value)
    await remember_wizard(state, callback.message)
    common = {"channel_post_id": post.id, "channel_page": callback_data.page, "channel_filter": callback_data.status, "channel_type": kind, "channel_media_count": len(post.media), "channel_reason": None, "channel_input_truncated": False}
    if kind == "PAGE":
        common.update(channel_title_ru=title, channel_text_ru=value, channel_title_kk=None, channel_text_kk=None)
    else:
        common.update(channel_question_ru=title, channel_answer_ru=value, channel_question_kk=None, channel_answer_kk=None)
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
            await service.create_page(item_id, settings.content_chat_id, callback.from_user.id, title_ru=data["channel_title_ru"], text_ru=data["channel_text_ru"], title_kk=data.get("channel_title_kk"), text_kk=data.get("channel_text_kk"))
        else:
            await service.create_faq(item_id, settings.content_chat_id, callback.from_user.id, question_ru=data["channel_question_ru"], answer_ru=data["channel_answer_ru"], question_kk=data.get("channel_question_kk"), answer_kk=data.get("channel_answer_kk"))
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
        text, markup = post_card(post, page, filter_)
        await safe_edit_text(callback.message, text, reply_markup=markup)
    await callback.answer()
