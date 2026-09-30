import unittest

from app.services.faq_csv import faq_template_csv, normalize_question, parse_faq_csv


class FAQCSVTests(unittest.TestCase):
    def test_normalized_duplicates_are_skipped(self) -> None:
        content = (
            "question_ru,answer_ru,question_kk,answer_kk,is_active\n"
            '"  какие   документы НУЖНЫ? ",Ответ,,,true\n'
            "Новый вопрос,Новый ответ,,,false\n"
            " новый   ВОПРОС ,Повтор внутри CSV,,,true\n"
        ).encode("utf-8")
        preview = parse_faq_csv(content, {"Какие документы нужны?"})
        self.assertEqual(preview.total_rows, 3)
        self.assertEqual(len(preview.new_rows), 1)
        self.assertEqual(len(preview.duplicates), 2)
        self.assertFalse(preview.new_rows[0].is_active)

    def test_invalid_rows_are_reported_without_becoming_new(self) -> None:
        content = ("question_ru,answer_ru,is_active\nВопрос,,true\nДругой,Ответ,maybe\n").encode("utf-8")
        preview = parse_faq_csv(content, set())
        self.assertEqual(len(preview.new_rows), 0)
        self.assertEqual(len(preview.errors), 2)

    def test_template_uses_utf8_bom(self) -> None:
        content = faq_template_csv()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("question_ru", content.decode("utf-8-sig"))

    def test_normalize_question(self) -> None:
        self.assertEqual(normalize_question("  ВОПРОС   Один "), "вопрос один")
