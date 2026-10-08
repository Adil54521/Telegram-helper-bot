import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.service import AIService
from app.database.models import TelegramContentPost
from app.database.models.enums import TelegramContentStatus
from app.handlers.admin.channel_posts import post_card, prepare_manual
from app.services.telegram_publication_grouper import PublicationGroupingResult

from app.services.telegram_content_parser import (
    TelegramContentConfidence,
    TelegramContentParseKind,
    TelegramContentParser,
    extract_title_body,
    normalize_source_text,
)


FIXTURE_A = """5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ

Қазақ және орыс тілдеріндегі практика есебінің үлгілерін төменнен жүктеп алуға болады.

5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ

Образцы отчётов на казахском и русском языках можно скачать ниже."""

FIXTURE_B = """6️⃣ ПРАКТИКА БОЙЫНША МІНЕЗДЕМЕ ҮЛГІСІ

Практика бойынша мінездеме үлгісін төменнен жүктеп алуға болады.

6️⃣ ОБРАЗЕЦ ХАРАКТЕРИСТИКИ ПО ПРАКТИКЕ

Образец характеристики по практике можно скачать ниже."""

FIXTURE_C = """📚 «ОҚИТЫН ҰЛТ» | ЦИФРЛЫҚ ПЛАТФОРМАЛАР

Құрметті студенттер!

Кітаптарды https://kitap.kz сайтынан оқуға болады.

━━━━━━━━━━━━━━

📚 «ЧИТАЮЩАЯ НАЦИЯ» | ЦИФРОВЫЕ ПЛАТФОРМЫ

Уважаемые студенты!

Книги доступны на сайте https://kitap.kz."""


class TelegramContentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TelegramContentParser()

    def test_real_kuam_fixture_a_repeated_numeric_marker(self):
        parsed = self.parser.parse(FIXTURE_A)
        self.assertEqual(parsed.kind, TelegramContentParseKind.BILINGUAL)
        self.assertEqual(parsed.confidence, TelegramContentConfidence.HIGH)
        self.assertEqual(parsed.title_kk, "5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ")
        self.assertEqual(parsed.text_kk, "Қазақ және орыс тілдеріндегі практика есебінің үлгілерін төменнен жүктеп алуға болады.")
        self.assertEqual(parsed.title_ru, "5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ")
        self.assertEqual(parsed.text_ru, "Образцы отчётов на казахском и русском языках можно скачать ниже.")

    def test_real_kuam_fixture_b(self):
        parsed = self.parser.parse(FIXTURE_B)
        self.assertEqual(parsed.kind, TelegramContentParseKind.BILINGUAL)
        self.assertIn("МІНЕЗДЕМЕ", parsed.title_kk or "")
        self.assertIn("ХАРАКТЕРИСТИКИ", parsed.title_ru or "")

    def test_real_kuam_fixture_c_separator_and_urls(self):
        parsed = self.parser.parse(FIXTURE_C)
        self.assertEqual(parsed.kind, TelegramContentParseKind.BILINGUAL)
        self.assertEqual(parsed.confidence, TelegramContentConfidence.HIGH)
        self.assertIn("https://kitap.kz", parsed.text_kk or "")
        self.assertIn("https://kitap.kz", parsed.text_ru or "")
        self.assertNotIn("━━", parsed.text_kk or "")
        self.assertNotIn("━━", parsed.text_ru or "")

    def test_ru_then_kk_without_matching_marker(self):
        source = """НОВОСТИ УНИВЕРСИТЕТА

Сегодня для студентов открылась новая цифровая библиотека.

УНИВЕРСИТЕТ ЖАҢАЛЫҚТАРЫ

Бүгін студенттер үшін жаңа цифрлық кітапхана ашылды."""
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.BILINGUAL)
        self.assertEqual(parsed.title_ru, "НОВОСТИ УНИВЕРСИТЕТА")
        self.assertEqual(parsed.title_kk, "УНИВЕРСИТЕТ ЖАҢАЛЫҚТАРЫ")

    def test_ru_only_with_bullets_numbers_and_date(self):
        source = """📌 ГРАФИКИ ПРОФЕССИОНАЛЬНОЙ ПРАКТИКИ

Графики на 2026–2027 учебный год:
• первый курс
• второй курс

Скачайте нужный файл ниже."""
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.RU_ONLY)
        self.assertIn("• первый курс", parsed.text_ru or "")
        self.assertIsNone(parsed.title_kk)

    def test_kk_only_with_english_product_name(self):
        source = """📌 ЦИФРЛЫҚ КІТАПХАНА

Құрметті студенттер, Google Books платформасын оқу үшін пайдалануға болады."""
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.KK_ONLY)
        self.assertIn("Google Books", parsed.text_kk or "")
        self.assertIsNone(parsed.title_ru)

    def test_ru_only_with_english_site_name(self):
        source = """ЦИФРОВАЯ БИБЛИОТЕКА

Студенты могут использовать Google Books для поиска учебной литературы."""
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.RU_ONLY)
        self.assertIn("Google Books", parsed.text_ru or "")

    def test_kazakh_with_russian_words_stays_kk(self):
        source = """СТУДЕНТТЕРГЕ АРНАЛҒАН АҚПАРАТ

Құрметті студенттер, практика және отчет үлгілерін төменнен жүктеп алуға болады."""
        self.assertEqual(self.parser.parse(source).kind, TelegramContentParseKind.KK_ONLY)

    def test_russian_with_word_kazakhstan_stays_ru(self):
        source = """ИНФОРМАЦИЯ ДЛЯ СТУДЕНТОВ

Новая образовательная программа университета Қазақстан доступна всем студентам."""
        self.assertEqual(self.parser.parse(source).kind, TelegramContentParseKind.RU_ONLY)

    def test_short_mixed_content_is_unknown(self):
        self.assertEqual(self.parser.parse("Қазақстан туралы информация").kind, TelegramContentParseKind.UNKNOWN)
        self.assertEqual(self.parser.parse("FAQ RU / KK").kind, TelegramContentParseKind.UNKNOWN)

    def test_empty_input_is_unknown(self):
        self.assertEqual(self.parser.parse("").kind, TelegramContentParseKind.UNKNOWN)

    def test_crlf_and_extra_blank_lines_are_normalized(self):
        source = "\r\n\r\nЗАГОЛОВОК НОВОСТИ\r\n\r\n\r\nПодробная информация для студентов университета.\r\n\r\n"
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.RU_ONLY)
        self.assertEqual(parsed.title_ru, "ЗАГОЛОВОК НОВОСТИ")
        self.assertNotIn("\r", normalize_source_text(source))

    def test_multiline_paragraph_is_preserved(self):
        source = """ВАЖНОЕ ОБЪЯВЛЕНИЕ

Первая строка подробного сообщения
продолжается на следующей строке без потери смысла."""
        parsed = self.parser.parse(source)
        self.assertIn("\n", parsed.text_ru or "")

    def test_title_without_emoji(self):
        parsed = self.parser.parse("НОВОСТИ УНИВЕРСИТЕТА\n\nПодробная информация опубликована для всех студентов.")
        self.assertEqual(parsed.title_ru, "НОВОСТИ УНИВЕРСИТЕТА")

    def test_url_is_not_used_as_title(self):
        title, body = extract_title_body("https://kuam.edu.kz\n\nПодробная информация для студентов университета")
        self.assertIsNone(title)
        self.assertIn("https://kuam.edu.kz", body or "")

    def test_filename_is_not_used_as_title(self):
        title, body = extract_title_body("practice.docx\n\nДокумент содержит необходимые материалы")
        self.assertIsNone(title)
        self.assertIn("practice.docx", body or "")

    def test_long_paragraph_is_not_used_as_title(self):
        paragraph = "Это обычный длинный абзац с большим количеством слов, который не должен ошибочно считаться заголовком публикации, потому что он представляет собой полноценное предложение с подробным описанием события."
        title, body = extract_title_body(paragraph)
        self.assertIsNone(title)
        self.assertEqual(body, paragraph)

    def test_markdown_like_characters_are_not_executed_or_removed(self):
        source = """📌 НОВЫЕ МАТЕРИАЛЫ

Смотрите **описание** и [ссылку](https://kuam.edu.kz) в публикации."""
        parsed = self.parser.parse(source)
        self.assertIn("**описание**", parsed.text_ru or "")
        self.assertIn("[ссылку](https://kuam.edu.kz)", parsed.text_ru or "")

    def test_repeated_emoji_marker_splits_without_blank_before_second_title(self):
        source = """📚 ОҚУ МАТЕРИАЛДАРЫ
Құрметті студенттер, оқу материалдарын төменнен жүктеп алуға болады.
📚 УЧЕБНЫЕ МАТЕРИАЛЫ
Уважаемые студенты, материалы можно скачать ниже."""
        parsed = self.parser.parse(source)
        self.assertEqual(parsed.kind, TelegramContentParseKind.BILINGUAL)
        self.assertEqual(parsed.confidence, TelegramContentConfidence.HIGH)
        self.assertEqual(parsed.title_ru, "📚 УЧЕБНЫЕ МАТЕРИАЛЫ")


class TelegramContentParserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _prepare_page(self, source: str) -> dict[str, object]:
        post = TelegramContentPost(
            id=15,
            chat_id=-1001,
            message_id=20,
            chat_type="supergroup",
            status=TelegramContentStatus.NEW,
            text=source,
            media=[],
        )
        callback = SimpleNamespace(message=SimpleNamespace(), answer=AsyncMock())
        callback_data = SimpleNamespace(item_id=15, page=2, status="NEW")
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), update_data=AsyncMock())
        settings = SimpleNamespace(content_chat_id=-1001)
        with (
            patch("app.handlers.admin.channel_posts.load_post", new=AsyncMock(return_value=post)),
            patch("app.handlers.admin.channel_posts.remember_wizard", new=AsyncMock()),
            patch("app.handlers.admin.channel_posts.show_draft", new=AsyncMock()),
            patch("app.handlers.admin.channel_posts.group_post", new=AsyncMock(return_value=PublicationGroupingResult(15, ()))),
            patch.object(AIService, "prepare_channel_content", new=AsyncMock()) as ai_mock,
        ):
            await prepare_manual(callback, callback_data, object(), settings, state, "PAGE")  # type: ignore[arg-type]
        ai_mock.assert_not_awaited()
        callback.answer.assert_awaited_once()
        return state.update_data.await_args.kwargs

    async def test_bilingual_page_prefills_both_languages_without_ai(self):
        data = await self._prepare_page(FIXTURE_A)
        self.assertEqual(data["channel_title_kk"], "5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ")
        self.assertIn("Қазақ және", data["channel_text_kk"])
        self.assertEqual(data["channel_title_ru"], "5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ")
        self.assertIn("Образцы отчётов", data["channel_text_ru"])

    async def test_ru_only_page_prefills_only_ru(self):
        source = """📌 ГРАФИКИ ПРОФЕССИОНАЛЬНОЙ ПРАКТИКИ

Графики профессиональной практики опубликованы для студентов университета."""
        data = await self._prepare_page(source)
        self.assertEqual(data["channel_title_ru"], "📌 ГРАФИКИ ПРОФЕССИОНАЛЬНОЙ ПРАКТИКИ")
        self.assertIsNone(data["channel_title_kk"])
        self.assertIsNone(data["channel_text_kk"])

    async def test_unknown_page_keeps_existing_raw_fallback(self):
        source = "Спасибо!"
        data = await self._prepare_page(source)
        self.assertEqual(data["channel_title_ru"], "Спасибо!")
        self.assertEqual(data["channel_text_ru"], source)
        self.assertIsNone(data["channel_title_kk"])

    def test_admin_card_contains_parsed_and_raw_previews(self):
        post = TelegramContentPost(
            id=15,
            chat_id=-1001,
            message_id=20,
            chat_type="supergroup",
            status=TelegramContentStatus.NEW,
            text=FIXTURE_A,
            media=[],
        )
        text, _ = post_card(post, 0, "NEW")
        self.assertIn("Қазақша + 🇷🇺 Русский", text)
        self.assertIn("Исходный текст", text)
        self.assertIn("ПРАКТИКА БОЙЫНША", text)


if __name__ == "__main__":
    unittest.main()
