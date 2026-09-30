from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.repositories.users import upsert_user
from app.handlers.user.pages import send_page

router = Router(name="user_fallback")


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def text_to_main(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not message.from_user:
        return
    user = await upsert_user(session, message.from_user)
    await state.update_data(current_page="main", navigation_stack=[])
    await send_page(
        message,
        session,
        "main",
        user.content_language_code,
        settings.ai_available,
    )
