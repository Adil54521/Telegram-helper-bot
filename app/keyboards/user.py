from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.callbacks import LanguageCallback, UserAICallback, UserAttachmentCallback, UserNavigationCallback, UserPageCallback
from app.database.models import BotButton
from app.database.models.enums import ButtonAction
from app.utils.i18n import t, translated


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇰🇿 Қазақша",
                    callback_data=LanguageCallback(code="kk").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🇷🇺 Русский",
                    callback_data=LanguageCallback(code="ru").pack(),
                )
            ],
        ]
    )


def _button_label(
    button: BotButton,
    language: str,
) -> str:
    """
    Возвращает локализованный текст кнопки.
    Если перевода нет — используется основной text.
    """

    translation = translated(
        button.translations,
        language,
    )

    label = (
        translation.text
        if translation
        else button.text
    )

    return label


def _build_button(
    button: BotButton,
    language: str,
) -> InlineKeyboardButton:
    """
    Преобразует BotButton из БД
    в Telegram InlineKeyboardButton.
    """

    label = _button_label(
        button,
        language,
    )

    # =====================================================
    # PAGE
    # =====================================================

    if button.action_type == ButtonAction.PAGE:
        target = button.target or "main"

        return InlineKeyboardButton(
            text=label,
            callback_data=UserPageCallback(
                key=target,
            ).pack(),
        )

    # =====================================================
    # BACK
    # =====================================================

    if button.action_type == ButtonAction.BACK:
        return InlineKeyboardButton(
            text=label,
            callback_data=UserNavigationCallback(action="back").pack(),
        )

    # =====================================================
    # HOME
    # =====================================================

    if button.action_type == ButtonAction.HOME:
        return InlineKeyboardButton(
            text=label,
            callback_data=UserNavigationCallback(action="home").pack(),
        )

    # =====================================================
    # URL
    # =====================================================

    if button.action_type == ButtonAction.URL:
        url = button.target or "https://telegram.org"

        return InlineKeyboardButton(
            text=label,
            url=url,
        )

    if button.action_type == ButtonAction.PHONE:
        return InlineKeyboardButton(
            text=label,
            callback_data=UserPageCallback(
                key=f"phone_{button.id}",
            ).pack(),
        )

    if button.action_type == ButtonAction.EMAIL:
        return InlineKeyboardButton(
            text=label,
            callback_data=UserPageCallback(
                key=f"email_{button.id}",
            ).pack(),
        )

    # =====================================================
    # TELEGRAM
    # =====================================================

    if button.action_type == ButtonAction.TELEGRAM:
        username = (button.target or "").strip()

        username = (
            username
            .removeprefix("https://t.me/")
            .removeprefix("http://t.me/")
            .lstrip("@")
            .strip("/")
        )

        return InlineKeyboardButton(
            text=label,
            url=f"https://t.me/{username}",
        )

    # =====================================================
    # FALLBACK
    # =====================================================

    # Если в БД каким-то образом появился неизвестный тип,
    # не роняем построение всей клавиатуры.
    return InlineKeyboardButton(
        text=label,
        callback_data=UserPageCallback(
            key="main",
        ).pack(),
    )


def page_keyboard(
    buttons: list[BotButton],
    language: str = "ru",
    ai_available: bool = False,
    page_key: str = "main",
    attachment_count: int = 0,
    page_id: int = 0,
) -> InlineKeyboardMarkup:
    """
    Создаёт пользовательскую клавиатуру страницы.
    """

    rows: dict[int, list[InlineKeyboardButton]] = {}

    for button in buttons:
        # Неактивные кнопки пользователю не показываем.
        if not button.is_active or button.action_type == ButtonAction.BACK:
            continue

        item = _build_button(
            button,
            language,
        )

        rows.setdefault(
            button.row,
            [],
        ).append(item)

    # Сортируем строки клавиатуры.
    result = [
        rows[row]
        for row in sorted(rows)
    ]

    if attachment_count > 0 and page_id > 0:
        result.append([InlineKeyboardButton(text=f"📎 {t('materials', language)} ({attachment_count})", callback_data=UserAttachmentCallback(action="list", page_id=page_id).pack())])

    if page_key == "main" and ai_available:
        result.append([InlineKeyboardButton(text=t("ask_ai", language), callback_data=UserAICallback(action="ask").pack())])

    # Системная кнопка смены языка.
    result.append(
        [
            InlineKeyboardButton(
                text=t(
                    "change_language",
                    language,
                ),
                callback_data=UserPageCallback(
                    key="language",
                ).pack(),
            )
        ]
    )

    if page_key != "main":
        result.append(
            [
                InlineKeyboardButton(
                    text=t("back", language),
                    callback_data=UserNavigationCallback(action="back").pack(),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=result)
