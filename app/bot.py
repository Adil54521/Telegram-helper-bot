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
from app.ai.knowledge import KnowledgeService
from app.ai.index import KnowledgeIndexService
from app.ai.provider import GeminiProvider
from app.ai.service import AIService
from app.utils.logging import configure_logging


async def main() -> None:
    settings = get_settings()
    log_dir = configure_logging(settings)
    logger = logging.getLogger(__name__)
    logger.info("Logging initialized dir=%s", log_dir)
    logger.info("Bot starting")
    engine, session_factory = create_session_factory(settings)
    await seed(session_factory)
    logger.info("Database initialized")
    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings
    dispatcher["session_factory"] = session_factory
    ai_service = None
    if settings.ai_available and settings.gemini_api_key:
        provider = GeminiProvider(
            settings.gemini_api_key.get_secret_value(), settings.gemini_model, settings.ai_timeout_seconds,
            embedding_model=settings.ai_embedding_model,
            embedding_dimension=settings.ai_embedding_dimension,
        )
        knowledge = KnowledgeService(
            provider,
            semantic_enabled=settings.ai_semantic_search_enabled,
        )
        ai_service = AIService(
            provider,
            settings.ai_cooldown_seconds,
            knowledge,
            provider_name=settings.ai_provider,
            model=settings.gemini_model,
            input_price_per_1m=settings.gemini_input_price_per_1m,
            output_price_per_1m=settings.gemini_output_price_per_1m,
        )
        ai_service.knowledge_index = KnowledgeIndexService(provider, settings.ai_embedding_model)
        logger.info("AI assistant enabled provider=%s model=%s", settings.ai_provider, settings.gemini_model)
    elif settings.ai_enabled:
        logger.warning("AI assistant requested but configuration is incomplete; AI remains unavailable")
    dispatcher["ai_service"] = ai_service
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
        if ai_service:
            await ai_service.close()
        await bot.session.close()
        await engine.dispose()  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
