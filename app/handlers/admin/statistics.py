from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AdminMenuCallback
from app.config import Settings
from app.services.permissions import Permission, PermissionService
from app.services.statistics import user_statistics
from app.utils.telegram import safe_edit_text

router = Router(name="statistics")


@router.callback_query(AdminMenuCallback.filter(F.section == "statistics"))
async def statistics(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await PermissionService(session, settings).has(callback.from_user.id, Permission.STATISTICS):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    values = await user_statistics(session)
    text = f"📊 <b>Статистика</b>\n\nВсего пользователей: {values['total']}\nНовых сегодня: {values['new_today']}\nНовых за 7 дней: {values['new_week']}\nАктивных за 7 дней: {values['active_week']}"
    if callback.message:
        await safe_edit_text(callback.message, text)
    await callback.answer()
