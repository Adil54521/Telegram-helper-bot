from typing import Any

SUPPORTED_LANGUAGES = ("ru", "kk")
DEFAULT_LANGUAGE = "ru"

LOCALES = {
    "ru": {"choose_language":"Выберите язык", "back":"⬅️ Назад", "main_menu":"Главное меню", "faq":"❓ FAQ", "change_language":"🌐 Сменить язык", "unavailable":"Раздел временно недоступен.", "faq_unavailable":"Вопрос недоступен"},
    "kk": {"choose_language":"Тілді таңдаңыз", "back":"⬅️ Артқа", "main_menu":"Басты мәзір", "faq":"❓ Жиі қойылатын сұрақтар", "change_language":"🌐 Тілді ауыстыру", "unavailable":"Бөлім уақытша қолжетімсіз.", "faq_unavailable":"Сұрақ қолжетімсіз"},
}


def normalize_language(language: str | None) -> str:
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, language: str | None) -> str:
    code = normalize_language(language)
    return LOCALES.get(code, LOCALES[DEFAULT_LANGUAGE]).get(key, LOCALES[DEFAULT_LANGUAGE].get(key, key))


def translated(items: list[Any], language: str | None) -> Any | None:
    code = normalize_language(language)
    def item_code(item: Any) -> str:
        return str(getattr(item.language_code, "value", item.language_code)).lower()
    return next((item for item in items if item_code(item) == code), None) or next((item for item in items if item_code(item) == DEFAULT_LANGUAGE), None)


def attachment_caption(attachment: Any, language: str | None) -> str | None:
    code = normalize_language(language)
    return getattr(attachment, f"caption_{code}", None) or getattr(attachment, "caption_ru", None)
