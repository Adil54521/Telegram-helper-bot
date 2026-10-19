import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.provider import AIProviderError
from app.ai.service import AIRateLimitError, MAX_QUESTION_LENGTH
from app.handlers.user.ai import answer_ai


MODULE = "app.handlers.user.ai"


class UserAITests(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.user = SimpleNamespace(
            id=10,
            content_language_code="ru",
        )

        self.message = MagicMock()
        self.message.from_user.id = 123456
        self.message.text = "Как получить справку?"
        self.message.chat.id = 123456
        self.message.answer = AsyncMock()
        self.message.bot = MagicMock()

        self.session = MagicMock()
        self.session.begin_nested.return_value.__aenter__ = AsyncMock()
        self.session.begin_nested.return_value.__aexit__ = AsyncMock()

        self.settings = SimpleNamespace(ai_available=True)
        self.state = MagicMock()
        self.state.get_data = AsyncMock(return_value={})
        self.state.update_data = AsyncMock()
        self.ai_service = MagicMock()
        self.ai_service.answer = AsyncMock()

        self.record = SimpleNamespace(
            id=1,
            status="PENDING",
            answer=None,
        )

        self.history = MagicMock()
        self.history.create = AsyncMock(return_value=self.record)
        self.history.complete = AsyncMock()
        self.history.fail = AsyncMock()

        self.patches = [
            patch(
                f"{MODULE}.upsert_user",
                new_callable=AsyncMock,
                return_value=self.user,
            ),
            patch(
                f"{MODULE}.AIHistoryRepository",
                return_value=self.history,
            ),
            patch(f"{MODULE}.ChatActionSender"),
        ]

        for item in self.patches:
            item.start()

        self.addCleanup(self.stop_patches)

    def stop_patches(self) -> None:
        for item in reversed(self.patches):
            item.stop()

    async def run_handler(self) -> None:
        await answer_ai(
            message=self.message,
            session=self.session,
            settings=self.settings,
            state=self.state,
            ai_service=self.ai_service,
        )

    async def test_successful_answer(self) -> None:
        self.ai_service.answer.return_value = SimpleNamespace(
            text="Справку можно получить в университете.",
            result_count=1,
        )

        await self.run_handler()

        self.history.create.assert_awaited_once_with(
            user_id=10,
            question="Как получить справку?",
            language="ru",
        )

        self.history.complete.assert_awaited_once_with(
            self.record,
            "Справку можно получить в университете.",
        )

        self.history.fail.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_no_information(self) -> None:
        self.ai_service.answer.return_value = None

        await self.run_handler()

        self.history.complete.assert_awaited_once_with(
            self.record,
            None,
        )

        self.history.fail.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_provider_failure(self) -> None:
        # TimeoutError обрабатывается тем же блоком,
        # что и AIProviderError.
        self.ai_service.answer.side_effect = TimeoutError()

        await self.run_handler()

        self.history.fail.assert_awaited_once_with(
            self.record,
        )

        self.history.complete.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_rate_limit(self) -> None:
        # Используем исключение с тем же типом,
        # что выбрасывает AIService.
        self.ai_service.answer.side_effect = AIRateLimitError()

        await self.run_handler()

        self.history.fail.assert_awaited_once_with(
            self.record,
        )

        self.assertEqual(
            self.record.status,
            "RATE_LIMITED",
        )

        self.message.answer.assert_awaited_once()

    async def test_history_create_failure(self) -> None:
        self.history.create.side_effect = RuntimeError(
            "History database error"
        )

        self.ai_service.answer.return_value = SimpleNamespace(
            text="Ответ Gemini",
            result_count=1,
        )

        await self.run_handler()

        self.ai_service.answer.assert_awaited_once()
        self.history.complete.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_question_too_long(self) -> None:
        self.message.text = "А" * (MAX_QUESTION_LENGTH + 1)

        await self.run_handler()

        self.ai_service.answer.assert_not_awaited()
        self.history.create.assert_not_awaited()
        self.message.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()