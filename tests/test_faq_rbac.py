
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.models.enums import (
    AdminRole,
    LanguageCode,
    TelegramContentStatus,
    TelegramContentTarget,
    TelegramProcessingMode,
)
from app.handlers.admin import faq as faq_module
from app.services.permissions import Permission, PermissionService
from app.services.telegram_auto_drafts import TelegramAutoDraftService


MODULE = "app.handlers.admin.faq"
AUTO_MODULE = "app.services.telegram_auto_drafts"


def make_faq(active=True):
    return SimpleNamespace(
        id=42,
        question="Как поступить?",
        answer="Обратитесь в приёмную комиссию.",
        position=0,
        is_active=active,
        translations=[
            SimpleNamespace(
                language_code=LanguageCode.RU,
                question="Как поступить?",
                answer="Обратитесь в приёмную комиссию.",
            )
        ],
    )


class FAQRoleMatrixTests(unittest.TestCase):

    def test_editor_can_manage_faq(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.FAQ_MANAGE,
            )
        )

    def test_editor_cannot_publish_faq(self):
        self.assertFalse(
            PermissionService.role_has(
                AdminRole.EDITOR,
                Permission.FAQ_PUBLISH,
            )
        )

    def test_admin_can_publish_faq(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.ADMIN,
                Permission.FAQ_PUBLISH,
            )
        )

    def test_super_admin_can_publish_faq(self):
        self.assertTrue(
            PermissionService.role_has(
                AdminRole.SUPER_ADMIN,
                Permission.FAQ_PUBLISH,
            )
        )

    def test_editor_card_hides_publish_and_delete(self):
        _, keyboard = faq_module.faq_card(
            make_faq(),
            can_publish=False,
        )

        actions = [
            faq_module.FAQCallback.unpack(button.callback_data).action
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]

        self.assertNotIn("toggle", actions)
        self.assertNotIn("delete_ask", actions)
        self.assertIn("edit_q_ru", actions)

    def test_admin_card_shows_publish_and_delete(self):
        _, keyboard = faq_module.faq_card(
            make_faq(),
            can_publish=True,
        )

        actions = [
            faq_module.FAQCallback.unpack(button.callback_data).action
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]

        self.assertIn("toggle", actions)
        self.assertIn("delete_ask", actions)


class FAQHandlerRBACTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.callback = MagicMock()
        self.callback.from_user.id = 123456
        self.callback.answer = AsyncMock()
        self.callback.message = MagicMock()

        self.session = MagicMock()
        self.session.flush = AsyncMock()

        self.settings = MagicMock()
        self.ai_service = None

        self.state = MagicMock()
        self.state.get_data = AsyncMock(return_value={
            "faq_question": "Как поступить?",
            "faq_answer": "Ответ",
            "faq_question_kk": None,
            "faq_answer_kk": None,
        })
        self.state.set_state = AsyncMock()
        self.state.update_data = AsyncMock()

        self.permissions = MagicMock()
        self.permissions.has = AsyncMock(return_value=False)

        self.permission_patcher = patch(
            f"{MODULE}.PermissionService",
            return_value=self.permissions,
        )
        self.permission_patcher.start()
        self.addCleanup(self.permission_patcher.stop)

        self.guard_patcher = patch(
            f"{MODULE}.guard",
            new_callable=AsyncMock,
            return_value=True,
        )
        self.guard_mock = self.guard_patcher.start()
        self.addCleanup(self.guard_patcher.stop)

    async def test_editor_creates_inactive_faq(self):
        with (
            patch(f"{MODULE}.FAQService") as service_class,
            patch(f"{MODULE}.sync_knowledge", new_callable=AsyncMock),
            patch(f"{MODULE}.finish_wizard", new_callable=AsyncMock),
            patch(f"{MODULE}.listing", new_callable=AsyncMock),
        ):
            service_class.return_value.create = AsyncMock()

            await faq_module.create(
                self.callback,
                self.session,
                self.settings,
                self.state,
                self.ai_service,
            )

            self.permissions.has.assert_awaited_with(
                123456,
                Permission.FAQ_PUBLISH,
            )

            self.assertFalse(
                service_class.return_value.create.await_args.kwargs[
                    "is_active"
                ]
            )

    async def test_admin_creates_active_faq(self):
        self.permissions.has.return_value = True

        with (
            patch(f"{MODULE}.FAQService") as service_class,
            patch(f"{MODULE}.sync_knowledge", new_callable=AsyncMock),
            patch(f"{MODULE}.finish_wizard", new_callable=AsyncMock),
            patch(f"{MODULE}.listing", new_callable=AsyncMock),
        ):
            service_class.return_value.create = AsyncMock()

            await faq_module.create(
                self.callback,
                self.session,
                self.settings,
                self.state,
                self.ai_service,
            )

            self.assertTrue(
                service_class.return_value.create.await_args.kwargs[
                    "is_active"
                ]
            )

    async def test_editor_cannot_toggle_faq(self):
        callback_data = SimpleNamespace(
            action="toggle",
            item_id=42,
            page=0,
        )

        with (
            patch(f"{MODULE}.CMSRepository") as repo_class,
            patch(f"{MODULE}.FAQService") as service_class,
        ):
            await faq_module.mutate(
                self.callback,
                callback_data,
                self.session,
                self.settings,
                self.ai_service,
            )

            repo_class.assert_not_called()
            service_class.assert_not_called()

        self.callback.answer.assert_awaited_once_with(
            "Недостаточно прав для этого действия",
            show_alert=True,
        )

    async def test_editor_cannot_delete_faq(self):
        for action in ("delete_ask", "delete_yes"):
            with self.subTest(action=action):
                self.callback.answer.reset_mock()

                callback_data = SimpleNamespace(
                    action=action,
                    item_id=42,
                    page=0,
                )

                with patch(f"{MODULE}.FAQService") as service_class:
                    await faq_module.mutate(
                        self.callback,
                        callback_data,
                        self.session,
                        self.settings,
                        self.ai_service,
                    )

                    service_class.assert_not_called()

                self.callback.answer.assert_awaited_once_with(
                    "Недостаточно прав для этого действия",
                    show_alert=True,
                )

    async def test_admin_can_toggle_faq(self):
        self.permissions.has.return_value = True

        item = make_faq(active=False)
        repo = MagicMock()
        repo.faq = AsyncMock(return_value=item)

        with (
            patch(f"{MODULE}.CMSRepository", return_value=repo),
            patch(f"{MODULE}.FAQService") as service_class,
            patch(f"{MODULE}.sync_knowledge", new_callable=AsyncMock),
            patch(f"{MODULE}.open_", new_callable=AsyncMock),
        ):
            service_class.return_value.update = AsyncMock()

            await faq_module.mutate(
                self.callback,
                SimpleNamespace(action="toggle", item_id=42, page=0),
                self.session,
                self.settings,
                self.ai_service,
            )

            service_class.return_value.update.assert_awaited_once_with(
                123456,
                item,
                is_active=True,
            )

    async def test_editor_csv_import_forces_inactive(self):
        self.state.get_data.return_value = {
            "import_rows": [{
                "row_number": 2,
                "question_ru": "Новый вопрос",
                "answer_ru": "Новый ответ",
                "question_kk": "",
                "answer_kk": "",
                "is_active": True,
            }],
            "import_duplicates": [],
        }

        repo = MagicMock()
        repo.faq_questions = AsyncMock(return_value=[])

        created = SimpleNamespace(id=99)

        with (
            patch(f"{MODULE}.CMSRepository", return_value=repo),
            patch(f"{MODULE}.FAQService") as service_class,
            patch(f"{MODULE}.sync_knowledge", new_callable=AsyncMock),
            patch(f"{MODULE}.safe_edit_text", new_callable=AsyncMock),
        ):
            service_class.return_value.create = AsyncMock(
                return_value=created
            )

            await faq_module.confirm_import(
                self.callback,
                self.session,
                self.settings,
                self.state,
                self.ai_service,
            )

            self.assertFalse(
                service_class.return_value.create.await_args.kwargs[
                    "is_active"
                ]
            )

    async def test_admin_csv_respects_active_flag(self):
        self.permissions.has.return_value = True

        self.state.get_data.return_value = {
            "import_rows": [{
                "row_number": 2,
                "question_ru": "Новый вопрос",
                "answer_ru": "Новый ответ",
                "question_kk": "",
                "answer_kk": "",
                "is_active": True,
            }],
            "import_duplicates": [],
        }

        repo = MagicMock()
        repo.faq_questions = AsyncMock(return_value=[])

        with (
            patch(f"{MODULE}.CMSRepository", return_value=repo),
            patch(f"{MODULE}.FAQService") as service_class,
            patch(f"{MODULE}.sync_knowledge", new_callable=AsyncMock),
            patch(f"{MODULE}.safe_edit_text", new_callable=AsyncMock),
        ):
            service_class.return_value.create = AsyncMock(
                return_value=SimpleNamespace(id=99)
            )

            await faq_module.confirm_import(
                self.callback,
                self.session,
                self.settings,
                self.state,
                self.ai_service,
            )

            self.assertTrue(
                service_class.return_value.create.await_args.kwargs[
                    "is_active"
                ]
            )


class TelegramFAQAutoPublishTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.session = MagicMock()
        self.session.add = MagicMock()

        self.chat_id = -1001234567890
        self.actor = 123456

        self.service = TelegramAutoDraftService(
            self.session,
            self.chat_id,
            self.actor,
        )

        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        self.post = SimpleNamespace(
            id=100,
            chat_id=self.chat_id,
            message_thread_id=10,
            status=TelegramContentStatus.NEW,
            created_faq_id=None,
            telegram_created_at=now,
            created_at=now,
            auto_process_after=None,
            auto_last_error=None,
        )

        self.config = SimpleNamespace(
            auto_draft_enabled=True,
            auto_draft_enabled_at=now - timedelta(minutes=5),
        )

        self.mapping = SimpleNamespace(
            id=1,
            is_active=True,
            processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
            auto_draft_enabled=True,
            processing_mode_enabled_at=None,
            content_target=TelegramContentTarget.FAQ,
            parent_page_id=None,
        )

        self.service.repo.settings = AsyncMock(
            return_value=self.config
        )
        self.service.repo.mapping = AsyncMock(
            return_value=self.mapping
        )
        self.service.repo.topic_names = AsyncMock(
            return_value={10: "FAQ"}
        )

        self.session.get = AsyncMock(return_value=None)

    async def check_faq_mode(self, mode, expected_active):
        self.mapping.processing_mode = mode

        parsed = SimpleNamespace(
            is_complete=True,
            confidence=faq_module_confidence_high(),
            question_ru="Как поступить?",
            answer_ru="Ответ",
            question_kk="Қалай түсуге болады?",
            answer_kk="Жауап",
            diagnostic=None,
        )

        self.service.faq_parser.parse = MagicMock(
            return_value=parsed
        )

        with (
            patch(f"{AUTO_MODULE}.source_text", return_value="FAQ"),
            patch(f"{AUTO_MODULE}.TelegramContentRepository") as repo_class,
            patch(f"{AUTO_MODULE}.TelegramContentImportService") as import_class,
            patch(
                f"{AUTO_MODULE}.queue_faq_knowledge_sync",
                new_callable=AsyncMock,
            ) as queue_mock,
        ):
            repo_class.return_value.media_count = AsyncMock(
                return_value=0
            )

            import_class.return_value.create_faq = AsyncMock(
                return_value=SimpleNamespace(
                    id=77,
                    is_active=expected_active,
                )
            )

            result = await self.service.process_claimed(self.post)

            self.assertIsNotNone(result)
            self.assertEqual(result.published, expected_active)

            self.assertEqual(
                import_class.return_value.create_faq.await_args.kwargs[
                    "is_active"
                ],
                expected_active,
            )

            if expected_active:
                queue_mock.assert_awaited_once()
            else:
                queue_mock.assert_not_awaited()

    async def test_auto_publish_creates_active_faq(self):
        await self.check_faq_mode(
            TelegramProcessingMode.AUTO_PUBLISH,
            True,
        )

    async def test_auto_draft_creates_inactive_faq(self):
        await self.check_faq_mode(
            TelegramProcessingMode.AUTO_DRAFT,
            False,
        )

    async def test_manual_mode_does_not_process(self):
        self.mapping.processing_mode = TelegramProcessingMode.MANUAL

        result = await self.service.process_claimed(self.post)

        self.assertIsNone(result)


def faq_module_confidence_high():
    from app.services.telegram_content_parser import (
        TelegramContentConfidence,
    )
    return TelegramContentConfidence.HIGH


if __name__ == "__main__":
    unittest.main()
