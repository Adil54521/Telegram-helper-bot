from aiogram import Router

from app.handlers.user import faq, pages, start


def router() -> Router:
    result = Router(name="user")
    result.include_routers(start.router, faq.router, pages.router)
    return result
