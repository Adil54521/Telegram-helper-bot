
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.callbacks import ButtonCallback
from app.database.models.enums import AdminRole, ButtonAction
from app.handlers.admin import buttons as module
from app.services.permissions import Permission, PermissionService


MODULE = "app.handlers.admin.buttons"


def make_page(published=False):
    return SimpleNamespace(
        id=10,
        published_revision_id=5 if published else None,
    )


def make_button():
    return SimpleNamespace(
        id=42,
        page_id=10,
        text="Расписание",
        action_type=ButtonAction.URL,
        target="https://kuam.edu.kz",
        is_active=True,
        position=0,
        row=0,
        translations=[],
    )


def actions_from_markup(markup):
    return [
        ButtonCallback.unpack(button.callback_data).action
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
        and button.callback_data.startswith("btn:")
    ]


class ButtonPermissionMatrixTests(unittest.TestCase):

    def test_editor_can_view_and_manage(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.CONTENT_VIEW,
            )
        )
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.CONTENT_MANAGE,
            )
        )

    def test_editor_cannot_publish(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.CONTENT_PUBLISH,
            )
        )

    def test_admin_can_publish(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.ADMIN,
                Permission.CONTENT_PUBLISH,
            )
        )

    def test_super_admin_can_publish(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.SUPER_ADMIN,
                Permission.CONTENT_PUBLISH,
            )
        )


class ButtonRBACTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.callback = MagicMock()
        self.callback.from_user.id = 123456
        self.callback.answer = AsyncMock()
        self.callback.message = MagicMock()

        self.message = MagicMock()
        self.message.from_user.id = 123456
        self.message.text = "Новый текст"
        self.message.answer = AsyncMock()

        self.session = MagicMock()
        self.settings = MagicMock()

        self.state = MagicMock()
        self.state.get_data = AsyncMock(return_value={
            "page_id": 10,
            "item_id": 42,
            "text": "Расписание",
            "text_kk": None,
            "action": "URL",
            "target": "https://kuam.edu.kz",
            "mode": "text",
            "language": "ru",
        })
        self.state.clear = AsyncMock()

        self.permissions = MagicMock()
        self.permissions.has = AsyncMock(return_value=True)

        self.permission_patcher = patch(
            f"{MODULE}.PermissionService",
            return_value=self.permissions,
        )
        self.permission_patcher.start()
        self.addCleanup(self.permission_patcher.stop)

        self.page_repo = MagicMock()
        self.page_repo.by_id = AsyncMock(
            return_value=make_page()
        )

        self.page_patcher = patch(
            f"{MODULE}.PageRepository",
            return_value=self.page_repo,
        )
        self.page_patcher.start()
        self.addCleanup(self.page_patcher.stop)

        self.cms_repo = MagicMock()
        self.cms_repo.button = AsyncMock(
            return_value=make_button()
        )
        self.cms_repo.buttons = AsyncMock(
            return_value=[make_button()]
        )

        self.cms_patcher = patch(
            f"{MODULE}.CMSRepository",
            return_value=self.cms_repo,
        )
        self.cms_patcher.start()
        self.addCleanup(self.cms_patcher.stop)

        self.service = MagicMock()
        self.service.create = AsyncMock(
            return_value=make_button()
        )
        self.service.update = AsyncMock()
        self.service.update_translation = AsyncMock()
        self.service.delete = AsyncMock()
        self.service.move = AsyncMock()

        self.service_patcher = patch(
            f"{MODULE}.ButtonService",
            return_value=self.service,
        )
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)

        self.edit_patcher = patch(
            f"{MODULE}.safe_edit_text",
            new_callable=AsyncMock,
        )
        self.edit_mock = self.edit_patcher.start()
        self.addCleanup(self.edit_patcher.stop)

        self.guard_patcher = patch(
            f"{MODULE}.guard",
            new_callable=AsyncMock,
            return_value=True,
        )
        self.guard_patcher.start()
        self.addCleanup(self.guard_patcher.stop)

        self.allowed_patcher = patch(
            f"{MODULE}.allowed",
            new_callable=AsyncMock,
            return_value=True,
        )
        self.allowed_patcher.start()
        self.addCleanup(self.allowed_patcher.stop)

    async def test_editor_can_modify_draft(self):
        self.page_repo.by_id.return_value = make_page(False)

        result = await module.can_modify_buttons(
            123456,
            10,
            self.session,
            self.settings,
        )

        self.assertTrue(result)

    async def test_editor_cannot_modify_published(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        result = await module.can_modify_buttons(
            123456,
            10,
            self.session,
            self.settings,
        )

        self.assertFalse(result)

    async def test_admin_can_modify_published(self):
        self.page_repo.by_id.return_value = make_page(True)

        result = await module.can_modify_buttons(
            123456,
            10,
            self.session,
            self.settings,
        )

        self.assertTrue(result)

    async def test_missing_page_denied(self):
        self.page_repo.by_id.return_value = None

        result = await module.can_modify_buttons(
            123456,
            10,
            self.session,
            self.settings,
        )

        self.assertFalse(result)

    async def test_editor_cannot_create_on_published(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.new_save(
            self.callback,
            self.session,
            self.settings,
            self.state,
        )

        self.service.create.assert_not_awaited()

    async def test_editor_can_create_on_draft(self):
        with (
            patch(
                f"{MODULE}.finish_wizard",
                new_callable=AsyncMock,
            ),
            patch(
                f"{MODULE}.show_list",
                new_callable=AsyncMock,
            ),
        ):
            await module.new_save(
                self.callback,
                self.session,
                self.settings,
                self.state,
            )

        self.service.create.assert_awaited_once()

    async def test_editor_cannot_delete_published(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.mutate(
            self.callback,
            SimpleNamespace(
                action="delete_yes",
                item_id=42,
                page_id=10,
                page=0,
            ),
            self.session,
            self.settings,
        )

        self.service.delete.assert_not_awaited()

    async def test_admin_can_delete_published(self):
        with patch(
            f"{MODULE}.show_list",
            new_callable=AsyncMock,
        ):
            await module.mutate(
                self.callback,
                SimpleNamespace(
                    action="delete_yes",
                    item_id=42,
                    page_id=10,
                    page=0,
                ),
                self.session,
                self.settings,
            )

        self.service.delete.assert_awaited_once()

    async def test_editor_cannot_toggle_published(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.mutate(
            self.callback,
            SimpleNamespace(
                action="toggle",
                item_id=42,
                page_id=10,
                page=0,
            ),
            self.session,
            self.settings,
        )

        self.service.update.assert_not_awaited()

    async def test_editor_cannot_save_fsm_after_publish(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.save_edit(
            self.message,
            self.session,
            self.settings,
            self.state,
        )

        self.service.update.assert_not_awaited()
        self.service.update_translation.assert_not_awaited()
        self.state.clear.assert_awaited_once()

    async def test_editor_does_not_see_add_button(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.show_list(
            self.callback,
            10,
            self.session,
            self.settings,
        )

        markup = self.edit_mock.await_args.kwargs[
            "reply_markup"
        ]

        texts = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]

        self.assertNotIn("➕ Добавить кнопку", texts)

    async def test_editor_sees_only_back_in_published_card(self):
        self.page_repo.by_id.return_value = make_page(True)

        async def has_permission(user_id, permission):
            return permission != Permission.CONTENT_PUBLISH

        self.permissions.has.side_effect = has_permission

        await module.open_button(
            self.callback,
            SimpleNamespace(
                action="open",
                item_id=42,
                page_id=10,
                page=0,
            ),
            self.session,
            self.settings,
        )

        markup = self.edit_mock.await_args.kwargs[
            "reply_markup"
        ]

        texts = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]

        self.assertEqual(texts, ["⬅️ Назад"])


if __name__ == "__main__":
    unittest.main()
