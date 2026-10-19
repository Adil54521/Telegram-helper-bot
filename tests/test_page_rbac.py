import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.models.enums import (
    AdminRole,
    PageStatus,
    TelegramContentStatus,
    TelegramContentTarget,
    TelegramProcessingMode,
)
from app.filters.admin import HasPermission
from app.handlers.admin.pages import (
    delete_page,
    publish,
    router,
)
from app.services.permissions import (
    Permission,
    PermissionService,
)
from app.services.telegram_auto_drafts import (
    TelegramAutoDraftService,
    processing_mode,
)


class PageRBACTests(unittest.TestCase):

    def test_editor_can_manage_drafts(self):
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

    def test_editor_cannot_delete_published_content(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.CONTENT_PUBLISH,
            )
        )

    def assert_handler_permission(self, handler_func, permission):
        handler = next(
            item
            for item in router.callback_query.handlers
            if item.callback is handler_func
        )

        self.assertTrue(
            any(
                isinstance(item.callback, HasPermission)
                and item.callback.permission == permission
                for item in handler.filters
            )
        )

    def test_publish_handler_has_permission_filter(self):
        self.assert_handler_permission(
            publish,
            Permission.CONTENT_PUBLISH,
        )

    def test_delete_handler_has_permission_filter(self):
        self.assert_handler_permission(
            delete_page,
            Permission.CONTENT_PUBLISH,
        )

    def test_auto_publish_mode(self):
        mapping = SimpleNamespace(
            processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
            auto_draft_enabled=True,
        )

        self.assertEqual(
            processing_mode(mapping),
            TelegramProcessingMode.AUTO_PUBLISH,
        )

    def test_auto_draft_mode(self):
        mapping = SimpleNamespace(
            processing_mode=TelegramProcessingMode.AUTO_DRAFT,
            auto_draft_enabled=True,
        )

        self.assertEqual(
            processing_mode(mapping),
            TelegramProcessingMode.AUTO_DRAFT,
        )


class PagePermissionFilterTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.event = MagicMock()
        self.event.from_user.id = 123456

        self.session = MagicMock()
        self.settings = MagicMock()

    async def check_permission(self, role):
        permission_filter = HasPermission(
            Permission.CONTENT_PUBLISH
        )

        with patch(
            "app.filters.admin.PermissionService"
        ) as service_class:
            service = service_class.return_value
            service.role_for = AsyncMock(return_value=role)
            service.role_has.side_effect = (
                PermissionService.role_has
            )

            result = await permission_filter(
                self.event,
                self.session,
                self.settings,
            )

        return result

    async def test_editor_permission_denied(self):
        result = await self.check_permission(
            AdminRole.EDITOR
        )

        self.assertFalse(result)

    async def test_admin_permission_allowed(self):
        result = await self.check_permission(
            AdminRole.ADMIN
        )

        self.assertEqual(
            result,
            {"admin_role": AdminRole.ADMIN},
        )

    async def test_super_admin_permission_allowed(self):
        result = await self.check_permission(
            AdminRole.SUPER_ADMIN
        )

        self.assertEqual(
            result,
            {"admin_role": AdminRole.SUPER_ADMIN},
        )

    async def test_unknown_user_permission_denied(self):
        result = await self.check_permission(None)

        self.assertFalse(result)


class TelegramAutoPublishRBACTests(
    unittest.IsolatedAsyncioTestCase
):

    def setUp(self):
        self.session = MagicMock()
        self.session.add = MagicMock()
        self.session.flush = AsyncMock()

        self.actor = 123456
        self.chat_id = -1001234567890

        self.service = TelegramAutoDraftService(
            self.session,
            self.chat_id,
            self.actor,
        )

        self.post = SimpleNamespace(
            id=100,
            chat_id=self.chat_id,
            message_thread_id=10,
            status=TelegramContentStatus.NEW,
            created_faq_id=None,
            telegram_created_at=None,
            created_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            auto_process_after=None,
            auto_last_error=None,
        )

        self.parent = SimpleNamespace(
            id=5,
            is_active=True,
            status=PageStatus.PUBLISHED,
        )

        self.config = SimpleNamespace(
            auto_draft_enabled=True,
            auto_draft_enabled_at=(
                self.post.created_at
                - __import__("datetime").timedelta(minutes=5)
            ),
        )

        self.mapping = SimpleNamespace(
            id=1,
            is_active=True,
            processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
            auto_draft_enabled=True,
            processing_mode_enabled_at=None,
            content_target=TelegramContentTarget.PAGE,
            parent_page_id=5,
            auto_attach_materials=False,
        )

        self.service.repo.settings = AsyncMock(
            return_value=self.config
        )
        self.service.repo.mapping = AsyncMock(
            return_value=self.mapping
        )
        self.service.repo.topic_names = AsyncMock(
            return_value={10: "Новости"}
        )

        self.session.get = AsyncMock(
            return_value=self.parent
        )

    async def test_auto_publish_calls_page_service(self):
        parsed = SimpleNamespace(
            kind=__import__(
                "app.services.telegram_content_parser",
                fromlist=["TelegramContentParseKind"],
            ).TelegramContentParseKind.BILINGUAL,
            confidence=__import__(
                "app.services.telegram_content_parser",
                fromlist=["TelegramContentConfidence"],
            ).TelegramContentConfidence.HIGH,
            title_ru="Новости университета",
            text_ru="Текст новости",
            title_kk="Университет жаңалықтары",
            text_kk="Жаңалық мәтіні",
        )

        page = SimpleNamespace(
            id=42,
            key="news",
            draft_revision_id=99,
        )

        self.service.parser.parse = MagicMock(
            return_value=parsed
        )

        with (
            patch(
                "app.services.telegram_auto_drafts.source_text",
                return_value="Тестовая публикация",
            ),
            patch(
                "app.services.telegram_auto_drafts.TelegramContentRepository"
            ) as content_repo_class,
            patch(
                "app.services.telegram_auto_drafts.TelegramContentImportService"
            ) as import_class,
            patch(
                "app.services.telegram_auto_drafts.PageService"
            ) as page_service_class,
            patch(
                "app.services.telegram_auto_drafts.source_fingerprint",
                return_value="test-fingerprint",
            ),
        ):
            content_repo_class.return_value.media_count = (
                AsyncMock(return_value=0)
            )

            import_class.return_value.create_page = AsyncMock(
                return_value=page
            )

            page_service_class.return_value.publish = AsyncMock()

            result = await self.service.process_claimed(
                self.post
            )

            page_service_class.return_value.publish.assert_awaited_once_with(
                42,
                self.actor,
            )

        self.assertIsNotNone(result)
        self.assertTrue(result.published)
        self.assertEqual(result.page_id, 42)

    async def test_auto_draft_does_not_publish(self):
        self.mapping.processing_mode = (
            TelegramProcessingMode.AUTO_DRAFT
        )

        parsed = SimpleNamespace(
            kind=__import__(
                "app.services.telegram_content_parser",
                fromlist=["TelegramContentParseKind"],
            ).TelegramContentParseKind.BILINGUAL,
            confidence=__import__(
                "app.services.telegram_content_parser",
                fromlist=["TelegramContentConfidence"],
            ).TelegramContentConfidence.HIGH,
            title_ru="Новости",
            text_ru="Текст",
            title_kk="Жаңалықтар",
            text_kk="Мәтін",
        )

        self.service.parser.parse = MagicMock(
            return_value=parsed
        )

        page = SimpleNamespace(
            id=43,
            key="draft",
            draft_revision_id=100,
        )

        with (
            patch(
                "app.services.telegram_auto_drafts.source_text",
                return_value="Тест",
            ),
            patch(
                "app.services.telegram_auto_drafts.TelegramContentRepository"
            ) as content_repo_class,
            patch(
                "app.services.telegram_auto_drafts.TelegramContentImportService"
            ) as import_class,
            patch(
                "app.services.telegram_auto_drafts.PageService"
            ) as page_service_class,
            patch(
                "app.services.telegram_auto_drafts.source_fingerprint",
                return_value="test-fingerprint",
            ),
        ):
            content_repo_class.return_value.media_count = (
                AsyncMock(return_value=0)
            )

            import_class.return_value.create_page = AsyncMock(
                return_value=page
            )

            page_service_class.return_value.publish = AsyncMock()

            result = await self.service.process_claimed(
                self.post
            )

            page_service_class.return_value.publish.assert_not_awaited()

        self.assertIsNotNone(result)
        self.assertFalse(result.published)

    async def test_manual_mode_does_not_process(self):
        self.mapping.processing_mode = (
            TelegramProcessingMode.MANUAL
        )

        result = await self.service.process_claimed(
            self.post
        )

        self.assertIsNone(result)

    async def test_inactive_topic_does_not_process(self):
        self.mapping.is_active = False

        result = await self.service.process_claimed(
            self.post
        )

        self.assertIsNone(result)

    async def test_unpublished_parent_does_not_process(self):
        self.parent.status = PageStatus.DRAFT

        result = await self.service.process_claimed(
            self.post
        )

        self.assertIsNone(result)
        self.assertEqual(
            self.post.auto_last_error,
            "parent page is not active and published",
        )


if __name__ == "__main__":
    unittest.main()