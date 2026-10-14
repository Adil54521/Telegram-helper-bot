import csv
import io
import unittest

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_history_csv import (
    AIHistoryExportError,
    export_ai_history_csv,
    generate_ai_history_csv,
)


MODULE = "app.services.ai_history_csv"


def make_record(record_id: int):
    return SimpleNamespace(
        id=record_id,
        user_id=10,
        question="Қазақша сұрақ: ә ғ қ ң ө ұ ү һ і",
        answer='Ответ Gemini,\n"вторая строка"',
        language_code="kk",
        status="COMPLETED",
        created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        answered_at=None,
    )


class AIHistoryCSVTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.session = MagicMock()

        self.repo = MagicMock()
        self.repo.count = AsyncMock(return_value=0)
        self.repo.list_recent = AsyncMock(return_value=[])

        self.patcher = patch(
            f"{MODULE}.AIHistoryRepository",
            return_value=self.repo,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_csv_unicode_and_multiline(self) -> None:
        content = export_ai_history_csv([make_record(1)])

        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))

        rows = list(csv.reader(
            io.StringIO(content.decode("utf-8-sig"))
        ))

        self.assertEqual(len(rows), 2)
        self.assertIn("ә ғ қ ң ө ұ ү һ і", rows[1][2])
        self.assertEqual(
            rows[1][3],
            'Ответ Gemini,\n"вторая строка"',
        )

    async def test_empty_export(self) -> None:
        content = await generate_ai_history_csv(
            self.session,
        )

        rows = list(csv.reader(
            io.StringIO(content.decode("utf-8-sig"))
        ))

        self.assertEqual(len(rows), 1)
        self.repo.list_recent.assert_not_awaited()

    async def test_status_filter(self) -> None:
        self.repo.count.return_value = 1
        self.repo.list_recent.return_value = [make_record(1)]

        await generate_ai_history_csv(
            self.session,
            status="COMPLETED",
        )

        self.repo.count.assert_awaited_once_with(
            status="COMPLETED",
        )

        self.repo.list_recent.assert_awaited_once_with(
            limit=1,
            offset=0,
            status="COMPLETED",
        )

    async def test_batch_loading(self) -> None:
        self.repo.count.return_value = 501

        self.repo.list_recent.side_effect = [
            [make_record(i) for i in range(1, 501)],
            [make_record(501)],
        ]

        content = await generate_ai_history_csv(
            self.session,
        )

        self.assertEqual(self.repo.list_recent.await_count, 2)

        rows = list(csv.reader(
            io.StringIO(content.decode("utf-8-sig"))
        ))

        self.assertEqual(len(rows), 502)

    async def test_too_many_records(self) -> None:
        self.repo.count.return_value = 50_001

        with self.assertRaises(AIHistoryExportError):
            await generate_ai_history_csv(self.session)

        self.repo.list_recent.assert_not_awaited()

    async def test_incomplete_batch(self) -> None:
        self.repo.count.return_value = 5
        self.repo.list_recent.return_value = []

        with self.assertRaises(AIHistoryExportError):
            await generate_ai_history_csv(self.session)

    async def test_file_size_limit(self) -> None:
        self.repo.count.return_value = 1
        self.repo.list_recent.return_value = [make_record(1)]

        with patch(f"{MODULE}.MAX_EXPORT_BYTES", 10):
            with self.assertRaises(AIHistoryExportError):
                await generate_ai_history_csv(self.session)

    def test_csv_formula_injection(self) -> None:
        from app.services.csv_export import safe_csv_value

        dangerous = [
            "=1+1",
            "+SUM(1,2)",
            "-10+20",
            "@SUM(1,2)",
            "\t=1+1",
            "\n=1+1",
            "  =1+1",
        ]

        for value in dangerous:
            with self.subTest(value=repr(value)):
                result = safe_csv_value(value)
                self.assertTrue(result.startswith("'"))


if __name__ == "__main__":
    unittest.main()