
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.callbacks import AttachmentCallback
from app.database.models.enums import AdminRole, MediaType
from app.handlers.admin import attachments as module
from app.services.permissions import Permission, PermissionService


MODULE = "app.handlers.admin.attachments"


def make_attachment(
    attachment_id=42,
    revision_id=20,
):
    return SimpleNamespace(
        id=attachment_id,
        revision_id=revision_id,
        type=MediaType.DOCUMENT,
        telegram_file_id="file_123",
        file_name="document.pdf",
        caption_ru="Документ",
        caption_kk=None,
        position=0,
        is_active=True,
    )


def make_page(
    draft_revision_id=20,
    published_revision_id=10,
):
    return SimpleNamespace(
        id=5,
        draft_revision_id=draft_revision_id,
        published_revision_id=published_revision_id,
        draft_revision=SimpleNamespace(
            id=draft_revision_id,
            attachments=[],
        ),
    )


class AttachmentPermissionTests(unittest.TestCase):

    def test_editor_can_manage_content(self):
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


class AttachmentHandlerRBACTests(
    unittest.IsolatedAsyncioTestCase
):

    def setUp(self):
        self.callback = MagicMock()
        self.callback.from_user.id = 123456
        self.callback.answer = AsyncMock()
        self.callback.message = MagicMock()

        self.message = MagicMock()
        self.message.from_user.id = 123456
        self.message.text = "Новая подпись"
        self.message.answer = AsyncMock()

        self.session = MagicMock()
        self.settings = MagicMock()

        self.state = MagicMock()
        self.state.get_data = AsyncMock(
            return_value={
                "page_id": 5,
                "item_id": 42,
                "type": MediaType.DOCUMENT.value,
                "file_id": "file_123",
                "file_name": "document.pdf",
                "caption_ru": "Документ",
                "caption_kk": None,
                "language": "ru",
                "list_page": 0,
            }
        )
        self.state.clear = AsyncMock()

        self.page = make_page()
        self.attachment = make_attachment()

        self.page_service = MagicMock()
        self.page_service.ensure_editable_draft = AsyncMock(
            return_value=self.page
        )

        self.page_patcher = patch(
            f"{MODULE}.PageService",
            return_value=self.page_service,
        )
        self.page_patcher.start()
        self.addCleanup(self.page_patcher.stop)

        self.repo = MagicMock()
        self.repo.attachment = AsyncMock(
            return_value=self.attachment
        )
        self.repo.attachments = AsyncMock(
            return_value=[self.attachment]
        )

        self.repo_patcher = patch(
            f"{MODULE}.PageRepository",
            return_value=self.repo,
        )
        self.repo_patcher.start()
        self.addCleanup(self.repo_patcher.stop)

        self.service = MagicMock()
        self.service.create = AsyncMock(
            return_value=self.attachment
        )
        self.service.update = AsyncMock()
        self.service.move = AsyncMock()
        self.service.delete = AsyncMock()

        self.service_patcher = patch(
            f"{MODULE}.AttachmentService",
            return_value=self.service,
        )
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)

        self.guard_patcher = patch(
            f"{MODULE}.guard",
            new_callable=AsyncMock,
            return_value=True,
        )
        self.guard_patcher.start()
        self.addCleanup(self.guard_patcher.stop)

        self.ok_patcher = patch(
            f"{MODULE}.ok",
            new_callable=AsyncMock,
            return_value=True,
        )
        self.ok_patcher.start()
        self.addCleanup(self.ok_patcher.stop)

        self.list_patcher = patch(
            f"{MODULE}.listing",
            new_callable=AsyncMock,
        )
        self.list_mock = self.list_patcher.start()
        self.addCleanup(self.list_patcher.stop)

    async def test_get_item_uses_draft_revision(self):
        result = await module.get_item(
            self.session,
            42,
            5,
            123456,
        )

        self.assertIs(result, self.attachment)

        self.repo.attachment.assert_awaited_once_with(
            20,
            42,
        )

    async def test_old_published_attachment_not_found(self):
        self.repo.attachment.return_value = None

        result = await module.get_item(
            self.session,
            99,
            5,
            123456,
        )

        self.assertIsNone(result)

        self.repo.attachment.assert_awaited_once_with(
            20,
            99,
        )

    async def test_editor_creates_draft_attachment(self):
        with patch(
            f"{MODULE}.finish_wizard",
            new_callable=AsyncMock,
        ):
            await module.create(
                self.callback,
                self.session,
                self.settings,
                self.state,
            )

        self.service.create.assert_awaited_once_with(
            5,
            123456,
            MediaType.DOCUMENT,
            "file_123",
            "document.pdf",
            "Документ",
            None,
        )

    async def test_editor_updates_draft_caption(self):
        await module.caption_value(
            self.message,
            self.session,
            self.settings,
            self.state,
        )

        self.service.update.assert_awaited_once_with(
            self.attachment,
            123456,
            caption_ru="Новая подпись",
        )

    async def test_editor_cannot_update_old_attachment(self):
        self.repo.attachment.return_value = None

        await module.caption_value(
            self.message,
            self.session,
            self.settings,
            self.state,
        )

        self.service.update.assert_not_awaited()

    async def test_editor_deletes_draft_attachment(self):
        await module.mutate(
            self.callback,
            SimpleNamespace(
                action="delete_yes",
                item_id=42,
                page_id=5,
                page=0,
            ),
            self.session,
            self.settings,
        )

        self.service.delete.assert_awaited_once_with(
            self.attachment,
            123456,
        )

    async def test_editor_cannot_delete_old_attachment(self):
        self.repo.attachment.return_value = None

        await module.mutate(
            self.callback,
            SimpleNamespace(
                action="delete_yes",
                item_id=99,
                page_id=5,
                page=0,
            ),
            self.session,
            self.settings,
        )

        self.service.delete.assert_not_awaited()

    async def test_editor_moves_draft_attachment(self):
        with patch(
            f"{MODULE}.open_",
            new_callable=AsyncMock,
        ) as open_mock:

            await module.mutate(
                self.callback,
                SimpleNamespace(
                    action="up",
                    item_id=42,
                    page_id=5,
                    page=0,
                ),
                self.session,
                self.settings,
            )

        self.service.move.assert_awaited_once_with(
            self.attachment,
            123456,
            -1,
        )

        open_mock.assert_awaited_once()

    async def test_editor_cannot_move_old_attachment(self):
        self.repo.attachment.return_value = None

        await module.mutate(
            self.callback,
            SimpleNamespace(
                action="up",
                item_id=99,
                page_id=5,
                page=0,
            ),
            self.session,
            self.settings,
        )

        self.service.move.assert_not_awaited()

    async def test_editor_can_toggle_draft_attachment(self):
        with patch(
            f"{MODULE}.open_",
            new_callable=AsyncMock,
        ):
            await module.mutate(
                self.callback,
                SimpleNamespace(
                    action="toggle",
                    item_id=42,
                    page_id=5,
                    page=0,
                ),
                self.session,
                self.settings,
            )

        self.service.update.assert_awaited_once_with(
            self.attachment,
            123456,
            is_active=False,
        )


if __name__ == "__main__":
    unittest.main()
