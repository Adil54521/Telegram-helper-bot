from aiogram import Router

from app.handlers.admin import admins, ai, attachments, broadcasts, buttons, channel_posts, dashboard, faq, pages, statistics, ai_history


def router() -> Router:
    result = Router(name="admin")
    result.include_routers(dashboard.router, ai.router, channel_posts.router, attachments.router, buttons.router, pages.router, faq.router, admins.router, broadcasts.router, statistics.router, ai_history.router)
    return result
