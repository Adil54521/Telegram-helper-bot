from aiogram import Router

from app.handlers.user import ai, attachments, fallback, faq, pages, start


def router() -> Router:
    result = Router(name="user")
    result.include_routers(start.router, faq.router, attachments.router, ai.router, pages.router, fallback.router)
    return result
