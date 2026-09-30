from aiogram import Router

from app.handlers.user import ai, faq, pages, start


def router() -> Router:
    result = Router(name="user")
    result.include_routers(start.router, faq.router, ai.router, pages.router)
    return result
