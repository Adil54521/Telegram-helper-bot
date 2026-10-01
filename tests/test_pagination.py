import unittest

from app.utils.pagination import PAGE_SIZE, paginate


class PaginationTests(unittest.TestCase):
    def test_boundaries_and_slices(self) -> None:
        for count, expected_pages in ((0, 1), (1, 1), (8, 1), (9, 2), (16, 2), (17, 3)):
            result = paginate(list(range(count)), 0)
            self.assertEqual(result.pages, expected_pages)
            self.assertEqual(len(result.items), min(PAGE_SIZE, count))

    def test_page_is_clamped_after_last_item_deleted(self) -> None:
        result = paginate(list(range(16)), 2)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.items, list(range(8, 16)))

    def test_negative_page_is_clamped(self) -> None:
        self.assertEqual(paginate(list(range(17)), -10).page, 0)

