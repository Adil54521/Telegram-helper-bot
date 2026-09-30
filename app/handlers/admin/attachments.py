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
from app.utils.wizard import consume_input, finish_wizard, remember_wizard
from app.utils.wizard import edit_wizard
from app.ai.service import AIService
from app.database.repositories.users import upsert_user
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
    if await guard(cb,session,settings):await state.set_state(AddAttachment.file);await state.update_data(page_id=callback_data.page_id);await remember_wizard(state,cb.message);await safe_edit_text(cb.message,"📎 <b>Новое вложение</b>\n\nШаг 1 из 2\nОтправьте документ или изображение.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel",page_id=callback_data.page_id).pack())]]));await cb.answer()
@router.message(AddAttachment.file,F.document|F.photo)
async def receive(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if m.document:type_,file_id,name=MediaType.DOCUMENT,m.document.file_id,m.document.file_name
    else:type_,file_id,name=MediaType.PHOTO,m.photo[-1].file_id,"image.jpg"
    await state.update_data(type=type_.value,file_id=file_id,file_name=name,caption_ru=None,caption_kk=None);await consume_input(m);await state.set_state(AddAttachment.caption_ru)
    await edit_wizard(m.bot,state,f"📎 <b>Новое вложение</b>\n\n{'🖼' if type_==MediaType.PHOTO else '📄'} {html_text(name or 'Файл')}\n\nШаг 2 из 3\n🇷🇺 <b>Подпись</b>",kb([[InlineKeyboardButton(text="✍️ Добавить подпись",callback_data=AttachmentCallback(action="new_cap_ru").pack())],[InlineKeyboardButton(text="⏭ Без подписи",callback_data=AttachmentCallback(action="new_no_cap").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel").pack())]]))
@router.message(AddAttachment.file)
async def wrong(m:Message):await m.answer("Нужен документ или изображение.")

@router.callback_query(AttachmentCallback.filter(F.action=="new_cap_ru"))
async def cap_ru(cb:CallbackQuery,state:FSMContext):await state.set_state(AddAttachment.caption_ru);await safe_edit_text(cb.message,"📎 <b>Новое вложение</b>\n\nШаг 2 из 3\n🇷🇺 Отправьте подпись.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel").pack())]]));await cb.answer()

@router.message(AddAttachment.caption_ru,F.text)
async def receive_cap_ru(m:Message,state:FSMContext):
    value=m.text.strip()
    if len(value)>1024:return
    await state.update_data(caption_ru=value);await consume_input(m);await show_kk_caption(m.bot,state)

async def show_kk_caption(bot,state):
    d=await state.get_data()
    if not d.get("caption_ru"):return await show_attachment_preview(bot,state)
    await state.set_state(AddAttachment.caption_kk);await edit_wizard(bot,state,f"📎 <b>Новое вложение</b>\n\nШаг 3 из 3\n🇰🇿 <b>Подпись</b>\n\n🇷🇺 {html_text(text_preview(d['caption_ru'],160))}",kb([[InlineKeyboardButton(text="✍️ Ввести вручную",callback_data=AttachmentCallback(action="new_cap_kk").pack())],[InlineKeyboardButton(text="✨ Перевести RU → KK",callback_data=AttachmentCallback(action="new_cap_ai").pack())],[InlineKeyboardButton(text="⏭ Без KK",callback_data=AttachmentCallback(action="new_cap_preview").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel").pack())]]))

@router.callback_query(AttachmentCallback.filter(F.action=="new_no_cap"))
async def no_cap(cb:CallbackQuery,state:FSMContext):await state.update_data(caption_ru=None,caption_kk=None);await show_attachment_preview(cb.bot,state);await cb.answer()
@router.callback_query(AttachmentCallback.filter(F.action=="new_cap_kk"))
async def cap_kk(cb:CallbackQuery,state:FSMContext):await state.set_state(AddAttachment.caption_kk);await safe_edit_text(cb.message,"📎 <b>Новое вложение</b>\n\nШаг 3 из 3\n🇰🇿 Отправьте подпись.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel").pack())]]));await cb.answer()
@router.message(AddAttachment.caption_kk,F.text)
async def receive_cap_kk(m:Message,state:FSMContext):
    if len(m.text.strip())>1024:return
    await state.update_data(caption_kk=m.text.strip());await consume_input(m);await show_attachment_preview(m.bot,state)
@router.callback_query(AttachmentCallback.filter(F.action=="new_cap_preview"))
async def no_kk_cap(cb:CallbackQuery,state:FSMContext):await state.update_data(caption_kk=None);await show_attachment_preview(cb.bot,state);await cb.answer()
@router.callback_query(AttachmentCallback.filter(F.action=="new_cap_ai"))
async def ai_cap(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext,ai_service:AIService|None):
    if not settings.ai_available or ai_service is None:return await cb.answer("Gemini недоступен",show_alert=True)
    d=await state.get_data();user=await upsert_user(session,cb.from_user)
    try:value=await ai_service.translate_short_text(session,user_id=user.id,text=d["caption_ru"],request_type="CMS_TRANSLATION",max_length=1024)
    except Exception:return await cb.answer("Не удалось создать перевод",show_alert=True)
    await state.update_data(caption_kk=value);await show_attachment_preview(cb.bot,state);await cb.answer()

async def show_attachment_preview(bot,state):
    d=await state.get_data();await state.set_state(AddAttachment.confirm)
    await edit_wizard(bot,state,f"👁 <b>Предпросмотр вложения</b>\n\n{'🖼' if d['type']==MediaType.PHOTO.value else '📄'} {html_text(d.get('file_name') or 'Файл')}\n\n🇷🇺 Подпись:\n{html_text(d.get('caption_ru') or '—')}\n\n🇰🇿 Қолтаңба:\n{html_text(d.get('caption_kk') or '—')}",kb([[InlineKeyboardButton(text="✅ Добавить вложение",callback_data=AttachmentCallback(action="create").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=AttachmentCallback(action="cancel",page_id=d["page_id"]).pack())]]))
@router.callback_query(AddAttachment.confirm,AttachmentCallback.filter(F.action=="create"))
async def create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    d=await state.get_data();await AttachmentService(session).create(d["page_id"],cb.from_user.id,MediaType(d["type"]),d["file_id"],d.get("file_name"),d.get("caption_ru"),d.get("caption_kk"));await finish_wizard(state,"page_id","type","file_id","file_name","caption_ru","caption_kk");await listing(cb,d["page_id"],session)
@router.callback_query(AttachmentCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,callback_data:AttachmentCallback,state:FSMContext,session:AsyncSession):
    d=await state.get_data();page_id=callback_data.page_id or d.get("page_id");await finish_wizard(state,"page_id","type","file_id","file_name");await listing(cb,page_id,session)
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
