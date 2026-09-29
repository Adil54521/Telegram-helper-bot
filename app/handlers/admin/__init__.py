from aiogram import Router

from app.handlers.admin import admins, attachments, broadcasts, buttons, dashboard, faq, pages, statistics


def router() -> Router:
    result = Router(name="admin")
    result.include_routers(dashboard.router, attachments.router, buttons.router, pages.router, faq.router, admins.router, broadcasts.router, statistics.router)
    return result
