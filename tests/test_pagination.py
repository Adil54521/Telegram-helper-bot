import unittest

from app.utils.pagination import PAGE_SIZE, paginate, paginate_count


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

    def test_first_page(self) -> None:
        result = paginate(
            list(range(20)),
            page=0,
            page_size=5,
        )

        self.assertEqual(result.items, [0, 1, 2, 3, 4])
        self.assertEqual(result.page, 0)
        self.assertEqual(result.pages, 4)
        self.assertEqual(result.total, 20)

    def test_last_page(self) -> None:
        result = paginate(
            list(range(12)),
            page=2,
            page_size=5,
        )

        self.assertEqual(result.items, [10, 11])
        self.assertEqual(result.pages, 3)

    def test_empty_list(self) -> None:
        result = paginate([], page=0)

        self.assertEqual(result.items, [])
        self.assertEqual(result.pages, 1)
        self.assertEqual(result.total, 0)

    def test_page_out_of_range(self) -> None:
        page, pages = paginate_count(
            total=12,
            page=100,
            page_size=5,
        )

        self.assertEqual(page, 2)
        self.assertEqual(pages, 3)

    def test_negative_page_count(self) -> None:
        page, pages = paginate_count(
            total=12,
            page=-5,
            page_size=5,
        )

        self.assertEqual(page, 0)
        self.assertEqual(pages, 3)

    def test_invalid_page_size(self) -> None:
        with self.assertRaises(ValueError):
            paginate_count(
                total=10,
                page=0,
                page_size=0,
            )


if __name__ == "__main__":
    unittest.main()