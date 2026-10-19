import unittest

from app.database.models.enums import AdminRole
from app.services.permissions import (
    Permission,
    PermissionService,
)


class RBACTests(unittest.TestCase):

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

    def test_editor_cannot_publish_faq(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.FAQ_PUBLISH,
            )
        )

    def test_admin_can_publish(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.ADMIN,
                Permission.CONTENT_PUBLISH,
            )
        )

    def test_admin_cannot_sync_ai_index(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.ADMIN,
                Permission.AI_INDEX_SYNC,
            )
        )

    def test_super_admin_has_every_permission(self):
        for permission in Permission:
            with self.subTest(permission=permission):
                self.assertTrue(
                    PermissionService.role_has(
                        AdminRole.SUPER_ADMIN,
                        permission,
                    )
                )

    def test_unknown_role_denied(self):
        self.assertFalse(
            PermissionService.role_has(
                None,
                Permission.CONTENT_MANAGE,
            )
        )

    def test_invalid_permission_denied(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.SUPER_ADMIN,
                "CONTENT_PUBLISH",
            )
        )


if __name__ == "__main__":
    unittest.main()