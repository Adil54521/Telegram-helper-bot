from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.callbacks import AdminMenuCallback, AdminPageCallback
from app.utils.pagination import paginate
from app.database.models import BotPage
from app.services.permissions import Permission, ROLE_PERMISSIONS
from app.database.models.enums import AdminRole


def dashboard(role: AdminRole) -> InlineKeyboardMarkup:
    allowed = ROLE_PERMISSIONS[role]
    definitions = [
        (Permission.CONTENT_VIEW, "📄 Страницы", "pages"),
        (Permission.CONTENT_MANAGE, "🔘 Меню и кнопки", "buttons"),
        (Permission.CONTENT_VIEW, "📥 Telegram-публикации", "channel_posts"),
        (Permission.FAQ_MANAGE, "❓ FAQ", "faq"),
        (Permission.BROADCAST_MANAGE, "📢 Рассылка", "broadcasts"),
        (Permission.ADMINS_MANAGE, "👥 Администраторы", "admins"),
        (Permission.STATISTICS_VIEW, "📊 Статистика", "statistics"),
    ]

    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=AdminMenuCallback(
                    section=section
                ).pack(),
            )
        ]
        for permission, label, section in definitions
        if permission in allowed
    ]

    if allowed.intersection({
        Permission.AI_ANALYTICS_VIEW,
        Permission.AI_DIAGNOSTICS,
        Permission.AI_HISTORY_VIEW,
        Permission.AI_INDEX_SYNC,
    }):
        rows.append([
            InlineKeyboardButton(
                text="🤖 ИИ-ассистент",
                callback_data=AdminMenuCallback(
                    section="ai"
                ).pack(),
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def pages_list(pages: list[BotPage], current: int = 0) -> InlineKeyboardMarkup:
    result=paginate(pages,current)
    rows = [[InlineKeyboardButton(text=page.draft_revision.title if page.draft_revision else page.key, callback_data=AdminPageCallback(action="open", page_id=page.id,page=result.page).pack())] for page in result.items]
    if result.pages>1:rows.append([InlineKeyboardButton(text="⬅️",callback_data=AdminPageCallback(action="list",page_id=0,page=max(0,result.page-1)).pack()),InlineKeyboardButton(text=f"{result.page+1} / {result.pages}",callback_data="noop"),InlineKeyboardButton(text="➡️",callback_data=AdminPageCallback(action="list",page_id=0,page=min(result.pages-1,result.page+1)).pack())])
    rows.append([InlineKeyboardButton(text="➕ Создать страницу", callback_data=AdminPageCallback(action="create", page_id=0).pack())])
    rows.append([InlineKeyboardButton(text="⬅️ Панель", callback_data=AdminMenuCallback(section="dashboard").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_actions(page_id: int) -> InlineKeyboardMarkup:
    buttons = [
        ("🇷🇺 Русский", "lang_ru"), ("🇰🇿 Қазақша", "lang_kk"), ("📎 Вложения", "attachments"), ("🔘 Кнопки", "buttons"),
        ("👁 Предпросмотр", "preview"), ("🚀 Опубликовать", "publish"), ("🗑 Удалить", "delete_ask"),
        ("⬅️ Назад", "back"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=AdminPageCallback(action=action, page_id=page_id).pack())] for label, action in buttons])


def confirm_edit(page_id: int, field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data=AdminPageCallback(action=f"save_{field}", page_id=page_id).pack())],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=AdminPageCallback(action="cancel", page_id=page_id).pack())],
    ])
