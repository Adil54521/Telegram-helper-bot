import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback
from app.config import Settings
from app.database.models.enums import AdminRole
from app.keyboards.admin import dashboard
from app.services.permissions import PermissionService
from app.utils.telegram import safe_edit_text
from app.handlers.user.pages import send_page

router = Router(name="admin_dashboard")
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()

def dashboard_text(role: AdminRole) -> str:
    if role == AdminRole.AI_HISTORY_VIEWER:
        return (
            "⚙️ <b>Панель управления</b>\n\n"
            "Доступен просмотр истории вопросов ИИ."
        )

    return (
        "⚙️ <b>Панель управления</b>\n\n"
        "Управление содержимым Telegram-бота"
    )

@router.message(Command("admin"))
async def admin(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not message.from_user:
        return
    role = await PermissionService(session, settings).role_for(message.from_user.id)
    if not role:
        logger.info("Unauthorized admin entry user_id=%s", message.from_user.id)
        await send_page(message, session, "main", ai_available=settings.ai_available)
        return
    await message.answer(text=dashboard_text(role), reply_markup=dashboard(role))


@router.callback_query(AdminMenuCallback.filter(F.section == "dashboard"))
async def back_dashboard(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await PermissionService(session, settings).role_for(callback.from_user.id)
    if not role:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    if callback.message:
        await safe_edit_text(callback.message, text=dashboard_text(role), reply_markup=dashboard(role))
    await callback.answer()
