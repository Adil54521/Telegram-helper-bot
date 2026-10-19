
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from app.handlers.user import ai as module
from app.states.user import AskAI


class UserAIUXTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = SimpleNamespace(
            edit_message_reply_markup=AsyncMock(),
        )

        self.state = SimpleNamespace(
            get_data=AsyncMock(return_value={}),
            update_data=AsyncMock(),
            set_data=AsyncMock(),
            set_state=AsyncMock(),
            get_state=AsyncMock(
                return_value=AskAI.question.state
            ),
        )

        self.message = SimpleNamespace(
            message_id=10,
            chat=SimpleNamespace(id=100),
            bot=self.bot,
            answer=AsyncMock(),
        )

    async def test_response_has_keyboard(self):
        sent = SimpleNamespace(
            message_id=11,
            chat=SimpleNamespace(id=100),
        )

        self.message.answer.return_value = sent

        result = await module.send_ai_response(
            self.message,
            self.state,
            "Ответ Gemini",
            "ru",
        )

        self.assertIs(result, sent)

        self.message.answer.assert_awaited_once()

        self.assertIsNotNone(
            self.message.answer.await_args.kwargs["reply_markup"]
        )

        self.state.update_data.assert_awaited_once_with(
            ai_control_message_id=11,
            ai_control_chat_id=100,
        )

    async def test_previous_keyboard_is_removed(self):
        self.state.get_data.return_value = {
            "ai_control_message_id": 10,
            "ai_control_chat_id": 100,
        }

        self.message.answer.return_value = SimpleNamespace(
            message_id=11,
            chat=SimpleNamespace(id=100),
        )

        await module.send_ai_response(
            self.message,
            self.state,
            "Новый ответ",
            "ru",
        )

        self.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=100,
            message_id=10,
            reply_markup=None,
        )

    async def test_other_chat_keyboard_is_not_removed(self):
        self.state.get_data.return_value = {
            "ai_control_message_id": 10,
            "ai_control_chat_id": 999,
        }

        self.message.answer.return_value = SimpleNamespace(
            message_id=11,
            chat=SimpleNamespace(id=100),
        )

        await module.send_ai_response(
            self.message,
            self.state,
            "Ответ",
            "ru",
        )

        self.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_keyboard_removal_error_does_not_break_response(self):
        self.state.get_data.return_value = {
            "ai_control_message_id": 10,
            "ai_control_chat_id": 100,
        }

        self.message.answer.return_value = SimpleNamespace(
            message_id=11,
            chat=SimpleNamespace(id=100),
        )

        self.bot.edit_message_reply_markup.side_effect = (
            TelegramBadRequest(
                method=MagicMock(),
                message="message can't be edited",
            )
        )

        result = await module.send_ai_response(
            self.message,
            self.state,
            "Ответ",
            "ru",
        )

        self.assertEqual(result.message_id, 11)

    async def test_history_opens_separate_message(self):
        callback = SimpleNamespace(
            message=self.message,
            bot=self.bot,
        )

        sent = SimpleNamespace(
            message_id=20,
            chat=SimpleNamespace(id=100),
        )

        self.message.answer.return_value = sent

        markup = module.ai_dialog_keyboard("ru")

        await module.show_ai_history(
            callback,
            self.state,
            "История вопросов",
            markup,
        )

        self.message.answer.assert_awaited_once_with(
            "История вопросов",
            reply_markup=markup,
        )

        self.state.update_data.assert_awaited_once_with(
            ai_history_message_id=20,
            ai_history_chat_id=100,
        )

    async def test_history_navigation_edits_existing_message(self):
        self.state.get_data.return_value = {
            "ai_history_message_id": 10,
            "ai_history_chat_id": 100,
        }

        callback = SimpleNamespace(
            message=self.message,
            bot=self.bot,
        )

        markup = module.ai_dialog_keyboard("ru")

        with patch.object(
            module,
            "safe_edit_text",
            new_callable=AsyncMock,
        ) as edit:
            await module.show_ai_history(
                callback,
                self.state,
                "Страница 2",
                markup,
            )

        edit.assert_awaited_once_with(
            self.message,
            "Страница 2",
            reply_markup=markup,
        )

        self.message.answer.assert_not_awaited()

    async def test_history_reuses_previous_message(self):
        self.state.get_data.return_value = {
            "ai_history_message_id": 20,
            "ai_history_chat_id": 100,
        }

        self.bot.edit_message_text = AsyncMock()

        callback = SimpleNamespace(
            message=self.message,
            bot=self.bot,
        )

        markup = module.ai_dialog_keyboard("ru")

        await module.show_ai_history(
            callback,
            self.state,
            "История",
            markup,
        )

        self.bot.edit_message_text.assert_awaited_once_with(
            "История",
            chat_id=100,
            message_id=20,
            reply_markup=markup,
        )

        self.message.answer.assert_not_awaited()

    async def test_leave_ai_clears_control_ids(self):
        self.state.get_data.return_value = {
            "ai_control_message_id": 10,
            "ai_control_chat_id": 100,
            "ai_history_message_id": 20,
            "ai_history_chat_id": 100,
            "last_question": "Вопрос",
            "current_page": "main",
        }

        await module.leave_ai_state(self.state)

        self.state.set_data.assert_awaited_once_with({
            "current_page": "main",
        })

        self.state.set_state.assert_awaited_once_with(None)

    async def test_back_from_history_deletes_history_only(self):
        self.state.get_data.return_value = {
            "ai_history_message_id": 10,
            "ai_history_chat_id": 100,
        }

        callback = SimpleNamespace(
            message=self.message,
            answer=AsyncMock(),
        )

        with patch.object(
            module,
            "safe_delete_message",
            new_callable=AsyncMock,
        ) as delete:
            await module.back_to_ai(
                callback,
                session=MagicMock(),
                state=self.state,
            )

        delete.assert_awaited_once_with(self.message)

        self.state.update_data.assert_awaited_once_with(
            ai_history_message_id=None,
            ai_history_chat_id=None,
        )

        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
