import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.callbacks import UserFAQCallback
from app.database.repositories.cms import CMSRepository
from app.handlers.user.faq import faq_list_keyboard


def faq_items(count: int):
    return [SimpleNamespace(id=index + 1, question=f"Question {index + 1}", translations=[]) for index in range(count)]


class UserFAQPaginationTests(unittest.TestCase):
    def test_boundaries_and_number_of_question_buttons(self) -> None:
        for count, expected_pages in ((0, 1), (1, 1), (8, 1), (9, 2), (17, 3)):
            keyboard, result = faq_list_keyboard(faq_items(count), "ru", 0)
            self.assertEqual(result.pages, expected_pages)
            question_rows = [row for row in keyboard.inline_keyboard if row[0].callback_data.startswith("uf:") and UserFAQCallback.unpack(row[0].callback_data).item_id > 0]
            self.assertLessEqual(len(question_rows), 8)
            navigation_rows = [row for row in keyboard.inline_keyboard if len(row) == 3]
            self.assertEqual(bool(navigation_rows), count > 8)

    def test_question_and_back_preserve_current_page(self) -> None:
        keyboard, result = faq_list_keyboard(faq_items(17), "ru", 1)
        question = UserFAQCallback.unpack(keyboard.inline_keyboard[0][0].callback_data)
        self.assertEqual(question.page, 1)
        back = UserFAQCallback(item_id=0, page=question.page)
        self.assertEqual(UserFAQCallback.unpack(back.pack()).page, 1)
        self.assertEqual(result.page, 1)

    def test_out_of_range_page_is_clamped(self) -> None:
        keyboard, result = faq_list_keyboard(faq_items(17), "ru", 99)
        self.assertEqual(result.page, 2)
        question = UserFAQCallback.unpack(keyboard.inline_keyboard[0][0].callback_data)
        self.assertEqual(question.page, 2)

    def test_callback_size_is_below_telegram_limit(self) -> None:
        packed = UserFAQCallback(item_id=9_223_372_036_854_775_807, page=999_999_999).pack()
        self.assertLess(len(packed.encode("utf-8")), 64)


class UserFAQRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_only_query_excludes_inactive_faq(self) -> None:
        scalars = SimpleNamespace(all=lambda: [])
        session = SimpleNamespace(scalars=AsyncMock(return_value=scalars))
        await CMSRepository(session).faqs(active_only=True)
        query = session.scalars.await_args.args[0]
        self.assertIn("faq_items.is_active IS true", str(query))


if __name__ == "__main__":
    unittest.main()
