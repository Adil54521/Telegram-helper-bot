import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.handlers.user.ai import (
    back_to_ai,
    my_questions,
    open_my_question,
)
from app.states.user import AskAI

MODULE = "app.handlers.user.ai"


class UserAIHistoryTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.user = SimpleNamespace(
            id=10,
            content_language_code="ru",
        )

        self.callback = MagicMock()
        self.callback.from_user.id = 123456
        self.callback.message = MagicMock()
        self.callback.answer = AsyncMock()

        self.session = MagicMock()

        self.state = MagicMock()
        self.state.get_data = AsyncMock(return_value={})
        self.state.update_data = AsyncMock()
        self.state.get_state = AsyncMock(
            return_value=AskAI.question.state
        )
        self.state.set_state = AsyncMock()

        self.callback.message.message_id = 100
        self.callback.message.chat.id = 123456
        self.callback.message.answer = AsyncMock(
            return_value=SimpleNamespace(
                message_id=200,
                chat=SimpleNamespace(id=123456),
            )
        )

        self.callback.bot.edit_message_text = AsyncMock()

        self.repo = MagicMock()
        self.repo.count_for_user = AsyncMock(return_value=0)
        self.repo.list_for_user = AsyncMock(return_value=[])
        self.repo.get_for_user = AsyncMock(return_value=None)

        self.edit_mock = AsyncMock()

        self.patchers = [
            patch(
                f"{MODULE}.upsert_user",
                new_callable=AsyncMock,
                return_value=self.user,
            ),
            patch(
                f"{MODULE}.AIHistoryRepository",
                return_value=self.repo,
            ),
            patch(
                f"{MODULE}.safe_edit_text",
                new=self.edit_mock,
            ),
        ]

        for patcher in self.patchers:
            patcher.start()

        self.addCleanup(self.stop_patches)

    def stop_patches(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()

    async def test_cannot_open_foreign_question(self) -> None:
        """Пользователь не должен получать чужие вопросы."""

        self.repo.get_for_user.return_value = None

        callback_data = SimpleNamespace(
            action="open",
            page=0,
            item_id=999,
        )

        await open_my_question(
            callback=self.callback,
            callback_data=callback_data,
            session=self.session,
        )

        self.repo.get_for_user.assert_awaited_once_with(
            record_id=999,
            user_id=10,
        )

        self.edit_mock.assert_not_awaited()

        self.callback.answer.assert_awaited_once_with(
            "Вопрос не найден.",
            show_alert=True,
        )

    async def test_open_own_question(self) -> None:
        """Свой вопрос должен открываться."""

        self.repo.get_for_user.return_value = SimpleNamespace(
            id=15,
            user_id=10,
            question="Как получить справку?",
            answer="Обратитесь в деканат.",
            status="COMPLETED",
        )

        callback_data = SimpleNamespace(
            action="open",
            page=0,
            item_id=15,
        )

        await open_my_question(
            callback=self.callback,
            callback_data=callback_data,
            session=self.session,
        )

        self.edit_mock.assert_awaited_once()

        text = self.edit_mock.await_args.args[1]

        self.assertIn("Как получить справку?", text)
        self.assertIn("Обратитесь в деканат.", text)

        self.callback.answer.assert_awaited_once()

    async def test_back_preserves_page(self) -> None:
        """Возврат из карточки сохраняет страницу."""

        self.repo.get_for_user.return_value = SimpleNamespace(
            id=25,
            user_id=10,
            question="Тестовый вопрос",
            answer="Тестовый ответ",
            status="COMPLETED",
        )

        callback_data = SimpleNamespace(
            action="open",
            page=3,
            item_id=25,
        )

        await open_my_question(
            callback=self.callback,
            callback_data=callback_data,
            session=self.session,
        )

        keyboard = self.edit_mock.await_args.kwargs["reply_markup"]

        back_button = keyboard.inline_keyboard[0][0]

        self.assertIn("list", back_button.callback_data)
        self.assertIn(":3:", back_button.callback_data)

    async def test_empty_history(self) -> None:
        callback_data = SimpleNamespace(
            action="list",
            page=0,
            item_id=0,
        )

        await my_questions(
            callback=self.callback,
            callback_data=callback_data,
            session=self.session,
            state=self.state,
        )

        self.repo.count_for_user.assert_awaited_once_with(10)

        self.repo.list_for_user.assert_awaited_once_with(
            user_id=10,
            limit=5,
            offset=0,
        )

        self.callback.message.answer.assert_awaited_once()

        text = self.callback.message.answer.await_args.args[0]

        self.assertIn("История", text)
        self.assertIn("0", text)

        self.edit_mock.assert_not_awaited()

        self.state.update_data.assert_awaited_once_with(
            ai_history_message_id=200,
            ai_history_chat_id=123456,
        )

    async def test_pagination_second_page(self) -> None:
        self.repo.count_for_user.return_value = 13

        self.repo.list_for_user.return_value = [
            SimpleNamespace(
                id=6,
                question="Вопрос 6",
            )
        ]

        callback_data = SimpleNamespace(
            action="list",
            page=1,
            item_id=0,
        )

        await my_questions(
            callback=self.callback,
            callback_data=callback_data,
            session=self.session,
            state=self.state,
        )

        self.repo.list_for_user.assert_awaited_once_with(
            user_id=10,
            limit=5,
            offset=5,
        )

        keyboard = (
            self.callback.message.answer
            .await_args.kwargs["reply_markup"]
        )

        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]

        self.assertTrue(
            any("open" in value for value in callbacks)
        )

    async def test_back_to_ai_closes_history(self) -> None:
        self.state.get_data.return_value = {
            "ai_history_message_id": 100,
            "ai_history_chat_id": 123456,
        }

        with patch(
            f"{MODULE}.safe_delete_message",
            new_callable=AsyncMock,
        ) as delete:
            await back_to_ai(
                callback=self.callback,
                session=self.session,
                state=self.state,
            )

        delete.assert_awaited_once_with(
            self.callback.message,
        )

        self.state.update_data.assert_awaited_once_with(
            ai_history_message_id=None,
            ai_history_chat_id=None,
        )

        self.state.set_state.assert_not_awaited()
        self.edit_mock.assert_not_awaited()
        self.callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()