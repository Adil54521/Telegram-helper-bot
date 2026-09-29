from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.users import upsert_user
from app.handlers.user.pages import send_page
from app.keyboards.user import language_keyboard
from app.utils.i18n import t

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    if not user.content_language_code:
        await message.answer("Тілді таңдаңыз / Выберите язык", reply_markup=language_keyboard())
        return
    await send_page(message, session, "main", user.content_language_code)
