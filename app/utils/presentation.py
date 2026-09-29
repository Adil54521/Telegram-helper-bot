from html import escape
from typing import Any


ACTION_PRESENTATION = {
    "PAGE": ("📄", "Открыть страницу", "Страница"),
    "URL": ("🌐", "Открыть сайт", "Адрес"),
    "BACK": ("⬅️", "Назад", None),
    "HOME": ("🏠", "Главное меню", None),
    "PHONE": ("📞", "Телефон", "Номер"),
    "EMAIL": ("✉️", "Email", "Адрес"),
    "TELEGRAM": ("💬", "Telegram", "Аккаунт"),
}


def text_preview(value: str | None, limit: int = 180) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def html_text(value: Any) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def _action_value(action: Any) -> str:
    return str(getattr(action, "value", action)).upper()


def button_action_icon(action: Any) -> str:
    return ACTION_PRESENTATION.get(_action_value(action), ("🔘", "Действие", None))[0]


def button_action_label(action: Any) -> str:
    icon, label, _ = ACTION_PRESENTATION.get(_action_value(action), ("🔘", "Действие", None))
    return f"{icon} {label}"


def button_action_details(action: Any, target: str | None) -> str:
    action_value = _action_value(action)
    _, _, target_label = ACTION_PRESENTATION.get(action_value, ("🔘", "Действие", "Значение"))
    heading = button_action_label(action)
    if not target_label:
        return heading
    display_target = target
    if action_value == "TELEGRAM" and target and not target.startswith("@"):
        display_target = f"@{target}"
    return f"{heading}\n\n<b>{target_label}:</b> {html_text(display_target)}"
