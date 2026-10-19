import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.database.models import Admin
from app.database.models.enums import AdminRole
from app.handlers.admin.attachments import get_item
from app.handlers.admin.dashboard import admin
from app.filters.admin import HasPermission
from app.database.repositories.cms import CMSRepository
from app.services.cms import AdminService
from app.services.permissions import Permission, PermissionService
from app.utils.presentation import html_text


class SecurityRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_admin_command_renders_main(self) -> None:
        message = SimpleNamespace(from_user=SimpleNamespace(id=123))
        settings = SimpleNamespace(ai_available=False)
        permissions = SimpleNamespace(role_for=AsyncMock(return_value=None))
        with (
            patch("app.handlers.admin.dashboard.PermissionService", return_value=permissions),
            patch("app.handlers.admin.dashboard.send_page", new=AsyncMock()) as send_page,
        ):
            await admin(message, object(), settings)
        send_page.assert_awaited_once_with(message, unittest.mock.ANY, "main", ai_available=False)

    async def test_unknown_permission_is_denied(self) -> None:
        service = PermissionService(object(), SimpleNamespace(super_admin_id=1))
        with patch.object(service, "role_for", new=AsyncMock(return_value=AdminRole.SUPER_ADMIN)):
            self.assertFalse(await service.has(1, "UNKNOWN"))  # type: ignore[arg-type]

    async def test_non_admin_callback_and_fsm_message_are_denied(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock(return_value=None))
        settings = SimpleNamespace(super_admin_id=999)
        permission_filter = HasPermission(Permission.CONTENT)
        callback = SimpleNamespace(from_user=SimpleNamespace(id=123))
        message = SimpleNamespace(from_user=SimpleNamespace(id=123))
        self.assertFalse(await permission_filter(callback, session, settings))
        self.assertFalse(await permission_filter(message, session, settings))

    async def test_editor_cannot_manage_admins(self) -> None:
        editor = SimpleNamespace(role=AdminRole.EDITOR)
        session = SimpleNamespace(scalar=AsyncMock(return_value=editor))
        service = PermissionService(session, SimpleNamespace(super_admin_id=999))
        self.assertFalse(await service.has(123, Permission.ADMINS))

    async def test_button_repository_scopes_lookup_to_parent_page(self) -> None:
        session = SimpleNamespace(scalar=AsyncMock(return_value=None))
        await CMSRepository(session).button(7, page_id=11)
        query = session.scalar.await_args.args[0]
        rendered = str(query)
        self.assertIn("bot_buttons.id", rendered)
        self.assertIn("bot_buttons.page_id", rendered)

    async def test_protected_and_last_super_admin_cannot_be_removed(self) -> None:
        protected = Admin(id=1, telegram_id=10, role=AdminRole.SUPER_ADMIN, is_active=True)
        with self.assertRaises(ValueError):
            await AdminService(SimpleNamespace(), 10)._ensure_removable(protected)
        last = Admin(id=2, telegram_id=20, role=AdminRole.SUPER_ADMIN, is_active=True)
        session = SimpleNamespace(scalar=AsyncMock(return_value=1))
        with self.assertRaises(ValueError):
            await AdminService(session, 10)._ensure_removable(last)

    async def test_attachment_lookup_is_scoped_to_page_revision(self) -> None:
        revision = SimpleNamespace(id=77)
        page = SimpleNamespace(draft_revision=revision)
        page_service = SimpleNamespace(ensure_editable_draft=AsyncMock(return_value=page))
        repository = SimpleNamespace(attachment=AsyncMock(return_value=None))
        with (
            patch("app.handlers.admin.attachments.PageService", return_value=page_service),
            patch("app.handlers.admin.attachments.PageRepository", return_value=repository),
        ):
            self.assertIsNone(await get_item(object(), 5, 9, 123))
        repository.attachment.assert_awaited_once_with(77, 5)

    def test_html_special_characters_are_escaped(self) -> None:
        self.assertEqual(html_text('<test> & "KUAM"'), "&lt;test&gt; &amp; &quot;KUAM&quot;")

    def test_admin_routers_have_server_side_permission_filters(self) -> None:
        modules = (
            "pages",
            "buttons",
            "attachments",
            "faq",
            "broadcasts",
            "admins",
            "statistics",
        )

        for name in modules:
            source = inspect.getsource(
                __import__(
                    f"app.handlers.admin.{name}",
                    fromlist=[name],
                )
            )

            self.assertIn(
                "router.callback_query.filter(HasPermission(",
                source,
                name,
            )

            self.assertIn(
                "router.message.filter(HasPermission(",
                source,
                name,
            )

    def test_ai_router_uses_granular_permissions(self) -> None:
        from app.handlers.admin import ai

        source = inspect.getsource(ai)

        required_permissions = (
            "Permission.AI_ANALYTICS_VIEW",
            "Permission.AI_DIAGNOSTICS",
            "Permission.AI_HISTORY_VIEW",
            "Permission.AI_INDEX_SYNC",
        )

        for permission in required_permissions:
            with self.subTest(permission=permission):
                self.assertIn(permission, source)

        self.assertNotIn(
            "HasPermission(Permission.STATISTICS)",
            source,
        )

    def test_broadcast_confirm_has_double_click_guard(self) -> None:
        source = inspect.getsource(__import__("app.handlers.admin.broadcasts", fromlist=["broadcasts"]))
        self.assertIn("_confirm_locks[cb.from_user.id]", source)
        self.assertIn('d.get("broadcast_starting")', source)

    def test_page_selection_does_not_put_page_key_in_callback_data(self) -> None:
        source = inspect.getsource(__import__("app.handlers.admin.buttons", fromlist=["buttons"]))
        self.assertNotIn("value=page.key", source)

    def test_ai_usage_model_has_no_content_fields(self) -> None:
        from app.database.models import AIUsage
        columns = set(AIUsage.__table__.columns.keys())
        self.assertTrue(columns.isdisjoint({"question", "answer", "prompt", "context", "api_key"}))


if __name__ == "__main__":
    unittest.main()
