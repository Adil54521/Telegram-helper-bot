import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.database.repositories.ai_history import AIHistoryRepository
from app.database.models.entities import AIConversation


class AIHistoryRepositoryTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.session = MagicMock()
        self.session.scalar = AsyncMock()
        self.session.scalars = AsyncMock()

        self.repo = AIHistoryRepository(self.session)

    async def test_user_can_get_own_question(self) -> None:
        record = SimpleNamespace(
            id=10,
            user_id=1,
            question="Как получить справку?",
            answer="Обратитесь в деканат.",
        )

        self.session.scalar.return_value = record

        result = await self.repo.get_for_user(
            record_id=10,
            user_id=1,
        )

        self.assertEqual(result.id, 10)
        self.assertEqual(result.user_id, 1)

    async def test_foreign_question_is_not_returned(self) -> None:
        self.session.scalar.return_value = None

        result = await self.repo.get_for_user(
            record_id=10,
            user_id=2,
        )

        self.assertIsNone(result)

    async def test_count_for_user(self) -> None:
        self.session.scalar.return_value = 5

        total = await self.repo.count_for_user(user_id=1)

        self.assertEqual(total, 5)

    async def test_empty_history(self) -> None:
        self.session.scalar.return_value = 0

        total = await self.repo.count_for_user(user_id=1)

        self.assertEqual(total, 0)

    async def test_list_for_user(self) -> None:
        records = [
            SimpleNamespace(id=3, user_id=1),
            SimpleNamespace(id=2, user_id=1),
        ]

        result_mock = MagicMock()
        result_mock.all.return_value = records

        self.session.scalars.return_value = result_mock

        result = await self.repo.list_for_user(
            user_id=1,
            limit=5,
            offset=0,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, 3)
        self.assertEqual(result[1].id, 2)

    async def test_pagination_arguments(self) -> None:
        result_mock = MagicMock()
        result_mock.all.return_value = []

        self.session.scalars.return_value = result_mock

        await self.repo.list_for_user(
            user_id=1,
            limit=5,
            offset=10,
        )

        self.session.scalars.assert_awaited_once()

        stmt = self.session.scalars.await_args.args[0]

        compiled = stmt.compile()

        self.assertEqual(compiled.params["param_1"], 5)
        self.assertEqual(compiled.params["param_2"], 10)

    async def test_get_for_user_filters_by_owner(self) -> None:
        self.session.scalar.return_value = None
        table = AIConversation.__table__.name

        await self.repo.get_for_user(
            record_id=10,
            user_id=2,
        )

        stmt = self.session.scalar.await_args.args[0]

        compiled = stmt.compile()
        sql = str(compiled)

        self.assertIn(f"{table}.id =", sql)
        self.assertIn(f"{table}.user_id =", sql)
        self.assertIn(10, compiled.params.values())
        self.assertIn(2, compiled.params.values())


if __name__ == "__main__":
    unittest.main()