import io
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback, FAQCallback
from app.config import Settings
from app.ai.service import AIService
from app.database.repositories.cms import CMSRepository
from app.database.repositories.users import upsert_user
from app.database.models.enums import LanguageCode
from app.services.cms import FAQService
from app.services.permissions import Permission, PermissionService
from app.states.admin import AddFAQ, EditFAQ, ImportFAQ
from app.services.faq_csv import FAQCSVRow, export_faq_csv, faq_template_csv, normalize_question, parse_faq_csv
from app.utils.telegram import safe_edit_text
from app.utils.wizard import consume_input, edit_wizard, finish_wizard, remember_wizard
from app.utils.presentation import html_text, text_preview
from app.filters.admin import HasPermission

router=Router(name="admin_faq"); SIZE=8
router.callback_query.filter(HasPermission(Permission.FAQ))
router.message.filter(HasPermission(Permission.FAQ))
IMPORT_KEYS=("import_rows","import_duplicates","import_errors","import_total","translation_faq_ids","ai_faq_translations","ai_edit_id","ai_edit_field","ai_edit_page")
def kb(r):return InlineKeyboardMarkup(inline_keyboard=r)
async def ok(uid,s,c):return await PermissionService(s,c).has(uid,Permission.FAQ)
async def guard(cb,s,c):
    if await ok(cb.from_user.id,s,c):return True
    await cb.answer("Доступ запрещён",show_alert=True);return False

async def listing(cb,session,page=0):
    items=await CMSRepository(session).faqs(); pages=max(1,(len(items)+SIZE-1)//SIZE);page=max(0,min(page,pages-1));chunk=items[page*SIZE:(page+1)*SIZE]
    active_count=sum(1 for item in items if item.is_active)
    rows=[[InlineKeyboardButton(text=f"{x.position+1}. {'🔴 ' if not x.is_active else ''}{text_preview(x.question,40)}",callback_data=FAQCallback(action="open",item_id=x.id,page=page).pack())] for x in chunk]
    if pages>1:rows.append([InlineKeyboardButton(text="⬅️",callback_data=FAQCallback(action="list",page=max(0,page-1)).pack()),InlineKeyboardButton(text=f"{page+1} / {pages}",callback_data="noop"),InlineKeyboardButton(text="➡️",callback_data=FAQCallback(action="list",page=min(pages-1,page+1)).pack())])
    rows += [[InlineKeyboardButton(text="➕ Добавить вопрос",callback_data=FAQCallback(action="add").pack())],[InlineKeyboardButton(text="📥 Импорт CSV",callback_data=FAQCallback(action="import").pack()),InlineKeyboardButton(text="📤 Экспорт CSV",callback_data=FAQCallback(action="export").pack())],[InlineKeyboardButton(text="📄 Скачать шаблон",callback_data=FAQCallback(action="template").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]]
    if cb.message:await safe_edit_text(cb.message,f"❓ <b>FAQ</b>\n\nВсего вопросов: <b>{len(items)}</b>\n🟢 Активных: <b>{active_count}</b>\n🔴 Выключено: <b>{len(items)-active_count}</b>\n\nВыберите вопрос:",reply_markup=kb(rows));await cb.answer()

@router.callback_query(AdminMenuCallback.filter(F.section=="faq"))
async def menu(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,session)
@router.callback_query(FAQCallback.filter(F.action=="list"))
async def list_(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,session,callback_data.page)
@router.callback_query(FAQCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).faq(callback_data.item_id)
    if not x:return await cb.answer("FAQ не найден",show_alert=True)
    card,markup=faq_card(x,callback_data.page)
    if cb.message:await safe_edit_text(cb.message,card,reply_markup=markup);await cb.answer()

def faq_card(x,page=0):
    rows=[[InlineKeyboardButton(text="🇷🇺 Редактировать RU",callback_data=FAQCallback(action="edit_q_ru",item_id=x.id,page=page).pack()),InlineKeyboardButton(text="🇰🇿 Редактировать KK",callback_data=FAQCallback(action="edit_q_kk",item_id=x.id,page=page).pack())],[InlineKeyboardButton(text="🇷🇺 Изменить ответ",callback_data=FAQCallback(action="edit_a_ru",item_id=x.id,page=page).pack()),InlineKeyboardButton(text="🇰🇿 Жауапты өзгерту",callback_data=FAQCallback(action="edit_a_kk",item_id=x.id,page=page).pack())],[InlineKeyboardButton(text="⬆️ Выше",callback_data=FAQCallback(action="up",item_id=x.id,page=page).pack()),InlineKeyboardButton(text="⬇️ Ниже",callback_data=FAQCallback(action="down",item_id=x.id,page=page).pack())],[InlineKeyboardButton(text="🔴 Выключить" if x.is_active else "🟢 Включить",callback_data=FAQCallback(action="toggle",item_id=x.id,page=page).pack())],[InlineKeyboardButton(text="🗑 Удалить",callback_data=FAQCallback(action="delete_ask",item_id=x.id,page=page).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="list",page=page).pack())]]
    ru=next((v for v in x.translations if v.language_code==LanguageCode.RU),None);kk=next((v for v in x.translations if v.language_code==LanguageCode.KK),None)
    ru_q,ru_a=(ru.question,ru.answer) if ru else (x.question,x.answer)
    if not kk:
        rows.insert(2, [InlineKeyboardButton(text="✨ Создать перевод KK", callback_data=FAQCallback(action="ai_one", item_id=x.id,page=page).pack())])
    kk_block=(f"<b>Сұрақ:</b>\n{html_text(kk.question)}\n\n<b>Жауап:</b>\n{html_text(text_preview(kk.answer))}" if kk else "Перевод отсутствует")
    card=(f"❓ <b>FAQ</b>\n\n{'🟢 Активен' if x.is_active else '🔴 Выключен'}\n\n🇷🇺 <b>Русский</b>\n\n<b>Вопрос:</b>\n{html_text(ru_q)}\n\n<b>Ответ:</b>\n{html_text(text_preview(ru_a))}\n\n🇰🇿 <b>Қазақша{' ⚠️' if not kk else ''}</b>\n\n{kk_block}\n\n📍 Позиция: {x.position+1}")
    return card,kb(rows)


@router.callback_query(FAQCallback.filter(F.action == "template"))
async def download_template(cb: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await guard(cb, session, settings): return
    await cb.message.answer_document(BufferedInputFile(faq_template_csv(), filename="faq_template.csv"))
    await cb.answer()


@router.callback_query(FAQCallback.filter(F.action == "export"))
async def export_csv(cb: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await guard(cb, session, settings): return
    content = export_faq_csv(await CMSRepository(session).faqs())
    await cb.message.answer_document(BufferedInputFile(content, filename=f"faq_export_{date.today().isoformat()}.csv"))
    await cb.answer()


@router.callback_query(FAQCallback.filter(F.action == "import"))
async def begin_import(cb: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await guard(cb, session, settings): return
    await state.set_state(ImportFAQ.file)
    await remember_wizard(state,cb.message)

    if cb.message:
        await safe_edit_text(
            cb.message,
            "📥 <b>Импорт FAQ</b>\n\n"
            "Отправьте CSV-файл.\n\n"
            "<b>Обязательные столбцы:</b>\n"
            "<code>question_ru</code>\n"
            "<code>answer_ru</code>\n\n"
            "<b>Дополнительно:</b>\n"
            "<code>question_kk</code>\n"
            "<code>answer_kk</code>\n"
            "<code>is_active</code>",
            reply_markup=kb(
                [
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data=FAQCallback(
                                action="import_cancel",
                            ).pack(),
                        )
                    ]
                ]
            ),
        )

    await cb.answer()


@router.message(ImportFAQ.file)
async def receive_import(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not message.from_user or not await ok(message.from_user.id, session, settings):
        return
    document = message.document
    if not document or not (document.file_name or "").casefold().endswith(".csv"):
        await consume_input(message);return await import_error(message,state,"Нужен документ с расширением .csv.")
    if document.file_size and document.file_size > 1_000_000:
        await consume_input(message);return await import_error(message,state,"CSV-файл слишком большой (максимум 1 МБ).")
    stream = io.BytesIO()
    await message.bot.download(document, destination=stream)
    try:
        preview = parse_faq_csv(stream.getvalue(), await CMSRepository(session).faq_questions())
    except ValueError as error:
        await consume_input(message);return await import_error(message,state,str(error))
    await consume_input(message)
    await state.update_data(
        import_rows=[row.to_fsm() for row in preview.new_rows],
        import_duplicates=[row.to_fsm() for row in preview.duplicates],
        import_errors=[error.to_fsm() for error in preview.errors],
        import_total=preview.total_rows,
    )
    await state.set_state(ImportFAQ.preview)
    with_kk = sum(1 for row in preview.new_rows if row.question_kk and row.answer_kk)
    rows = []
    if preview.errors: rows.append([InlineKeyboardButton(text="👁 Ошибки", callback_data=FAQCallback(action="import_errors").pack())])
    if preview.duplicates: rows.append([InlineKeyboardButton(text="♻️ Дубликаты", callback_data=FAQCallback(action="import_duplicates").pack())])
    if preview.new_rows: rows.append([InlineKeyboardButton(text=f"✅ Импортировать {len(preview.new_rows)}", callback_data=FAQCallback(action="import_confirm").pack())])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=FAQCallback(action="import_cancel").pack())])
    await edit_wizard(message.bot,state,f"📥 <b>Проверка FAQ CSV</b>\n\nВсего строк: <b>{preview.total_rows}</b>\n✅ Новых: <b>{len(preview.new_rows)}</b>\n♻️ Дубликатов: <b>{len(preview.duplicates)}</b>\n❌ Ошибок: <b>{len(preview.errors)}</b>\n\n🇷🇺 RU: <b>{len(preview.new_rows)}</b>\n🇰🇿 С KK: <b>{with_kk}</b>\n⚠️ Без KK: <b>{len(preview.new_rows)-with_kk}</b>",kb(rows))

async def import_error(message:Message,state:FSMContext,reason:str)->None:
    await state.set_state(ImportFAQ.file)
    await edit_wizard(message.bot,state,f"⚠️ <b>Не удалось обработать CSV</b>\n\nПричина:\n{html_text(reason)}\n\nОтправьте исправленный CSV-файл.",kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="import_cancel").pack())]]))

async def show_import_preview(cb:CallbackQuery,state:FSMContext)->None:
    data=await state.get_data();new=data.get("import_rows",[]);duplicates=data.get("import_duplicates",[]);errors=data.get("import_errors",[]);with_kk=sum(1 for row in new if row.get("question_kk") and row.get("answer_kk"))
    rows=[]
    if errors:rows.append([InlineKeyboardButton(text="👁 Ошибки",callback_data=FAQCallback(action="import_errors").pack())])
    if duplicates:rows.append([InlineKeyboardButton(text="♻️ Дубликаты",callback_data=FAQCallback(action="import_duplicates").pack())])
    if new:rows.append([InlineKeyboardButton(text=f"✅ Импортировать {len(new)}",callback_data=FAQCallback(action="import_confirm").pack())])
    rows.append([InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="import_cancel").pack())]);await state.set_state(ImportFAQ.preview)
    await safe_edit_text(cb.message,f"📥 <b>Проверка FAQ CSV</b>\n\nВсего строк: <b>{data.get('import_total',0)}</b>\n✅ Новых: <b>{len(new)}</b>\n♻️ Дубликатов: <b>{len(duplicates)}</b>\n❌ Ошибок: <b>{len(errors)}</b>\n\n🇷🇺 RU: <b>{len(new)}</b>\n🇰🇿 С KK: <b>{with_kk}</b>\n⚠️ Без KK: <b>{len(new)-with_kk}</b>",reply_markup=kb(rows));await cb.answer()


@router.callback_query(ImportFAQ.preview, FAQCallback.filter(F.action.in_({"import_errors", "import_duplicates"})))
async def import_details(cb: CallbackQuery, callback_data: FAQCallback, state: FSMContext) -> None:
    data = await state.get_data()
    if callback_data.action == "import_errors":
        values = data.get("import_errors", [])
        title = "❌ <b>Ошибки</b>"
        lines = [f"Строка {value['row_number']}:\n{html_text(str(value['message']))}" for value in values[:30]]
    else:
        values = data.get("import_duplicates", [])
        title = "♻️ <b>Дубликаты</b>"
        lines = [f"Строка {value['row_number']}: {html_text(text_preview(str(value['question_ru']), 80))}" for value in values[:30]]
    suffix = f"\n\n… ещё {len(values)-30}" if len(values) > 30 else ""
    await safe_edit_text(cb.message,title + "\n\n" + "\n\n".join(lines) + suffix,reply_markup=kb([[InlineKeyboardButton(text="⬅️ К результатам",callback_data=FAQCallback(action="import_preview").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="import_cancel").pack())]]))
    await cb.answer()

@router.callback_query(FAQCallback.filter(F.action=="import_preview"))
async def back_import_preview(cb:CallbackQuery,state:FSMContext)->None:await show_import_preview(cb,state)


@router.callback_query(ImportFAQ.preview, FAQCallback.filter(F.action == "import_confirm"))
async def confirm_import(cb: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await guard(cb, session, settings): return
    data = await state.get_data(); raw_rows = data.get("import_rows", [])
    if not raw_rows: return await cb.answer("Нет новых строк для импорта", show_alert=True)
    existing = {normalize_question(value) for value in await CMSRepository(session).faq_questions()}
    missing_ids: list[int] = []; added = skipped = 0
    service = FAQService(session)
    for raw in raw_rows:
        row = FAQCSVRow(**raw)
        normalized = normalize_question(row.question_ru)
        if normalized in existing:
            skipped += 1; continue
        item = await service.create(cb.from_user.id, row.question_ru, row.answer_ru, row.question_kk or None, row.answer_kk or None, is_active=row.is_active)
        await session.flush(); existing.add(normalized); added += 1
        if not row.question_kk: missing_ids.append(item.id)
    await state.update_data(translation_faq_ids=missing_ids)
    await state.set_state(ImportFAQ.translation_preview if missing_ids else None)
    rows = []
    if missing_ids: rows.append([InlineKeyboardButton(text=f"✨ Создать KK для {len(missing_ids)}", callback_data=FAQCallback(action="ai_generate").pack())])
    rows.append([InlineKeyboardButton(text="⬅️ FAQ", callback_data=FAQCallback(action="import_done").pack())])
    await safe_edit_text(cb.message,f"✅ <b>Импорт завершён</b>\n\nДобавлено: <b>{added}</b>\n♻️ Пропущено: <b>{len(data.get('import_duplicates', []))+skipped}</b>\n⚠️ Без KK: <b>{len(missing_ids)}</b>",reply_markup=kb(rows))
    await cb.answer()


@router.callback_query(FAQCallback.filter(F.action == "import_cancel"))
async def cancel_import(cb: CallbackQuery, state: FSMContext,session:AsyncSession) -> None:
    await finish_wizard(state,*IMPORT_KEYS);await listing(cb,session)

@router.callback_query(FAQCallback.filter(F.action=="import_done"))
async def finish_import(cb:CallbackQuery,state:FSMContext,session:AsyncSession)->None:
    await finish_wizard(state,*IMPORT_KEYS);await listing(cb,session)


async def show_ai_preview(cb: CallbackQuery, state: FSMContext, page: int = 0) -> None:
    data = await state.get_data(); values = data.get("ai_faq_translations", [])
    pages = max(1, (len(values) + 4) // 5); page = max(0, min(page, pages - 1))
    chunk = values[page * 5:(page + 1) * 5]
    body = []
    rows = []
    for index, value in enumerate(chunk, start=page * 5 + 1):
        body.append(f"<b>{index}.</b>\n🇷🇺 {html_text(text_preview(value['question_ru'], 100))}\n🇰🇿 {html_text(text_preview(value['question_kk'], 100))}")
        rows.append([InlineKeyboardButton(text=f"✏️ {index}. {text_preview(value['question_ru'], 30)}", callback_data=FAQCallback(action="ai_open", item_id=value["faq_id"], page=page).pack())])
    if pages > 1:
        rows.append([InlineKeyboardButton(text="⬅️", callback_data=FAQCallback(action="ai_preview", page=max(0, page-1)).pack()), InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"), InlineKeyboardButton(text="➡️", callback_data=FAQCallback(action="ai_preview", page=min(pages-1, page+1)).pack())])
    rows.extend([[InlineKeyboardButton(text="✅ Сохранить KK", callback_data=FAQCallback(action="ai_save").pack())], [InlineKeyboardButton(text="❌ Отмена", callback_data=FAQCallback(action="import_cancel").pack())]])
    await state.set_state(ImportFAQ.translation_preview)
    if cb.message: await safe_edit_text(cb.message, f"✨ <b>AI-переводы FAQ</b>\n\nГотово: <b>{len(values)}</b>\n\n" + "\n\n".join(body), reply_markup=kb(rows))
    await cb.answer()


@router.callback_query(FAQCallback.filter(F.action.in_({"ai_generate", "ai_one"})))
async def generate_ai_faq(cb: CallbackQuery, callback_data: FAQCallback, session: AsyncSession, settings: Settings, state: FSMContext, ai_service: AIService | None) -> None:
    if not await guard(cb, session, settings): return
    if not settings.ai_available or ai_service is None: return await cb.answer("Gemini недоступен", show_alert=True)
    if callback_data.action == "ai_one":
        ids = [callback_data.item_id]
    else:
        ids = list((await state.get_data()).get("translation_faq_ids", []))
    pending = []
    for faq_id in ids:
        item = await CMSRepository(session).faq(int(faq_id))
        if item and not any(value.language_code == LanguageCode.KK for value in item.translations):
            pending.append({"faq_id": item.id, "question_ru": item.question, "answer_ru": item.answer})
    if not pending: return await cb.answer("FAQ без KK-перевода не найдены", show_alert=True)
    user = await upsert_user(session, cb.from_user)
    translated = []
    try:
        batches: list[list[dict[str, object]]] = []
        batch: list[dict[str, object]] = []; batch_size = 0
        for value in pending:
            value_size = len(str(value["question_ru"])) + len(str(value["answer_ru"]))
            if batch and (len(batch) >= 8 or batch_size + value_size > 10_000):
                batches.append(batch); batch = []; batch_size = 0
            batch.append(value); batch_size += value_size
        if batch: batches.append(batch)
        for batch in batches:
            results = await ai_service.translate_faq_batch(session, user_id=user.id, rows=batch)
            for source, result in zip(batch, results, strict=True):
                translated.append({**source, "question_kk": result.question_kk.strip(), "answer_kk": result.answer_kk.strip()})
    except Exception:
        return await cb.answer("Gemini не смог подготовить корректный перевод", show_alert=True)
    await state.update_data(ai_faq_translations=translated)
    await show_ai_preview(cb, state)


@router.callback_query(ImportFAQ.translation_preview, FAQCallback.filter(F.action == "ai_preview"))
async def ai_preview_page(cb: CallbackQuery, callback_data: FAQCallback, state: FSMContext) -> None:
    await show_ai_preview(cb, state, callback_data.page)


@router.callback_query(ImportFAQ.translation_preview, FAQCallback.filter(F.action == "ai_open"))
async def ai_open_translation(cb: CallbackQuery, callback_data: FAQCallback, state: FSMContext) -> None:
    values = (await state.get_data()).get("ai_faq_translations", [])
    value = next((item for item in values if item["faq_id"] == callback_data.item_id), None)
    if not value: return await cb.answer("Перевод не найден", show_alert=True)
    rows = [[InlineKeyboardButton(text="✏️ Изменить сұрақ", callback_data=FAQCallback(action="ai_edit_q", item_id=value["faq_id"], page=callback_data.page).pack())], [InlineKeyboardButton(text="✏️ Изменить жауап", callback_data=FAQCallback(action="ai_edit_a", item_id=value["faq_id"], page=callback_data.page).pack())], [InlineKeyboardButton(text="⬅️ Назад", callback_data=FAQCallback(action="ai_preview", page=callback_data.page).pack())]]
    await safe_edit_text(cb.message, f"✨ <b>Предложенный перевод</b>\n\n🇷🇺 <b>Вопрос:</b>\n{html_text(value['question_ru'])}\n\n🇰🇿 <b>Сұрақ:</b>\n{html_text(value['question_kk'])}\n\n🇷🇺 <b>Ответ:</b>\n{html_text(text_preview(value['answer_ru']))}\n\n🇰🇿 <b>Жауап:</b>\n{html_text(text_preview(value['answer_kk']))}", reply_markup=kb(rows)); await cb.answer()


@router.callback_query(ImportFAQ.translation_preview, FAQCallback.filter(F.action.in_({"ai_edit_q", "ai_edit_a"})))
async def begin_ai_edit(cb: CallbackQuery, callback_data: FAQCallback, state: FSMContext) -> None:
    field="question_kk" if callback_data.action.endswith("q") else "answer_kk";data=await state.get_data();value=next((item for item in data.get("ai_faq_translations",[]) if item["faq_id"]==callback_data.item_id),None)
    await state.update_data(ai_edit_id=callback_data.item_id, ai_edit_field=field, ai_edit_page=callback_data.page)
    await state.set_state(ImportFAQ.translation_edit)
    await safe_edit_text(cb.message,f"✏️ <b>Редактирование перевода</b>\n\n🇰🇿 Поле: <b>{'Сұрақ' if field=='question_kk' else 'Жауап'}</b>\n\nТекущее значение:\n{html_text(text_preview(value[field] if value else '',300))}\n\nОтправьте исправленный текст.",reply_markup=kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="ai_edit_back",item_id=callback_data.item_id,page=callback_data.page).pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="import_cancel").pack())]]));await cb.answer()


@router.message(ImportFAQ.translation_edit, F.text)
async def receive_ai_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data(); values = data.get("ai_faq_translations", [])
    for value in values:
        if value["faq_id"] == data.get("ai_edit_id"): value[data["ai_edit_field"]] = message.text.strip()
    await state.update_data(ai_faq_translations=values);await consume_input(message)
    await state.set_state(ImportFAQ.translation_preview)
    value=next(item for item in values if item["faq_id"]==data.get("ai_edit_id"));rows=[[InlineKeyboardButton(text="✏️ Изменить сұрақ",callback_data=FAQCallback(action="ai_edit_q",item_id=value["faq_id"],page=data.get("ai_edit_page",0)).pack())],[InlineKeyboardButton(text="✏️ Изменить жауап",callback_data=FAQCallback(action="ai_edit_a",item_id=value["faq_id"],page=data.get("ai_edit_page",0)).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="ai_preview",page=data.get("ai_edit_page",0)).pack())]]
    await edit_wizard(message.bot,state,f"✨ <b>Предложенный перевод</b>\n\n🇷🇺 <b>Вопрос:</b>\n{html_text(value['question_ru'])}\n\n🇰🇿 <b>Сұрақ:</b>\n{html_text(value['question_kk'])}\n\n🇷🇺 <b>Ответ:</b>\n{html_text(text_preview(value['answer_ru']))}\n\n🇰🇿 <b>Жауап:</b>\n{html_text(text_preview(value['answer_kk']))}",kb(rows))

@router.callback_query(FAQCallback.filter(F.action=="ai_edit_back"))
async def ai_edit_back(cb:CallbackQuery,callback_data:FAQCallback,state:FSMContext)->None:
    await state.set_state(ImportFAQ.translation_preview);await ai_open_translation(cb,callback_data,state)


@router.callback_query(ImportFAQ.translation_preview, FAQCallback.filter(F.action == "ai_save"))
async def save_ai_faq(cb: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if not await guard(cb, session, settings): return
    values = (await state.get_data()).get("ai_faq_translations", []); saved = skipped = 0
    for value in values:
        item = await CMSRepository(session).faq(value["faq_id"])
        if not item or any(translation.language_code == LanguageCode.KK for translation in item.translations):
            skipped += 1; continue
        await FAQService(session).update_translation(cb.from_user.id, item, LanguageCode.KK, question=value["question_kk"], answer=value["answer_kk"]); saved += 1
    await state.set_state(ImportFAQ.translation_preview)
    await safe_edit_text(cb.message,f"✅ <b>KK-переводы сохранены</b>\n\nСохранено: <b>{saved}</b>\nПропущено: <b>{skipped}</b>",reply_markup=kb([[InlineKeyboardButton(text="⬅️ FAQ",callback_data=FAQCallback(action="import_done").pack())]]));await cb.answer()

@router.callback_query(FAQCallback.filter(F.action=="add"))
async def add(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):return
    await state.set_state(AddFAQ.question);await state.update_data(faq_question=None,faq_answer=None,faq_question_kk=None,faq_answer_kk=None)
    await remember_wizard(state,cb.message)
    await safe_edit_text(cb.message,"➕ <b>Новый FAQ</b>\n\nШаг 1 из 4\n🇷🇺 <b>Вопрос</b>\n\nОтправьте вопрос на русском языке.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]));await cb.answer()
@router.message(AddFAQ.question)
async def question(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):return
    value=(m.text or "").strip()
    if not value or len(value)>500:return
    await state.update_data(faq_question=value);await consume_input(m);await state.set_state(AddFAQ.answer)
    await edit_wizard(m.bot,state,f"➕ <b>Новый FAQ</b>\n\nШаг 2 из 4\n🇷🇺 <b>Ответ</b>\n\n❓ Вопрос:\n{html_text(text_preview(value,120))}\n\nОтправьте ответ на русском языке.",kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="create_back_q").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))
@router.message(AddFAQ.answer)
async def answer(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):return
    value=(m.text or "").strip()
    if not value or len(value)>10000:return
    await state.update_data(faq_answer=value);await consume_input(m);await state.set_state(AddFAQ.question_kk)
    await show_kk_choice(m.bot,state)

async def show_kk_choice(bot,state):
    await state.set_state(AddFAQ.question_kk)
    await edit_wizard(bot,state,"➕ <b>Новый FAQ</b>\n\nШаг 3 из 4\n🇰🇿 <b>Қазақша</b>\n\nРусская версия заполнена.\n\nКак добавить казахский перевод?",kb([[InlineKeyboardButton(text="✍️ Ввести вручную",callback_data=FAQCallback(action="create_manual_kk").pack())],[InlineKeyboardButton(text="✨ Создать через Gemini",callback_data=FAQCallback(action="create_ai_kk").pack())],[InlineKeyboardButton(text="⏭ Без KK",callback_data=FAQCallback(action="create_skip_kk").pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="create_back_a").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))

@router.callback_query(FAQCallback.filter(F.action=="create_manual_kk"))
async def manual_kk(cb:CallbackQuery,state:FSMContext):
    await state.set_state(AddFAQ.question_kk);await safe_edit_text(cb.message,"➕ <b>Новый FAQ</b>\n\nШаг 3 из 4\n🇰🇿 <b>Сұрақ</b>\n\nОтправьте вопрос на казахском языке.",reply_markup=kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="create_kk_choice").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]));await cb.answer()

@router.callback_query(FAQCallback.filter(F.action=="create_ai_kk"))
async def create_ai_kk(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext,ai_service:AIService|None):
    if not settings.ai_available or ai_service is None:return await cb.answer("Gemini недоступен",show_alert=True)
    d=await state.get_data();await safe_edit_text(cb.message,"✨ <b>Создаю казахский перевод...</b>")
    user=await upsert_user(session,cb.from_user)
    try:
        result=(await ai_service.translate_faq_batch(session,user_id=user.id,rows=[{"question_ru":d["faq_question"],"answer_ru":d["faq_answer"]}]))[0]
    except Exception:
        await safe_edit_text(cb.message,"⚠️ <b>Не удалось создать перевод.</b>",reply_markup=kb([[InlineKeyboardButton(text="🔄 Попробовать ещё раз",callback_data=FAQCallback(action="create_ai_kk").pack())],[InlineKeyboardButton(text="✍️ Ввести вручную",callback_data=FAQCallback(action="create_manual_kk").pack())],[InlineKeyboardButton(text="⏭ Без KK",callback_data=FAQCallback(action="create_skip_kk").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]));return await cb.answer()
    await state.update_data(faq_question_kk=result.question_kk,faq_answer_kk=result.answer_kk);await show_create_preview(cb.bot,state);await cb.answer()
@router.message(AddFAQ.question_kk)
async def question_kk(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):return
    value=(m.text or "").strip()
    if not value or len(value)>500:return
    await state.update_data(faq_question_kk=value);await consume_input(m);await state.set_state(AddFAQ.answer_kk)
    await edit_wizard(m.bot,state,f"➕ <b>Новый FAQ</b>\n\nШаг 4 из 4\n🇰🇿 <b>Жауап</b>\n\n❓ Сұрақ:\n{html_text(text_preview(value,120))}\n\nОтправьте ответ на казахском языке.",kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="create_manual_kk").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))
@router.message(AddFAQ.answer_kk)
async def answer_kk(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):return
    value=(m.text or "").strip()
    if not value or len(value)>10000:return
    await state.update_data(faq_answer_kk=value);await consume_input(m);await show_create_preview(m.bot,state)

async def show_create_preview(bot,state):
    d=await state.get_data();await state.set_state(AddFAQ.confirm)
    kk=(f"❓ Сұрақ:\n{html_text(d['faq_question_kk'])}\n\n💬 Жауап:\n{html_text(text_preview(d['faq_answer_kk']))}" if d.get('faq_question_kk') and d.get('faq_answer_kk') else "⚠️ Перевод отсутствует")
    await edit_wizard(bot,state,f"👁 <b>Предпросмотр FAQ</b>\n\n🇷🇺 <b>Русский</b>\n\n❓ Вопрос:\n{html_text(d['faq_question'])}\n\n💬 Ответ:\n{html_text(text_preview(d['faq_answer']))}\n\n🇰🇿 <b>Қазақша{' ⚠️' if not d.get('faq_question_kk') else ''}</b>\n\n{kk}\n\n🟢 Статус: Активен",kb([[InlineKeyboardButton(text="✏️ Изменить RU",callback_data=FAQCallback(action="create_edit_ru").pack()),InlineKeyboardButton(text="✏️ Изменить KK",callback_data=FAQCallback(action="create_kk_choice").pack())],[InlineKeyboardButton(text="✅ Создать FAQ",callback_data=FAQCallback(action="create").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))
@router.callback_query(AddFAQ.confirm,FAQCallback.filter(F.action=="create"))
async def create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):return
    d=await state.get_data();await FAQService(session).create(cb.from_user.id,d["faq_question"],d["faq_answer"],d.get("faq_question_kk"),d.get("faq_answer_kk"));await finish_wizard(state,"faq_question","faq_answer","faq_question_kk","faq_answer_kk");await listing(cb,session)

@router.callback_query(FAQCallback.filter(F.action.in_({"create_back_q","create_back_a","create_kk_choice","create_skip_kk","create_edit_ru"})))
async def create_navigation(cb:CallbackQuery,callback_data:FAQCallback,state:FSMContext):
    if callback_data.action in {"create_back_q","create_edit_ru"}:
        await state.set_state(AddFAQ.question);await safe_edit_text(cb.message,"➕ <b>Новый FAQ</b>\n\nШаг 1 из 4\n🇷🇺 <b>Вопрос</b>\n\nОтправьте вопрос на русском языке.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))
    elif callback_data.action=="create_back_a":
        await state.set_state(AddFAQ.answer);d=await state.get_data();await safe_edit_text(cb.message,f"➕ <b>Новый FAQ</b>\n\nШаг 2 из 4\n🇷🇺 <b>Ответ</b>\n\n❓ Вопрос:\n{html_text(text_preview(d['faq_question'],120))}\n\nОтправьте ответ на русском языке.",reply_markup=kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="create_back_q").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="create_cancel").pack())]]))
    elif callback_data.action=="create_skip_kk":await state.update_data(faq_question_kk=None,faq_answer_kk=None);await show_create_preview(cb.bot,state)
    else:await show_kk_choice(cb.bot,state)
    await cb.answer()

@router.callback_query(FAQCallback.filter(F.action=="create_cancel"))
async def cancel_create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    await finish_wizard(state,"faq_question","faq_answer","faq_question_kk","faq_answer_kk");await listing(cb,session)

@router.callback_query(FAQCallback.filter(F.action.in_({"up","down","toggle","delete_ask","delete_yes"})))
async def mutate(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).faq(callback_data.item_id)
    if not x:return await cb.answer("FAQ не найден",show_alert=True)
    svc=FAQService(session)
    if callback_data.action in {"up","down"}:await svc.move(cb.from_user.id,x,-1 if callback_data.action=="up" else 1);items=await CMSRepository(session).faqs();new_page=next((index//8 for index,item in enumerate(items) if item.id==x.id),callback_data.page);return await open_(cb,FAQCallback(action="open",item_id=x.id,page=new_page),session,settings)
    if callback_data.action=="toggle":await svc.update(cb.from_user.id,x,is_active=not x.is_active);return await open_(cb,FAQCallback(action="open",item_id=x.id,page=callback_data.page),session,settings)
    if callback_data.action=="delete_ask":
        if cb.message:await safe_edit_text(cb.message,f"⚠️ <b>Удалить FAQ «{html_text(text_preview(x.question,60))}»?</b>\n\nЭто действие нельзя отменить.",reply_markup=kb([[InlineKeyboardButton(text="🔴 Да, удалить",callback_data=FAQCallback(action="delete_yes",item_id=x.id,page=callback_data.page).pack())],[InlineKeyboardButton(text="⬅️ Отмена",callback_data=FAQCallback(action="open",item_id=x.id,page=callback_data.page).pack())]]));return await cb.answer()
    await svc.delete(cb.from_user.id,x);await listing(cb,session,callback_data.page)

@router.callback_query(FAQCallback.filter(F.action.in_({"edit_q","edit_a","edit_q_ru","edit_a_ru","edit_q_kk","edit_a_kk"})))
async def edit(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):
        parts=callback_data.action.split("_");field="question" if parts[1]=="q" else "answer";language=parts[2] if len(parts)>2 else "ru";x=await CMSRepository(session).faq(callback_data.item_id);translation=next((v for v in x.translations if v.language_code==LanguageCode(language)),None) if x else None;current=(getattr(translation,field) if translation else getattr(x,field,""))
        await state.set_state(EditFAQ.value);await state.update_data(edit_faq_id=callback_data.item_id,edit_faq_field=field,edit_faq_language=language,edit_faq_page=callback_data.page);await remember_wizard(state,cb.message);await safe_edit_text(cb.message,f"✏️ <b>Редактирование FAQ</b>\n\n{'🇷🇺' if language=='ru' else '🇰🇿'} <b>{'Вопрос' if field=='question' else 'Ответ'}</b>\n\nТекущее значение:\n{html_text(text_preview(current,300))}\n\nОтправьте новое значение.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="edit_cancel",item_id=callback_data.item_id,page=callback_data.page).pack())]]));await cb.answer()
@router.message(EditFAQ.value)
async def edit_value(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):return
    d=await state.get_data();x=await CMSRepository(session).faq(d["edit_faq_id"])
    if x and m.text:
        await FAQService(session).update_translation(m.from_user.id,x,LanguageCode(d["edit_faq_language"]),**{d["edit_faq_field"]:m.text});await session.flush();await consume_input(m);card,markup=faq_card(x,d.get("edit_faq_page",0));await edit_wizard(m.bot,state,card,markup);await finish_wizard(state,"edit_faq_id","edit_faq_field","edit_faq_language","edit_faq_page")

@router.callback_query(EditFAQ.value,FAQCallback.filter(F.action=="edit_cancel"))
async def edit_cancel(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,state:FSMContext)->None:
    await finish_wizard(state,"edit_faq_id","edit_faq_field","edit_faq_language","edit_faq_page");x=await CMSRepository(session).faq(callback_data.item_id)
    if x:card,markup=faq_card(x,callback_data.page);await safe_edit_text(cb.message,card,reply_markup=markup)
    await cb.answer()
