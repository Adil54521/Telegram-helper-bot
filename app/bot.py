import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app.config import get_settings
from app.database.session import create_session_factory
from app.handlers.admin import router as admin_router
from app.handlers.user import router as user_router
from app.middlewares.database import DatabaseMiddleware
from app.services.seed import seed


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)
    engine, session_factory = create_session_factory(settings)
    await seed(session_factory)
    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings
    dispatcher["session_factory"] = session_factory
    database = DatabaseMiddleware(session_factory)
    dispatcher.update.outer_middleware(database)
    dispatcher.include_routers(admin_router(), user_router())

    @dispatcher.error()
    async def error_handler(event: ErrorEvent) -> bool:
        logger.exception("Unhandled update error", exc_info=event.exception)
        if event.update.callback_query:
            await event.update.callback_query.answer("⚠️ Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
        elif event.update.message:
            await event.update.message.answer("⚠️ Произошла ошибка. Попробуйте ещё раз.")
        return True

    logger.info("Bot polling started")
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
