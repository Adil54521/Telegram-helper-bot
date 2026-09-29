from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback, FAQCallback
from app.config import Settings
from app.database.repositories.cms import CMSRepository
from app.database.models.enums import LanguageCode
from app.services.cms import FAQService
from app.services.permissions import Permission, PermissionService
from app.states.admin import AddFAQ, EditFAQ
from app.utils.telegram import safe_edit_text
from app.utils.presentation import html_text, text_preview

router=Router(name="admin_faq"); SIZE=8
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
    rows += [[InlineKeyboardButton(text="➕ Добавить вопрос",callback_data=FAQCallback(action="add").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]]
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
    rows=[[InlineKeyboardButton(text="🇷🇺 Редактировать RU",callback_data=FAQCallback(action="edit_q_ru",item_id=x.id).pack()),InlineKeyboardButton(text="🇰🇿 Редактировать KK",callback_data=FAQCallback(action="edit_q_kk",item_id=x.id).pack())],[InlineKeyboardButton(text="🇷🇺 Изменить ответ",callback_data=FAQCallback(action="edit_a_ru",item_id=x.id).pack()),InlineKeyboardButton(text="🇰🇿 Жауапты өзгерту",callback_data=FAQCallback(action="edit_a_kk",item_id=x.id).pack())],[InlineKeyboardButton(text="⬆️ Выше",callback_data=FAQCallback(action="up",item_id=x.id).pack()),InlineKeyboardButton(text="⬇️ Ниже",callback_data=FAQCallback(action="down",item_id=x.id).pack())],[InlineKeyboardButton(text="🔴 Выключить" if x.is_active else "🟢 Включить",callback_data=FAQCallback(action="toggle",item_id=x.id).pack())],[InlineKeyboardButton(text="🗑 Удалить",callback_data=FAQCallback(action="delete_ask",item_id=x.id).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=FAQCallback(action="list").pack())]]
    ru=next((v for v in x.translations if v.language_code==LanguageCode.RU),None);kk=next((v for v in x.translations if v.language_code==LanguageCode.KK),None)
    ru_q,ru_a=(ru.question,ru.answer) if ru else (x.question,x.answer)
    kk_block=(f"<b>Сұрақ:</b>\n{html_text(kk.question)}\n\n<b>Жауап:</b>\n{html_text(text_preview(kk.answer))}" if kk else "Перевод отсутствует")
    card=(f"❓ <b>FAQ</b>\n\n{'🟢 Активен' if x.is_active else '🔴 Выключен'}\n\n🇷🇺 <b>Русский</b>\n\n<b>Вопрос:</b>\n{html_text(ru_q)}\n\n<b>Ответ:</b>\n{html_text(text_preview(ru_a))}\n\n🇰🇿 <b>Қазақша{' ⚠️' if not kk else ''}</b>\n\n{kk_block}\n\n📍 Позиция: {x.position+1}")
    if cb.message:await safe_edit_text(cb.message,card,reply_markup=kb(rows));await cb.answer()

@router.callback_query(FAQCallback.filter(F.action=="add"))
async def add(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(AddFAQ.question);await cb.message.answer("Введите вопрос:");await cb.answer()
@router.message(AddFAQ.question)
async def question(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст вопроса.")
    await state.update_data(question=m.text);await state.set_state(AddFAQ.answer);await m.answer("Введите ответ:")
@router.message(AddFAQ.answer)
async def answer(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст ответа.")
    await state.update_data(answer=m.text);await state.set_state(AddFAQ.question_kk);await m.answer("Введите вопрос 🇰🇿:")
@router.message(AddFAQ.question_kk)
async def question_kk(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст.")
    await state.update_data(question_kk=m.text);await state.set_state(AddFAQ.answer_kk);await m.answer("Введите ответ 🇰🇿:")
@router.message(AddFAQ.answer_kk)
async def answer_kk(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст.")
    d=await state.get_data();await state.update_data(answer_kk=m.text);await state.set_state(AddFAQ.confirm)
    await m.answer(f"<b>Предпросмотр FAQ</b>\n\n🇷🇺 {html_text(d['question'])}\n{html_text(d['answer'])}\n\n🇰🇿 {html_text(d['question_kk'])}\n{html_text(m.text)}",reply_markup=kb([[InlineKeyboardButton(text="✅ Создать",callback_data=FAQCallback(action="create").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=FAQCallback(action="cancel").pack())]]))
@router.callback_query(AddFAQ.confirm,FAQCallback.filter(F.action=="create"))
async def create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    d=await state.get_data();await FAQService(session).create(cb.from_user.id,d["question"],d["answer"],d.get("question_kk"),d.get("answer_kk"));await state.clear();await listing(cb,session)
@router.callback_query(FAQCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,state:FSMContext):await state.clear();await cb.answer("Отменено",show_alert=True)

@router.callback_query(FAQCallback.filter(F.action.in_({"up","down","toggle","delete_ask","delete_yes"})))
async def mutate(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).faq(callback_data.item_id)
    if not x:return await cb.answer("FAQ не найден",show_alert=True)
    svc=FAQService(session)
    if callback_data.action in {"up","down"}:await svc.move(cb.from_user.id,x,-1 if callback_data.action=="up" else 1);return await open_(cb,FAQCallback(action="open",item_id=x.id),session,settings)
    if callback_data.action=="toggle":await svc.update(cb.from_user.id,x,is_active=not x.is_active);return await open_(cb,FAQCallback(action="open",item_id=x.id),session,settings)
    if callback_data.action=="delete_ask":
        if cb.message:await safe_edit_text(cb.message,f"⚠️ <b>Удалить FAQ «{html_text(text_preview(x.question,60))}»?</b>\n\nЭто действие нельзя отменить.",reply_markup=kb([[InlineKeyboardButton(text="🔴 Да, удалить",callback_data=FAQCallback(action="delete_yes",item_id=x.id).pack())],[InlineKeyboardButton(text="⬅️ Отмена",callback_data=FAQCallback(action="open",item_id=x.id).pack())]]));return await cb.answer()
    await svc.delete(cb.from_user.id,x);await listing(cb,session)

@router.callback_query(FAQCallback.filter(F.action.in_({"edit_q","edit_a","edit_q_ru","edit_a_ru","edit_q_kk","edit_a_kk"})))
async def edit(cb:CallbackQuery,callback_data:FAQCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):
        parts=callback_data.action.split("_");await state.set_state(EditFAQ.value);await state.update_data(item_id=callback_data.item_id,field="question" if parts[1]=="q" else "answer",language=parts[2] if len(parts)>2 else "ru");await cb.message.answer("Введите новое значение:");await cb.answer()
@router.message(EditFAQ.value)
async def edit_value(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    d=await state.get_data();x=await CMSRepository(session).faq(d["item_id"])
    if x and m.text:await FAQService(session).update_translation(m.from_user.id,x,LanguageCode(d.get("language","ru")),**{d["field"]:m.text});await state.clear();await m.answer("✅ FAQ обновлён.")
