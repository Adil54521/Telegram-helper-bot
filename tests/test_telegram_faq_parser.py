import unittest

from app.services.telegram_content_parser import TelegramContentConfidence
from app.services.telegram_faq_parser import TelegramFAQParser


KK_RU = """❓ UTS жүйесіне қалай кіруге болады?

UTS жүйесіне университет берген
логин және құпиясөз арқылы кіруге болады.

━━━━━━━━━━━━━━

❓ Как войти в UTS?

Для входа используйте логин и пароль,
выданные университетом. https://uts.kuam.kz"""


class TelegramFAQParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TelegramFAQParser()

    def test_main_format_kk_ru_preserves_multiline_and_url(self):
        value = self.parser.parse(KK_RU)
        self.assertTrue(value.is_complete)
        self.assertEqual(value.confidence, TelegramContentConfidence.HIGH)
        self.assertEqual(value.question_kk, "UTS жүйесіне қалай кіруге болады?")
        self.assertIn("логин және құпиясөз", value.answer_kk)
        self.assertEqual(value.question_ru, "Как войти в UTS?")
        self.assertIn("https://uts.kuam.kz", value.answer_ru)
        self.assertNotIn("━", value.answer_ru + value.answer_kk)

    def test_main_format_ru_kk(self):
        first, second = KK_RU.split("━━━━━━━━━━━━━━")
        value = self.parser.parse(second.strip() + "\n\n━━━━━━━━━━━━━━\n\n" + first.strip())
        self.assertTrue(value.is_complete)
        self.assertEqual(value.question_ru, "Как войти в UTS?")
        self.assertEqual(value.question_kk, "UTS жүйесіне қалай кіруге болады?")

    def test_pipe_kuam_format(self):
        value = self.parser.parse("""👥 ҚАШЫҚТАН ОҚИТЫН СТУДЕНТТЕРГЕ ТОПТАР МЕН КУРАТОРЛАР БОЛА МА? | ЕСТЬ ЛИ ГРУППЫ И КУРАТОРЫ У СТУДЕНТОВ ДОТ?

Қашықтан оқыту бөлімінің студенттеріне топтар мен кураторлар қарастырылмаған.

Для студентов отделения дистанционного обучения группы и кураторы не предусмотрены.""")
        self.assertTrue(value.is_complete)
        self.assertIn("ҚАШЫҚТАН", value.question_kk)
        self.assertIn("ЕСТЬ ЛИ", value.question_ru)

    def test_incomplete_single_language_and_question_list_are_rejected(self):
        values = (
            "❓ Как поступить?",
            "❓ Как поступить?\n\nОтвет только по-русски.",
            "❓ Как поступить?\n\n❓ Какие документы нужны?",
            "Обычное объявление университета без вопроса и ответа.",
            "Значение A | значение B",
        )
        for source in values:
            with self.subTest(source=source):
                self.assertFalse(self.parser.parse(source).is_complete)
