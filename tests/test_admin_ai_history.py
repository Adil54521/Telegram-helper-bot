import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.filters.admin import HasPermission
from app.handlers.admin.ai_history import (
    export_ai_history,
    show_ai_history,
    show_ai_history_detail,
)
from app.services.permissions import Permission

MODULE = "app.handlers.admin.ai_history"


class AdminAIHistoryTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.callback = MagicMock()
        self.callback.from_user.id = 123456
        self.callback.message = MagicMock()
        self.callback.message.chat.type = "private"
        self.callback.message.chat.id = 123456
        self.callback.message.answer_document = AsyncMock()
        self.callback.answer = AsyncMock()

        self.session = MagicMock()
        self.session.get = AsyncMock(return_value=None)

        self.settings = MagicMock()

        self.repo = MagicMock()
        self.repo.count = AsyncMock(return_value=0)
        self.repo.list_recent = AsyncMock(return_value=[])
        self.repo.get_by_id = AsyncMock(return_value=None)

        self.edit_mock = AsyncMock()

        self.permissions = MagicMock()
        self.permissions.has = AsyncMock(return_value=True)

        self.patchers = [
            patch(
                f"{MODULE}.AIHistoryRepository",
                return_value=self.repo,
            ),
            patch(
                f"{MODULE}.safe_edit_text",
                new=self.edit_mock,
            ),
            patch(
                f"{MODULE}.PermissionService",
                return_value=self.permissions,
            ),
        ]

        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.csv_patcher = patch(
            f"{MODULE}.generate_ai_history_csv",
            new_callable=AsyncMock,
            return_value=b"ID,Question\n1,Test\n",
        )

        self.csv_mock = self.csv_patcher.start()
        self.addCleanup(self.csv_patcher.stop)

    def stop_patches(self) -> None:
        for item in reversed(self.patchers):
            item.stop()

    async def test_history_default(self) -> None:
        callback_data = SimpleNamespace(action="history")

        await show_ai_history(
            self.callback,
            callback_data,
            self.session,
            self.settings
        )

        self.repo.count.assert_awaited_once_with(status=None)
        self.repo.list_recent.assert_awaited_once_with(
            limit=8,
            offset=0,
            status=None,
        )

        self.edit_mock.assert_awaited_once()

    async def test_filter_completed(self) -> None:
        callback_data = SimpleNamespace(action="h_ok_0")

        await show_ai_history(
            self.callback,
            callback_data,
            self.session,
            self.settings,
        )

        self.repo.count.assert_awaited_once_with(
            status="COMPLETED",
        )

        self.repo.list_recent.assert_awaited_once_with(
            limit=8,
            offset=0,
            status="COMPLETED",
        )

    async def test_filter_no_info(self) -> None:
        callback_data = SimpleNamespace(action="h_none_0")

        await show_ai_history(
            self.callback,
            callback_data,
            self.session,
            self.settings,
        )

        self.repo.count.assert_awaited_once_with(
            status="NO_INFO",
        )

    async def test_pagination(self) -> None:
        self.repo.count.return_value = 25

        callback_data = SimpleNamespace(action="h_fail_2")

        await show_ai_history(
            self.callback,
            callback_data,
            self.session,
            self.settings,
        )

        self.repo.list_recent.assert_awaited_once_with(
            limit=8,
            offset=16,
            status="FAILED",
        )

    async def test_permission_denied(self) -> None:
        permission_filter = HasPermission(
            Permission.AI_HISTORY_VIEW
        )

        with patch(
            "app.filters.admin.PermissionService"
        ) as service_mock:
            service = service_mock.return_value

            service.role_for = AsyncMock(
                return_value=None
            )

            result = await permission_filter(
                self.callback,
                self.session,
                self.settings,
            )

        self.assertFalse(result)

        service.role_for.assert_awaited_once_with(
            123456
        )

        self.repo.count.assert_not_awaited()
        self.repo.list_recent.assert_not_awaited()
        self.edit_mock.assert_not_awaited()

    async def test_detail_preserves_filter_and_page(self) -> None:
        self.repo.get_by_id.return_value = SimpleNamespace(
            id=42,
            user_id=10,
            language_code="ru",
            status="COMPLETED",
            created_at=__import__("datetime").datetime(
                2026, 10, 14, 10, 30,
            ),
            question="Как получить справку?",
            answer="Обратитесь в деканат.",
        )

        callback_data = SimpleNamespace(
            action="hist_none_3_42",
        )

        await show_ai_history_detail(
            self.callback,
            callback_data,
            self.session
        )

        self.repo.get_by_id.assert_awaited_once_with(42)

        keyboard = self.edit_mock.await_args.kwargs[
            "reply_markup"
        ]

        back_callback = keyboard.inline_keyboard[0][0].callback_data

        self.assertIn("h_none_3", back_callback)

    async def test_csv_export(self) -> None:
        self.callback.message.chat.type = "private"

        callback_data = SimpleNamespace(action="csv_ok")

        await export_ai_history(
            self.callback,
            callback_data,
            self.session,
            self.settings,
        )

        self.csv_mock.assert_awaited_once_with(
            self.session,
            status="COMPLETED",
        )

        self.callback.message.answer_document.assert_awaited_once()

        document = (
            self.callback.message.answer_document
            .await_args.args[0]
        )

        self.assertEqual(
            document.data,
            b"ID,Question\n1,Test\n",
        )

        self.assertIn("ai_history_ok_", document.filename)


if __name__ == "__main__":
    unittest.main()