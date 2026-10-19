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
from app.utils.pagination import paginate
from app.utils.presentation import html_text
from app.filters.admin import HasPermission

router = Router(name="admins")
router.callback_query.filter(HasPermission(Permission.ADMINS_MANAGE))
router.message.filter(HasPermission(Permission.ADMINS_MANAGE))

def kb(r):
    return InlineKeyboardMarkup(inline_keyboard=r)

async def guard(cb: CallbackQuery, session: AsyncSession, settings: Settings):
    if await PermissionService(session, settings).has(cb.from_user.id, Permission.ADMINS_MANAGE):
        return True
    
    await cb.answer("Доступ запрещён", show_alert=True)
    return False

async def permitted(uid, session: AsyncSession, settings: Settings):
    return await PermissionService(session, settings).has(uid, Permission.ADMINS_MANAGE)

ROLE_ICONS = {
    AdminRole.SUPER_ADMIN: "👑",
    AdminRole.ADMIN: "🛡",
    AdminRole.EDITOR: "✏️",
    AdminRole.AI_HISTORY_VIEWER: "👁",
}

def roles(action, item_id=0, page=0):
    return kb(
        [
            [InlineKeyboardButton(text=f"{icon} {r.value}", callback_data=AdminCallback(action=action, item_id=item_id, value=r.value,page=page).pack())] 
            for icon,r in (("👑", AdminRole.SUPER_ADMIN), ("🛡", AdminRole.ADMIN), ("✏️", AdminRole.EDITOR), ("👁", AdminRole.AI_HISTORY_VIEWER))
        ]
    )

async def listing(cb, s, page=0):
    items = await CMSRepository(s).admins()
    result = paginate(items,page)
    rows = [
        [
            InlineKeyboardButton(
            text=(
                f"{ROLE_ICONS.get(x.role, '👤')} "
                f"{x.display_name or x.telegram_id} — {x.role.value}"
            ),
            callback_data=AdminCallback(action="open", item_id=x.id,page=result.page).pack())
        ] for x in result.items
    ]
    
    if result.pages > 1:
        rows.append(
            [
             InlineKeyboardButton(text="⬅️", callback_data=AdminCallback(action="list", page=max(0, result.page - 1)).pack()),
             InlineKeyboardButton(text=f"{result.page + 1} / {result.pages}",callback_data="noop"),
             InlineKeyboardButton(text="➡️", callback_data=AdminCallback(action="list", page=min(result.pages - 1, result.page + 1)).pack())
            ]
        )
    
    rows += [
        [InlineKeyboardButton(text="➕ Добавить администратора", callback_data=AdminCallback(action="add", page=result.page).pack())],
        [InlineKeyboardButton(text="⬅️ Панель", callback_data=AdminMenuCallback(section="dashboard").pack())]
    ]
    
    await safe_edit_text(
        cb.message, f"👥 <b>Администраторы</b>\n\nВсего: <b>{len(items)}</b>\n\n"+("Выберите администратора." if items else "Пока ничего нет."), 
        reply_markup=kb(rows))
    
    await cb.answer()

@router.callback_query(AdminMenuCallback.filter(F.section == "admins"))
async def menu(cb:CallbackQuery, session:AsyncSession, settings:Settings):
    if await guard(cb, session, settings):
        await listing(cb, session)

@router.callback_query(AdminCallback.filter(F.action=="list"))
async def list_(cb:CallbackQuery, callback_data:AdminCallback, session:AsyncSession, settings:Settings):
    if await guard(cb, session, settings):
        await listing(cb, session, callback_data.page)

@router.callback_query(AdminCallback.filter(F.action=="open"))
async def open_(cb:CallbackQuery, callback_data:AdminCallback, session:AsyncSession, settings:Settings):
    if not await guard(cb, session, settings):
        return
    
    x = await CMSRepository(session).admin(callback_data.item_id)
    
    if not x:
        return await cb.answer("Администратор не найден",show_alert=True)
    
    rows = [
        [InlineKeyboardButton(text="🔄 Изменить роль", callback_data=AdminCallback(action="role", item_id=x.id, page=callback_data.page).pack())],
        [InlineKeyboardButton(text="🔴 Отключить" if x.is_active else "🟢 Включить",callback_data=AdminCallback(action="toggle", item_id=x.id, page=callback_data.page).pack())],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=AdminCallback(action="delete_ask", item_id=x.id, page=callback_data.page).pack())],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminCallback(action="list", page=callback_data.page).pack())]
    ]

    await safe_edit_text(
        cb.message,
        f"👤 <b>Администратор</b>\n\nTelegram ID: <code>{x.telegram_id}</code>\nИмя: {html_text(x.display_name or '—')}\nРоль: {x.role.value}\nАктивен: {'да' if x.is_active else 'нет'}\nСоздан: {x.created_at:%d.%m.%Y %H:%M}",reply_markup=kb(rows))
    
    await cb.answer()

@router.callback_query(AdminCallback.filter(F.action=="add"))
async def add(cb:CallbackQuery, session:AsyncSession, settings:Settings, state:FSMContext):
    if await guard(cb,session,settings):
        await state.set_state(AddAdmin.telegram_id)
        await remember_wizard(state,cb.message)
        await safe_edit_text(
            cb.message,"👤 <b>Новый администратор</b>\n\nШаг 1 из 2\n<b>Telegram ID</b>\n\nОтправьте Telegram ID пользователя.", 
            reply_markup=kb([[InlineKeyboardButton(text="❌ Отмена",callback_data=AdminCallback(action="cancel").pack())]]))
        await cb.answer()

@router.message(AddAdmin.telegram_id)
async def get_id(m:Message,session:AsyncSession,settings:Settings,state:FSMContext):
    if not m.from_user or not await permitted(m.from_user.id,session,settings):
        await state.clear()
        return
    
    try:
        value = int(m.text or "")
    except ValueError:
        return await m.answer("Telegram ID должен быть целым числом.")
    
    if value <= 0:
        return await m.answer("Telegram ID должен быть положительным.")
    
    if await CMSRepository(session).admin_by_telegram(value):
        return await m.answer("Такой администратор уже существует.")
    
    await state.update_data(telegram_id=value)
    await consume_input(m)
    await state.set_state(AddAdmin.role)
    
    d = await state.get_data()
    
    await m.bot.edit_message_text(
        f"👤 <b>Новый администратор</b>\n\nTelegram ID: <code>{value}</code>\n\nШаг 2 из 2\nВыберите роль:",
        chat_id=d["wizard_chat_id"],
        message_id=d["wizard_message_id"],
        reply_markup=roles("new_role")
    )

@router.callback_query(AddAdmin.role,AdminCallback.filter(F.action=="new_role"))
async def role_new(cb:CallbackQuery,callback_data:AdminCallback,session:AsyncSession,settings:Settings,state:FSMContext):
    if not await guard(cb,session,settings):
        await state.clear()
        return
    try:
        role = AdminRole(callback_data.value)
    except ValueError:
        await cb.answer(
            "Неизвестная роль",
            show_alert=True,
        )
        return

    await state.update_data(role=role.value)
    await state.set_state(AddAdmin.confirm)

    d = await state.get_data()
    
    await safe_edit_text(
        cb.message,
        f"Telegram ID: <code>{d['telegram_id']}</code>\nРоль: {callback_data.value}",
        reply_markup=kb(
            [
                [InlineKeyboardButton(text="✅ Добавить", callback_data=AdminCallback(action="create").pack())],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=AdminCallback(action="cancel").pack())]
            ]
        )
    )
    await cb.answer()

@router.callback_query(AddAdmin.confirm, AdminCallback.filter(F.action=="create"))
async def create(cb: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext):
    if not await guard(cb,session,settings):
        await state.clear()
        return
    
    d = await state.get_data()
    
    try:
        await AdminService(session, settings.super_admin_id).create(cb.from_user.id, d["telegram_id"], AdminRole(d["role"]))
    except ValueError as e:
        return await cb.answer(str(e), show_alert=True)
    
    await finish_wizard(state, "telegram_id", "role")
    await listing(cb, session)

@router.callback_query(AdminCallback.filter(F.action=="cancel"))
async def cancel(cb: CallbackQuery, state:FSMContext, session:AsyncSession):
    await finish_wizard(state, "telegram_id","role")
    await listing(cb, session)

@router.callback_query(AdminCallback.filter(F.action=="role"))
async def choose_role(cb: CallbackQuery, callback_data: AdminCallback, session: AsyncSession, settings: Settings):
    if await guard(cb, session, settings):
        await safe_edit_text(cb.message, "Выберите новую роль:", reply_markup=roles("set_role", callback_data.item_id, callback_data.page))
        await cb.answer()

@router.callback_query(AdminCallback.filter(F.action.in_({"set_role", "toggle", "delete_ask", "delete_yes"})))
async def mutate(cb: CallbackQuery, callback_data: AdminCallback, session: AsyncSession, settings: Settings):
    if not await guard(cb, session, settings):
        return

    x = await CMSRepository(session).admin(callback_data.item_id)

    if not x:
        await cb.answer(
            "Администратор не найден",
            show_alert=True,
        )
        return

    svc = AdminService(session, settings.super_admin_id)

    try:
        if callback_data.action == "set_role":
            role = AdminRole(callback_data.value)

            await svc.role(
                cb.from_user.id,
                x,
                role,
            )

            return await open_(
                cb,
                AdminCallback(
                    action="open",
                    item_id=x.id,
                    page=callback_data.page,
                ),
                session,
                settings,
            )

        if callback_data.action == "toggle":
            await svc.toggle(cb.from_user.id, x)

            return await open_(
                cb,
                AdminCallback(
                    action="open",
                    item_id=x.id,
                    page=callback_data.page,
                ),
                session,
                settings,
            )

        if callback_data.action == "delete_ask":
            await safe_edit_text(
                cb.message,
                "Удалить администратора?",
                reply_markup=kb([
                    [
                        InlineKeyboardButton(
                            text="🔴 Да, удалить",
                            callback_data=AdminCallback(
                                action="delete_yes",
                                item_id=x.id,
                                page=callback_data.page,
                            ).pack(),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Отмена",
                            callback_data=AdminCallback(
                                action="open",
                                item_id=x.id,
                                page=callback_data.page,
                            ).pack(),
                        )
                    ],
                ]),
            )

            await cb.answer()
            return

        if callback_data.action == "delete_yes":
            await svc.delete(cb.from_user.id, x)

            await listing(cb, session, callback_data.page)

    except ValueError as error:
        await cb.answer(str(error), show_alert=True)
