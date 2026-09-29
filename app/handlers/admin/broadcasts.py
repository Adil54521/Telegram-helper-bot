import asyncio
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
router=Router(name="broadcasts");SIZE=8
def kb(r):return InlineKeyboardMarkup(inline_keyboard=r)
async def ok(uid,s,c):return await PermissionService(s,c).has(uid,Permission.BROADCAST)
async def guard(cb,s,c):
    if await ok(cb.from_user.id,s,c):return True
    await cb.answer("Доступ запрещён",show_alert=True);return False
@router.callback_query(AdminMenuCallback.filter(F.section=="broadcasts"))
async def menu(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    await safe_edit_text(cb.message,"📢 <b>Рассылка</b>",reply_markup=kb([[InlineKeyboardButton(text="➕ Новая рассылка",callback_data=BroadcastCallback(action="new").pack())],[InlineKeyboardButton(text="📋 История рассылок",callback_data=BroadcastCallback(action="history").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]]));await cb.answer()
@router.callback_query(BroadcastCallback.filter(F.action=="new"))
async def new(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(CreateBroadcast.text);await cb.message.answer("Введите текст рассылки (HTML поддерживается):");await cb.answer()
@router.message(CreateBroadcast.text)
async def text(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    if not m.text:return await m.answer("Отправьте текст.")
    await state.update_data(text=m.text);await state.set_state(CreateBroadcast.media);await m.answer("Добавьте фото или продолжите без него:",reply_markup=kb([[InlineKeyboardButton(text="➡️ Без фото",callback_data=BroadcastCallback(action="no_photo").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]))
@router.message(CreateBroadcast.media,F.photo)
async def photo(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await ok(m.from_user.id,session,settings):await state.clear();return
    await prepare(m,state,m.photo[-1].file_id,session)
@router.callback_query(CreateBroadcast.media,BroadcastCallback.filter(F.action=="no_photo"))
async def no_photo(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    await prepare(cb.message,state,None,session);await cb.answer()
async def prepare(m:Message,state:FSMContext,photo_id:str|None,session:AsyncSession):
    d=await state.get_data();await state.update_data(photo=photo_id);await state.set_state(CreateBroadcast.confirm);count=await CMSRepository(session).recipient_count()
    if photo_id:await m.answer_photo(photo_id,caption=d["text"])
    else:await m.answer(d["text"])
    await m.answer(f"📢 <b>Рассылка готова</b>\nПолучателей: {count}",reply_markup=kb([[InlineKeyboardButton(text="🚀 Начать рассылку",callback_data=BroadcastCallback(action="create_start").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=BroadcastCallback(action="cancel").pack())]]))
@router.callback_query(CreateBroadcast.confirm,BroadcastCallback.filter(F.action=="create_start"))
async def create_start(cb:CallbackQuery,bot:Bot,session:AsyncSession,settings:Settings,state:FSMContext,session_factory:async_sessionmaker[AsyncSession]):
    if not await guard(cb,session,settings):await state.clear();return
    d=await state.get_data();item=Broadcast(text=d["text"],media_file_id=d.get("photo"),status=BroadcastStatus.DRAFT,created_by=cb.from_user.id);session.add(item);await session.flush();session.add(AuditLog(admin_id=cb.from_user.id,action="BROADCAST_CREATED",entity_type="Broadcast",entity_id=str(item.id),new_data={"has_photo":bool(item.media_file_id)}));await session.commit();await state.clear();await cb.answer("Рассылка запущена",show_alert=True)
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
            except Exception:pass
    asyncio.create_task(task())
@router.callback_query(BroadcastCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,state:FSMContext):await state.clear();await cb.answer("Отменено",show_alert=True)
@router.callback_query(BroadcastCallback.filter(F.action=="history"))
async def history(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    items=await CMSRepository(session).broadcasts();icons={BroadcastStatus.COMPLETED:"✅",BroadcastStatus.FAILED:"❌",BroadcastStatus.DRAFT:"🟡",BroadcastStatus.RUNNING:"🔵",BroadcastStatus.CANCELLED:"⚪"};rows=[[InlineKeyboardButton(text=f"{icons[x.status]} #{x.id} — {x.sent}/{x.total}",callback_data=BroadcastCallback(action="open",item_id=x.id).pack())] for x in items[:SIZE]];rows.append([InlineKeyboardButton(text="⬅️ Назад",callback_data=AdminMenuCallback(section="broadcasts").pack())]);await safe_edit_text(cb.message,"📋 <b>История рассылок</b>",reply_markup=kb(rows));await cb.answer()
@router.callback_query(BroadcastCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery,callback_data:BroadcastCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).broadcast(callback_data.item_id)
    if not x:return await cb.answer("Рассылка не найдена",show_alert=True)
    snippet=x.text[:200]+("…" if len(x.text)>200 else "");await safe_edit_text(cb.message,f"📢 <b>Рассылка #{x.id}</b>\n\nСтатус: {x.status.value}\nСоздана: {x.created_at:%d.%m.%Y %H:%M}\nСтарт: {x.started_at or '—'}\nЗавершена: {x.finished_at or '—'}\nВсего: {x.total}\nОтправлено: {x.sent}\nОшибок: {x.failed}\nАвтор: <code>{x.created_by}</code>\n\n{snippet}",reply_markup=kb([[InlineKeyboardButton(text="⬅️ Назад",callback_data=BroadcastCallback(action="history").pack())]]));await cb.answer()
