from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback
from app.config import Settings
from app.keyboards.admin import dashboard
from app.services.permissions import PermissionService
from app.utils.telegram import safe_edit_text

router = Router(name="admin_dashboard")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command("admin"))
async def admin(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not message.from_user:
        return
    role = await PermissionService(session, settings).role_for(message.from_user.id)
    if not role:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer("⚙️ <b>Панель управления</b>\n\nУправление содержимым Telegram-бота", reply_markup=dashboard(role))


@router.callback_query(AdminMenuCallback.filter(F.section == "dashboard"))
async def back_dashboard(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await PermissionService(session, settings).role_for(callback.from_user.id)
    if not role:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    if callback.message:
        await safe_edit_text(callback.message, "⚙️ <b>Панель управления</b>\n\nУправление содержимым Telegram-бота", reply_markup=dashboard(role))
    await callback.answer()
