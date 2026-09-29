from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.callbacks import AdminPageCallback, AttachmentCallback
from app.config import Settings
from app.database.models import PageAttachment
from app.database.models.enums import MediaType
from app.database.repositories.pages import PageRepository
from app.services.attachments import AttachmentService
from app.services.pages import PageService
from app.services.permissions import Permission, PermissionService
from app.states.admin import AddAttachment, EditAttachment
from app.utils.telegram import safe_edit_text
from app.utils.presentation import html_text, text_preview
router=Router(name="attachments")
def kb(r):return InlineKeyboardMarkup(inline_keyboard=r)
async def ok(uid,s,c):return await PermissionService(s,c).has(uid,Permission.CONTENT)
async def guard(cb,s,c):
    if await ok(cb.from_user.id,s,c):return True
    await cb.answer("Доступ запрещён",show_alert=True);return False
async def get_item(session,item_id):return await session.get(PageAttachment,item_id)
async def listing(cb,page_id,session):
    page=await PageService(session).ensure_editable_draft(page_id,cb.from_user.id)
    if not page or not page.draft_revision:return await cb.answer("Страница не найдена",show_alert=True)
    items=sorted(page.draft_revision.attachments,key=lambda x:x.position);active_count=sum(1 for x in items if x.is_active);rows=[[InlineKeyboardButton(text=f"{i+1}. {'🔴 ' if not x.is_active else ''}{'🖼' if x.type==MediaType.PHOTO else '📄'} {text_preview(x.file_name or x.type.value,38)}",callback_data=AttachmentCallback(action="open",item_id=x.id,page_id=page_id).pack())] for i,x in enumerate(items)];rows += [[InlineKeyboardButton(text="➕ Добавить вложение",callback_data=AttachmentCallback(action="add",page_id=page_id).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=AdminPageCallback(action="open",page_id=page_id).pack())]];await safe_edit_text(cb.message,f"📎 <b>Вложения</b>\n\nВсего: <b>{len(items)}</b>\n🟢 Активных: <b>{active_count}</b>",reply_markup=kb(rows));await cb.answer()
@router.callback_query(AdminPageCallback.filter(F.action=="attachments"))
async def menu(cb:CallbackQuery,callback_data:AdminPageCallback,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,callback_data.page_id,session)
@router.callback_query(AttachmentCallback.filter(F.action=="list"))
async def list_(cb:CallbackQuery,callback_data:AttachmentCallback,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,callback_data.page_id,session)
@router.callback_query(AttachmentCallback.filter(F.action=="add"))
async def add(cb:CallbackQuery,callback_data:AttachmentCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(AddAttachment.file);await state.update_data(page_id=callback_data.page_id);await cb.message.answer("Отправьте документ или изображение.");await cb.answer()
@router.message(AddAttachment.file,F.document|F.photo)
async def receive(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if m.document:type_,file_id,name=MediaType.DOCUMENT,m.document.file_id,m.document.file_name
    else:type_,file_id,name=MediaType.PHOTO,m.photo[-1].file_id,"image.jpg"
    await state.update_data(type=type_.value,file_id=file_id,file_name=name);await state.set_state(AddAttachment.confirm)
    if type_==MediaType.PHOTO:await m.answer_photo(file_id,caption=html_text(name))
    else:await m.answer_document(file_id,caption=html_text(name))
    await m.answer("Добавить вложение?",reply_markup=kb([[InlineKeyboardButton(text="✅ Добавить",callback_data=AttachmentCallback(action="create").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel").pack())]]))
@router.message(AddAttachment.file)
async def wrong(m:Message):await m.answer("Нужен документ или изображение.")
@router.callback_query(AddAttachment.confirm,AttachmentCallback.filter(F.action=="create"))
async def create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    d=await state.get_data();await AttachmentService(session).create(d["page_id"],cb.from_user.id,MediaType(d["type"]),d["file_id"],d.get("file_name"));await state.clear();await listing(cb,d["page_id"],session)
@router.callback_query(AttachmentCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,state:FSMContext):await state.clear();await cb.answer("Отменено",show_alert=True)
@router.callback_query(AttachmentCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery,callback_data:AttachmentCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await get_item(session,callback_data.item_id)
    if not x:return await cb.answer("Вложение не найдено",show_alert=True)
    rows=[[InlineKeyboardButton(text="🇷🇺 Подпись RU",callback_data=AttachmentCallback(action="caption",item_id=x.id,page_id=callback_data.page_id,language="ru").pack()),InlineKeyboardButton(text="🇰🇿 Подпись KK",callback_data=AttachmentCallback(action="caption",item_id=x.id,page_id=callback_data.page_id,language="kk").pack())],[InlineKeyboardButton(text="⬆️ Выше",callback_data=AttachmentCallback(action="up",item_id=x.id,page_id=callback_data.page_id).pack()),InlineKeyboardButton(text="⬇️ Ниже",callback_data=AttachmentCallback(action="down",item_id=x.id,page_id=callback_data.page_id).pack())],[InlineKeyboardButton(text="🔴 Выключить" if x.is_active else "🟢 Включить",callback_data=AttachmentCallback(action="toggle",item_id=x.id,page_id=callback_data.page_id).pack())],[InlineKeyboardButton(text="🗑 Удалить",callback_data=AttachmentCallback(action="delete_ask",item_id=x.id,page_id=callback_data.page_id).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=AttachmentCallback(action="list",page_id=callback_data.page_id).pack())]]
    type_label="🖼 Изображение" if x.type==MediaType.PHOTO else "📄 Документ";card=f"📎 <b>{html_text(x.file_name or type_label)}</b>\n\n{'🟢 Активно' if x.is_active else '🔴 Выключено'}\n\n<b>Тип:</b> {type_label}\n📁 <b>Файл:</b>\n{html_text(x.file_name or '—')}\n\n🇷🇺 <b>Подпись:</b>\n{html_text(x.caption_ru or '—')}\n\n🇰🇿 <b>Қолтаңба:</b>\n{html_text(x.caption_kk or '—')}\n\n📍 Позиция: {x.position+1}"
    await safe_edit_text(cb.message,card,reply_markup=kb(rows));await cb.answer()
@router.callback_query(AttachmentCallback.filter(F.action=="caption"))
async def caption(cb:CallbackQuery,callback_data:AttachmentCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(EditAttachment.caption);await state.update_data(item_id=callback_data.item_id,page_id=callback_data.page_id,language=callback_data.language);await cb.message.answer("Введите подпись:");await cb.answer()
@router.message(EditAttachment.caption)
async def caption_value(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    d=await state.get_data();x=await get_item(session,d["item_id"])
    if x and m.text:await AttachmentService(session).update(x,m.from_user.id,**{f"caption_{d['language']}":m.text});await state.clear();await m.answer("✅ Подпись обновлена.")
@router.callback_query(AttachmentCallback.filter(F.action.in_({"up","down","toggle","delete_ask","delete_yes"})))
async def mutate(cb:CallbackQuery,callback_data:AttachmentCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await get_item(session,callback_data.item_id)
    if not x:return await cb.answer("Вложение не найдено",show_alert=True)
    svc=AttachmentService(session)
    if callback_data.action in {"up","down"}:await svc.move(x,cb.from_user.id,-1 if callback_data.action=="up" else 1);return await open_(cb,callback_data,session,settings)
    if callback_data.action=="toggle":await svc.update(x,cb.from_user.id,is_active=not x.is_active);return await open_(cb,callback_data,session,settings)
    if callback_data.action=="delete_ask":await safe_edit_text(cb.message,f"⚠️ <b>Удалить вложение «{html_text(x.file_name or x.type.value)}»?</b>\n\nЭто действие нельзя отменить.",reply_markup=kb([[InlineKeyboardButton(text="🔴 Да, удалить",callback_data=AttachmentCallback(action="delete_yes",item_id=x.id,page_id=callback_data.page_id).pack())],[InlineKeyboardButton(text="⬅️ Отмена",callback_data=AttachmentCallback(action="open",item_id=x.id,page_id=callback_data.page_id).pack())]]));return await cb.answer()
    await svc.delete(x,cb.from_user.id);await listing(cb,callback_data.page_id,session)
