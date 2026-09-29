from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.callbacks import AdminMenuCallback, AdminPageCallback
from app.database.models import BotPage
from app.services.permissions import Permission, ROLE_PERMISSIONS
from app.database.models.enums import AdminRole


def dashboard(role: AdminRole) -> InlineKeyboardMarkup:
    allowed = ROLE_PERMISSIONS[role]
    definitions = [
        (Permission.CONTENT, "📄 Страницы", "pages"),
        (Permission.CONTENT, "🔘 Меню и кнопки", "buttons"),
        (Permission.FAQ, "❓ FAQ", "faq"),
        (Permission.BROADCAST, "📢 Рассылка", "broadcasts"),
        (Permission.ADMINS, "👥 Администраторы", "admins"),
        (Permission.STATISTICS, "📊 Статистика", "statistics"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=AdminMenuCallback(section=section).pack())] for permission, label, section in definitions if permission in allowed])


def pages_list(pages: list[BotPage]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=page.draft_revision.title if page.draft_revision else page.key, callback_data=AdminPageCallback(action="open", page_id=page.id).pack())] for page in pages]
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
