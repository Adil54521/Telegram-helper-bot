from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.callbacks import AdminCallback, AdminMenuCallback
from app.config import Settings
from app.database.models.enums import AdminRole
from app.database.repositories.cms import CMSRepository
from app.services.cms import AdminService
from app.services.permissions import Permission, PermissionService
from app.states.admin import AddAdmin
from app.utils.telegram import safe_edit_text
from app.utils.wizard import consume_input, finish_wizard, remember_wizard
router=Router(name="admins")
def kb(r):return InlineKeyboardMarkup(inline_keyboard=r)
async def guard(cb,s,c):
    if await PermissionService(s,c).has(cb.from_user.id,Permission.ADMINS):return True
    await cb.answer("Доступ запрещён",show_alert=True);return False
async def permitted(uid,s,c):return await PermissionService(s,c).has(uid,Permission.ADMINS)
def roles(action,item_id=0):return kb([[InlineKeyboardButton(text=f"{icon} {r.value}",callback_data=AdminCallback(action=action,item_id=item_id,value=r.value).pack())] for icon,r in (("👑",AdminRole.SUPER_ADMIN),("🛡",AdminRole.ADMIN),("✏️",AdminRole.EDITOR))])
async def listing(cb,s):
    items=await CMSRepository(s).admins();rows=[[InlineKeyboardButton(text=f"{'👑' if x.role==AdminRole.SUPER_ADMIN else '🛡' if x.role==AdminRole.ADMIN else '✏️'} {x.display_name or x.telegram_id} — {x.role.value}",callback_data=AdminCallback(action="open",item_id=x.id).pack())] for x in items];rows += [[InlineKeyboardButton(text="➕ Добавить администратора",callback_data=AdminCallback(action="add").pack())],[InlineKeyboardButton(text="⬅️ Панель",callback_data=AdminMenuCallback(section="dashboard").pack())]];await safe_edit_text(cb.message,"👥 <b>Администраторы</b>",reply_markup=kb(rows));await cb.answer()
@router.callback_query(AdminMenuCallback.filter(F.section=="admins"))
async def menu(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,session)
@router.callback_query(AdminCallback.filter(F.action=="list"))
async def list_(cb:CallbackQuery,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await listing(cb,session)
@router.callback_query(AdminCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery,callback_data:AdminCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).admin(callback_data.item_id)
    if not x:return await cb.answer("Администратор не найден",show_alert=True)
    rows=[[InlineKeyboardButton(text="🔄 Изменить роль",callback_data=AdminCallback(action="role",item_id=x.id).pack())],[InlineKeyboardButton(text="🔴 Отключить" if x.is_active else "🟢 Включить",callback_data=AdminCallback(action="toggle",item_id=x.id).pack())],[InlineKeyboardButton(text="🗑 Удалить",callback_data=AdminCallback(action="delete_ask",item_id=x.id).pack())],[InlineKeyboardButton(text="⬅️ Назад",callback_data=AdminCallback(action="list").pack())]]
    await safe_edit_text(cb.message,f"👤 <b>Администратор</b>\n\nTelegram ID: <code>{x.telegram_id}</code>\nИмя: {x.display_name or '—'}\nРоль: {x.role.value}\nАктивен: {'да' if x.is_active else 'нет'}\nСоздан: {x.created_at:%d.%m.%Y %H:%M}",reply_markup=kb(rows));await cb.answer()
@router.callback_query(AdminCallback.filter(F.action=="add"))
async def add(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if await guard(cb,session,settings):await state.set_state(AddAdmin.telegram_id);await remember_wizard(state,cb.message);await safe_edit_text(cb.message,"👤 <b>Новый администратор</b>\n\nШаг 1 из 2\n<b>Telegram ID</b>\n\nОтправьте Telegram ID пользователя.",reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=AdminCallback(action="cancel").pack())]]));await cb.answer()
@router.message(AddAdmin.telegram_id)
async def get_id(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await permitted(m.from_user.id,session,settings):await state.clear();return
    try:value=int(m.text or "")
    except ValueError:return await m.answer("Telegram ID должен быть целым числом.")
    if value<=0:return await m.answer("Telegram ID должен быть положительным.")
    if await CMSRepository(session).admin_by_telegram(value):return await m.answer("Такой администратор уже существует.")
    await state.update_data(telegram_id=value);await consume_input(m);await state.set_state(AddAdmin.role);d=await state.get_data();await m.bot.edit_message_text(f"👤 <b>Новый администратор</b>\n\nTelegram ID: <code>{value}</code>\n\nШаг 2 из 2\nВыберите роль:",chat_id=d["wizard_chat_id"],message_id=d["wizard_message_id"],reply_markup=roles("new_role"))
@router.callback_query(AddAdmin.role,AdminCallback.filter(F.action=="new_role"))
async def role_new(cb:CallbackQuery,callback_data:AdminCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    await state.update_data(role=callback_data.value);await state.set_state(AddAdmin.confirm);d=await state.get_data();await safe_edit_text(cb.message,f"Telegram ID: <code>{d['telegram_id']}</code>\nРоль: {callback_data.value}",reply_markup=kb([[InlineKeyboardButton(text="✅ Добавить",callback_data=AdminCallback(action="create").pack())],[InlineKeyboardButton(text="❌ Отмена",callback_data=AdminCallback(action="cancel").pack())]]));await cb.answer()
@router.callback_query(AddAdmin.confirm,AdminCallback.filter(F.action=="create"))
async def create(cb:CallbackQuery,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):await state.clear();return
    d=await state.get_data()
    try:await AdminService(session,settings.super_admin_id).create(cb.from_user.id,d["telegram_id"],AdminRole(d["role"]))
    except ValueError as e:return await cb.answer(str(e),show_alert=True)
    await finish_wizard(state,"telegram_id","role");await listing(cb,session)
@router.callback_query(AdminCallback.filter(F.action=="cancel"))
async def cancel(cb:CallbackQuery,state:FSMContext,session:AsyncSession):await finish_wizard(state,"telegram_id","role");await listing(cb,session)
@router.callback_query(AdminCallback.filter(F.action=="role"))
async def choose_role(cb:CallbackQuery,callback_data:AdminCallback,session:AsyncSession,settings:Settings):
    if await guard(cb,session,settings):await safe_edit_text(cb.message,"Выберите новую роль:",reply_markup=roles("set_role",callback_data.item_id));await cb.answer()
@router.callback_query(AdminCallback.filter(F.action.in_({"set_role","toggle","delete_ask","delete_yes"})))
async def mutate(cb:CallbackQuery,callback_data:AdminCallback,session:AsyncSession,settings:Settings):
    if not await guard(cb,session,settings):return
    x=await CMSRepository(session).admin(callback_data.item_id)
    if not x:return await cb.answer("Администратор не найден",show_alert=True)
    svc=AdminService(session,settings.super_admin_id)
    try:
        if callback_data.action=="set_role":await svc.role(cb.from_user.id,x,AdminRole(callback_data.value));return await open_(cb,AdminCallback(action="open",item_id=x.id),session,settings)
        if callback_data.action=="toggle":await svc.toggle(cb.from_user.id,x);return await open_(cb,AdminCallback(action="open",item_id=x.id),session,settings)
        if callback_data.action=="delete_ask":await safe_edit_text(cb.message,"Удалить администратора?",reply_markup=kb([[InlineKeyboardButton(text="🔴 Да, удалить",callback_data=AdminCallback(action="delete_yes",item_id=x.id).pack())],[InlineKeyboardButton(text="Отмена",callback_data=AdminCallback(action="open",item_id=x.id).pack())]]));return await cb.answer()
        await svc.delete(cb.from_user.id,x);await listing(cb,session)
    except ValueError as e:await cb.answer(str(e),show_alert=True)
