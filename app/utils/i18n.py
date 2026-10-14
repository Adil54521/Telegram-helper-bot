from typing import Any
from app.utils.presentation import html_text

SUPPORTED_LANGUAGES = ("ru", "kk")
DEFAULT_LANGUAGE = "ru"

LOCALES = {
    "ru": {
        "choose_language": "Выберите язык", 
        "back": "⬅️ Назад", 
        "main_menu":"Главное меню", 
        "faq":"❓ FAQ", 
        "change_language":"🌐 Сменить язык", 
        "unavailable":"Раздел временно недоступен.", 
        "faq_unavailable":"Вопрос недоступен", 
        "materials":"Материалы", 
        "materials_total":"Всего", 
        "materials_choose":"Выберите материал", 
        "materials_empty":"Материалы недоступны или отсутствуют.", 
        "material_document":"Документ", 
        "material_photo":"Изображение", 
        "back_to_page":"⬅️ К странице", 
        "ask_ai":"🤖 Спросить ИИ", 
        "ai_prompt":"🤖 Задайте вопрос об университете.\n\nНапример:\n«Какие программы есть по IT?»", 
        "cancel":"❌ Отмена", 
        "finish_ai":"🏠 Завершить и в меню", 
        "ai_no_info":"🤖 В базе бота пока нет подтверждённой информации по этому вопросу.\n\nПопробуйте сформулировать вопрос иначе или воспользуйтесь разделами меню.", 
        "ai_unavailable":"⚠️ ИИ-ассистент временно недоступен. Попробуйте позже.", 
        "ai_text_only":"Пожалуйста, отправьте вопрос текстом.", 
        "ai_too_long":"Вопрос слишком длинный. Пожалуйста, сократите его до 1000 символов.", 
        "ai_cooldown":"Пожалуйста, подождите несколько секунд перед следующим вопросом.", 
        "ai_processing": "⏳ Ответ ещё обрабатывается.",
        "ai_source":"📚 По материалам базы KUAM.",
        "my_questions": "🕘 Мои вопросы",
        "my_questions_title": "🕘 История моих вопросов",
        "my_questions_total": "Всего вопросов",
        "my_questions_page": "Страница",
        "my_questions_choose": "Выберите вопрос:",
        "my_questions_empty": "Вы ещё не задавали вопросов ИИ.",
        "my_questions_back": "⬅️ К моим вопросам",
        "my_question_title": "🕘 Мой вопрос",
        "my_question_label": "Вопрос",
        "my_answer_label": "Ответ",
        "my_answer_empty": "Информация не найдена в базе знаний.",
        "my_question_not_found": "Вопрос не найден.",
    },
    "kk": {
        "choose_language":"Тілді таңдаңыз", 
        "back":"⬅️ Артқа", 
        "main_menu":"Басты мәзір", 
        "faq":"❓ Жиі қойылатын сұрақтар", 
        "change_language":"🌐 Тілді ауыстыру", 
        "unavailable":"Бөлім уақытша қолжетімсіз.", 
        "faq_unavailable":"Сұрақ қолжетімсіз", 
        "materials":"Материалдар", 
        "materials_total":"Барлығы", "materials_choose":"Материалды таңдаңыз", 
        "materials_empty":"Материалдар қолжетімсіз немесе жоқ.", 
        "material_document":"Құжат", 
        "material_photo":"Кескін", 
        "back_to_page":"⬅️ Бетке оралу", 
        "ask_ai":"🤖 AI-дан сұрау", 
        "ai_prompt":"🤖 Университет туралы сұрағыңызды қойыңыз.\n\nМысалы:\n«IT бойынша қандай бағдарламалар бар?»", 
        "cancel":"❌ Бас тарту", 
        "finish_ai":"🏠 Аяқтап, мәзірге оралу", 
        "ai_no_info":"🤖 Боттың дерекқорында бұл сұрақ бойынша расталған ақпарат әзірге жоқ.\n\nСұрақты басқаша қойып көріңіз немесе мәзір бөлімдерін пайдаланыңыз.", 
        "ai_unavailable":"⚠️ AI-ассистент уақытша қолжетімсіз. Кейінірек қайталап көріңіз.", 
        "ai_text_only":"Сұрақты мәтін түрінде жіберіңіз.", 
        "ai_too_long":"Сұрақ тым ұзын. Оны 1000 таңбаға дейін қысқартыңыз.", 
        "ai_cooldown":"Келесі сұрақтың алдында бірнеше секунд күтіңіз.", 
        "ai_processing": "⏳ Жауап әлі өңделуде.",
        "ai_source":"📚 KUAM дерекқоры материалдары бойынша.",
        "my_questions": "🕘 Менің сұрақтарым",
        "my_questions_title": "🕘 Сұрақтарымның тарихы",
        "my_questions_total": "Сұрақтар саны",
        "my_questions_page": "Бет",
        "my_questions_choose": "Сұрақты таңдаңыз:",
        "my_questions_empty": "Сіз әлі AI-ға сұрақ қойған жоқсыз.",
        "my_questions_back": "⬅️ Сұрақтарыма оралу",
        "my_question_title": "🕘 Менің сұрағым",
        "my_question_label": "Сұрақ",
        "my_answer_label": "Жауап",
        "my_answer_empty": "Білім қорынан ақпарат табылмады.",
        "my_question_not_found": "Сұрақ табылмады.",
    },
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
    value = getattr(attachment, f"caption_{code}", None) or getattr(attachment, "caption_ru", None)
    return html_text(value) if value else None
