import asyncio
import logging
from collections import defaultdict
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.callbacks import AdminMenuCallback, BroadcastCallback
from app.config import Settings
from app.database.models import AuditLog, Broadcast
from app.database.models.enums import BroadcastStatus
from app.database.repositories.cms import CMSRepository
from app.services.broadcasts import run_broadcast
from app.services.permissions import Permission, PermissionService
from app.states.admin import CreateBroadcast
from app.utils.telegram import safe_edit_text
from app.utils.presentation import html_text, text_preview
from app.utils.wizard import consume_input, edit_wizard, finish_wizard, remember_wizard
from app.utils.pagination import paginate
from app.filters.admin import HasPermission
router=Router(name="broadcasts");SIZE=8
router.callback_query.filter(HasPermission(Permission.BROADCAST_MANAGE))
router.message.filter(HasPermission(Permission.BROADCAST_MANAGE))
logger=logging.getLogger(__name__)
_confirm_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
MAX_BROADCAST_PHOTO_BYTES = 20_000_000
def kb(r):return InlineKeyboardMarkup(inline_keyboard=r)
async def ok(uid,s,c):return await PermissionService(s,c).has(uid,Permission.BROADCAST_MANAGE)
async def guard(cb,s,c):
    if await ok(cb.from_user.id,s,c):return True
    await cb.answer("Доступ запрещён",show_alert=True);return False
@router.callback_query(AdminMenuCallback.filter(F.section=="broadcasts"))
async def menu(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    await safe_edit_text(cb.message,"📢 <b>Рассылка</b>",reply_markup=kb([[InlineKeyboardButton(text="➕ Новая рассылка",callback_data=BroadcastCallback(action="new").pack())],[InlineKeyboardButton(text="📋 История рассылок",callback_data=BroadcastCallback(action="history").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]]));await cb.answer()
@router.callback_query(BroadcastCallback.filter(F.action=="new"))
async def new(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(CreateBroadcast.text);await remember_wizard(state,cb.message);await safe_edit_text(cb.message,"📢 <b>Новая рассылка</b>\n\nШаг 1 из 2\n📝 <b>Текст</b>\n\nОтправьте текст рассылки.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]));await cb.answer()
@router.message(CreateBroadcast.text)
async def text(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст.")
    await state.update_data(text=m.text);await consume_input(m);await state.set_state(CreateBroadcast.media);await edit_wizard(m.bot,state,f"📢 <b>Новая рассылка</b>\n\nШаг 2 из 2\n🖼 <b>Медиа</b>\n\nТекст:\n{html_text(text_preview(m.text))}\n\nОтправьте фото или продолжите без него.",kb([[InlineKeyboardButton(text="⏭ Без фото",callback_data=BroadcastCallback(action="no_photo").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]))
@router.message(CreateBroadcast.media,F.photo)
async def photo(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if m.photo[-1].file_size and m.photo[-1].file_size > MAX_BROADCAST_PHOTO_BYTES:return await m.answer("Фото слишком большое (максимум 20 МБ).")
    photo_id=m.photo[-1].file_id;await consume_input(m);await prepare(m,state,photo_id,session)
@router.callback_query(CreateBroadcast.media,BroadcastCallback.filter(F.action=="no_photo"))
async def no_photo(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    await prepare(cb.message,state,None,session);await cb.answer()
async def prepare(m:Message,state:FSMContext,photo_id:str|None,session:AsyncSession):
    d=await state.get_data();await state.update_data(photo=photo_id);await state.set_state(CreateBroadcast.confirm);count=await CMSRepository(session).recipient_count()
    await edit_wizard(m.bot,state,f"👁 <b>Предпросмотр рассылки</b>\n\n{html_text(text_preview(d['text']))}\n\nМедиа: {'🖼 Фото' if photo_id else '—'}\nПолучателей: <b>{count}</b>",kb([[InlineKeyboardButton(text="🚀 Начать рассылку",callback_data=BroadcastCallback(action="create_start").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]))
@router.callback_query(CreateBroadcast.confirm,BroadcastCallback.filter(F.action=="create_start"))
async def create_start(cb:CallbackQuery,bot:Bot,session:AsyncSession,settings:Settings,state:FSMContext,session_factory:async_sessionmaker[AsyncSession]):
    if not await guard(cb,session,settings):await state.clear();return
    async with _confirm_locks[cb.from_user.id]:
        d=await state.get_data()
        if d.get("broadcast_starting") or not d.get("text"):return await cb.answer("Рассылка уже запускается",show_alert=True)
        await state.update_data(broadcast_starting=True)
        item=Broadcast(text=d["text"],media_file_id=d.get("photo"),status=BroadcastStatus.DRAFT,created_by=cb.from_user.id);session.add(item);await session.flush();metadata={"has_photo":bool(item.media_file_id)}
        if d.get("source_broadcast_id"):metadata["source_broadcast_id"]=d["source_broadcast_id"]
        session.add(AuditLog(admin_id=cb.from_user.id,action="BROADCAST_CREATED",entity_type="Broadcast",entity_id=str(item.id),new_data=metadata));await session.commit();await finish_wizard(state,"text","photo","source_broadcast_id","history_page","broadcast_starting");await safe_edit_text(cb.message,"🚀 <b>Рассылка запущена</b>");await cb.answer()
    async def task():
        try:
            total,sent,failed=await run_broadcast(bot,session_factory,item.id,cb.from_user.id)
            await bot.send_message(cb.from_user.id,f"✅ <b>Рассылка завершена</b>\n\nВсего: {total}\nОтправлено: {sent}\nОшибок: {failed}")
        except Exception:
            async with session_factory() as failed_session, failed_session.begin():
                failed_item=await failed_session.get(Broadcast,item.id)
                if failed_item and failed_item.status==BroadcastStatus.RUNNING:
                    from datetime import datetime, timezone
                    failed_item.status=BroadcastStatus.FAILED;failed_item.finished_at=datetime.now(timezone.utc)
                    failed_session.add(AuditLog(admin_id=cb.from_user.id,action="BROADCAST_FAILED",entity_type="Broadcast",entity_id=str(item.id),new_data={"status":"FAILED"}))
            try:await bot.send_message(cb.from_user.id,"❌ Рассылка завершилась с системной ошибкой. Подробности записаны в лог.")
            except Exception:
                logger.warning("Could not notify broadcast owner about failure",exc_info=True)
    asyncio.create_task(task())
@router.callback_query(BroadcastCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,state:FSMContext,session:AsyncSession,settings:Settings):await finish_wizard(state,"text","photo","source_broadcast_id","history_page");await menu(cb,session,settings)
@router.callback_query(BroadcastCallback.filter(F.action=="history"))
async def history(cb:CallbackQuery,session:AsyncSession,settings:Settings,callback_data:BroadcastCallback):
    if not await guard(cb,session,settings):return
    items=await CMSRepository(session).broadcasts();result=paginate(items,callback_data.page);icons={BroadcastStatus.COMPLETED:"✅",BroadcastStatus.FAILED:"❌",BroadcastStatus.DRAFT:"🟡",BroadcastStatus.RUNNING:"🔵",BroadcastStatus.CANCELLED:"⚪"};rows=[[InlineKeyboardButton(text=f"{icons[x.status]} #{x.id} — {x.created_at:%d.%m.%Y %H:%M}",callback_data=BroadcastCallback(action="open",item_id=x.id,page=result.page).pack())] for x in result.items]
    if result.pages>1:rows.append([InlineKeyboardButton(text="⬅️",callback_data=BroadcastCallback(action="history",page=max(0,result.page-1)).pack()),InlineKeyboardButton(text=f"{result.page+1} / {result.pages}",callback_data="noop"),InlineKeyboardButton(text="➡️",callback_data=BroadcastCallback(action="history",page=min(result.pages-1,result.page+1)).pack())])
    rows += [[InlineKeyboardButton(text="➕ Новая рассылка",callback_data=BroadcastCallback(action="new").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]];await safe_edit_text(cb.message,f"📋 <b>История рассылок</b>\n\nВсего: <b>{len(items)}</b>\n\n"+("Выберите рассылку." if items else "Пока ничего нет."),reply_markup=kb(rows));await cb.answer()
@router.callback_query(BroadcastCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery,callback_data:BroadcastCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).broadcast(callback_data.item_id)
    if not x:return await cb.answer("Рассылка не найдена",show_alert=True)
    statuses={BroadcastStatus.COMPLETED:"🟢 Завершена",BroadcastStatus.FAILED:"🔴 Ошибка",BroadcastStatus.DRAFT:"🟡 Черновик",BroadcastStatus.RUNNING:"🔵 Выполняется",BroadcastStatus.CANCELLED:"⚪ Отменена"};await safe_edit_text(cb.message,f"📢 <b>Рассылка #{x.id}</b>\n\n{statuses[x.status]}\n📅 {x.created_at:%d.%m.%Y %H:%M}\n\n👥 Получателей: <b>{x.total}</b>\n✅ Отправлено: <b>{x.sent}</b>\n❌ Ошибок: <b>{x.failed}</b>\n\n📝 <b>Текст:</b>\n{html_text(text_preview(x.text))}\n\n📎 Медиа: {'🖼 Фото' if x.media_file_id else '—'}",reply_markup=kb([[InlineKeyboardButton(text="🔁 Повторить рассылку",callback_data=BroadcastCallback(action="repeat",item_id=x.id,page=callback_data.page).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=BroadcastCallback(action="history",page=callback_data.page).pack())]]));await cb.answer()

@router.callback_query(BroadcastCallback.filter(F.action=="repeat"))
async def repeat(cb:CallbackQuery,callback_data:BroadcastCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):return
    source=await CMSRepository(session).broadcast(callback_data.item_id)
    if not source:return await cb.answer("Рассылка не найдена",show_alert=True)
    await remember_wizard(state,cb.message);await state.update_data(text=source.text,photo=source.media_file_id,source_broadcast_id=source.id,history_page=callback_data.page);await state.set_state(CreateBroadcast.confirm);count=await CMSRepository(session).recipient_count()
    await safe_edit_text(cb.message,f"🔁 <b>Повтор рассылки #{source.id}</b>\n\n📝 Текст:\n{html_text(text_preview(source.text))}\n\n📎 Медиа: {'🖼 Фото' if source.media_file_id else '—'}\n\n👥 Текущих получателей: <b>{count}</b>\n\nЭто новая рассылка. Исходная #{source.id} останется без изменений.",reply_markup=kb([[InlineKeyboardButton(text="✏️ Изменить текст",callback_data=BroadcastCallback(action="repeat_text").pack())],[InlineKeyboardButton(text="📎 Изменить медиа",callback_data=BroadcastCallback(action="repeat_media").pack())],[InlineKeyboardButton(text="🚀 Отправить",callback_data=BroadcastCallback(action="create_start").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]));await cb.answer()

@router.callback_query(CreateBroadcast.confirm,BroadcastCallback.filter(F.action=="repeat_text"))
async def repeat_text(cb:CallbackQuery,state:FSMContext):await state.set_state(CreateBroadcast.text);await safe_edit_text(cb.message,"📢 <b>Повтор рассылки</b>\n\nОтправьте новый текст.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]));await cb.answer()
@router.callback_query(CreateBroadcast.confirm,BroadcastCallback.filter(F.action=="repeat_media"))
async def repeat_media(cb:CallbackQuery,state:FSMContext):await state.set_state(CreateBroadcast.media);await safe_edit_text(cb.message,"📢 <b>Повтор рассылки</b>\n\nОтправьте новое фото или удалите текущее.",reply_markup=kb([[InlineKeyboardButton(text="🗑 Без фото",callback_data=BroadcastCallback(action="no_photo").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]));await cb.answer()
