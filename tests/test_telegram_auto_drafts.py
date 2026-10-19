from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.callbacks import ChannelPostCallback
from app.database.models import AuditLog, BotButton, BotButtonTranslation, BotPage, BotPageRevision, BotPageRevisionTranslation, PageAttachment, TelegramAttachmentReplacement, TelegramAutoDraftLink, TelegramContentPost, TelegramImportSettings, TelegramTopicPageMapping
from app.database.models.enums import ButtonAction, LanguageCode, MediaType, PageStatus, TelegramContentStatus, TelegramProcessingMode
from app.database.repositories.telegram_auto_drafts import TelegramAutoDraftRepository
from app.services.pages import PageService
from app.services.permissions import Permission
from app.services.telegram_auto_drafts import AutoDraftCreated, TelegramAutoDraftService, _notify_created
from app.services.telegram_content_parser import ParsedTelegramContent, TelegramContentConfidence, TelegramContentParseKind
from app.handlers.channel import _should_notify_new_post
from app.handlers.admin.channel_posts import _render_file_pending


BILINGUAL = """5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ

Қазақ және орыс тілдеріндегі практика есебінің үлгілерін төменнен жүктеп алуға болады.

5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ

Образцы отчётов на казахском и русском языках можно скачать ниже."""


class AutoProcessingNotificationTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=True, super_admin_id=42)

    async def test_cms_off_suppresses_auto_draft_and_auto_publish_notifications(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        for published in (False, True):
            result = AutoDraftCreated(1, 2, "Page", "Topic", 1, published)
            with patch(
                "app.services.telegram_auto_drafts.TelegramAutoDraftRepository.notifications_enabled",
                new=AsyncMock(return_value=False),
            ):
                await _notify_created(bot, object(), self.settings(), result)  # type: ignore[arg-type]
        bot.send_message.assert_not_awaited()

    async def test_cms_on_sends_auto_publish_notification(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        result = AutoDraftCreated(1, 2, "Page", "Topic", 1, True)
        with patch(
            "app.services.telegram_auto_drafts.TelegramAutoDraftRepository.notifications_enabled",
            new=AsyncMock(return_value=True),
        ):
            await _notify_created(bot, session, self.settings(), result)  # type: ignore[arg-type]
        bot.send_message.assert_awaited_once()

    async def test_database_setting_has_priority_and_env_is_only_fallback(self):
        session = SimpleNamespace(scalar=AsyncMock(return_value=TelegramImportSettings(chat_id=-1001, notify_admins=False)))
        repo = TelegramAutoDraftRepository(session)  # type: ignore[arg-type]
        self.assertFalse(await repo.notifications_enabled(-1001, True))
        session.scalar.return_value = None
        self.assertTrue(await repo.notifications_enabled(-1001, True))
        self.assertFalse(await repo.notifications_enabled(-1001, False))

    async def test_only_manual_or_unmapped_posts_get_immediate_notification(self):
        post = TelegramContentPost(chat_id=-1001, message_id=1, message_thread_id=2, chat_type="supergroup")
        session = SimpleNamespace()
        for mode, expected in (
            (TelegramProcessingMode.MANUAL, True),
            (TelegramProcessingMode.AUTO_DRAFT, False),
            (TelegramProcessingMode.AUTO_PUBLISH, False),
        ):
            mapping = TelegramTopicPageMapping(
                chat_id=-1001, message_thread_id=2, parent_page_id=1,
                processing_mode=mode, is_active=True,
            )
            with patch("app.handlers.channel.TelegramAutoDraftRepository.mapping", new=AsyncMock(return_value=mapping)):
                self.assertEqual(await _should_notify_new_post(session, post), expected)  # type: ignore[arg-type]
        with patch("app.handlers.channel.TelegramAutoDraftRepository.mapping", new=AsyncMock(return_value=None)):
            self.assertTrue(await _should_notify_new_post(session, post))  # type: ignore[arg-type]


class AttachmentReviewPresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_material_review_shows_page_old_new_and_change_type(self):
        old = PageAttachment(
            id=5,
            revision_id=7,
            type=MediaType.DOCUMENT,
            telegram_file_id="old",
            file_name="old.pdf",
        )

        pending = TelegramAttachmentReplacement(
            id=9,
            source_post_id=2,
            page_id=3,
            attachment_id=5,
            base_revision_id=7,
            media_type=MediaType.DOCUMENT,
            new_file_id="new",
            new_file_name="new.pdf",
            source_message_id=20,
            status="REVIEW",
        )

        revision = BotPageRevision(
            id=7,
            page_id=3,
            title="Документы",
            text="Body",
        )

        page = BotPage(
            id=3,
            key="documents",
            draft_revision=revision,
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123456),
            message=object(),
            answer=AsyncMock(),
        )

        session = SimpleNamespace(get=AsyncMock(return_value=old))
        settings = MagicMock()

        with (
            patch("app.handlers.admin.channel_posts.PageRepository.by_id", new=AsyncMock(return_value=page)),
            patch("app.handlers.admin.channel_posts.PermissionService") as permissions_class,
            patch("app.handlers.admin.channel_posts.safe_edit_text", new_callable=AsyncMock) as edit,
        ):
            permissions_class.return_value.has = AsyncMock(return_value=True)

            await _render_file_pending(callback, session, pending, settings)

        rendered = edit.await_args.args[1]

        self.assertIn("Документы", rendered)
        self.assertIn("old.pdf", rendered)
        self.assertIn("new.pdf", rendered)
        self.assertIn("Замена файла", rendered)

        permissions_class.return_value.has.assert_awaited_once_with(123456, Permission.CONTENT_PUBLISH)
        
    async def test_editor_cannot_see_file_apply_button(self):
        pending = TelegramAttachmentReplacement(
            id=9,
            source_post_id=2,
            page_id=3,
            attachment_id=5,
            base_revision_id=7,
            media_type=MediaType.DOCUMENT,
            new_file_id="new",
            new_file_name="new.pdf",
            source_message_id=20,
            status="REVIEW",
        )

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123456),
            message=object(),
            answer=AsyncMock(),
        )

        session = SimpleNamespace(get=AsyncMock(return_value=None))
        settings = MagicMock()

        with (
            patch("app.handlers.admin.channel_posts.PageRepository.by_id", new=AsyncMock(return_value=None)),
            patch("app.handlers.admin.channel_posts.PermissionService") as permissions_class,
            patch("app.handlers.admin.channel_posts.safe_edit_text", new_callable=AsyncMock) as edit,
        ):
            permissions_class.return_value.has = AsyncMock(return_value=False)

            await _render_file_pending(
                callback,
                session,
                pending,
                settings,
            )

        markup = edit.await_args.kwargs["reply_markup"]

        actions = [
            ChannelPostCallback.unpack(button.callback_data).action
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]

        self.assertNotIn("file_apply", actions)
        self.assertIn("file_reject", actions)


class AutoDraftSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        value = TelegramAutoDraftService(SimpleNamespace(), -1001, 42)  # type: ignore[arg-type]
        value.repo = SimpleNamespace(settings=AsyncMock(), mapping=AsyncMock(), pending_source=AsyncMock())
        return value

    def post(self, *, thread_id=10, created=None, text=BILINGUAL):
        return TelegramContentPost(
            id=1, chat_id=-1001, message_id=100, message_thread_id=thread_id,
            chat_type="supergroup", status=TelegramContentStatus.NEW, text=text,
            telegram_created_at=created or datetime.now(timezone.utc),
        )

    async def test_global_default_off_never_schedules(self):
        service = self.service()
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=False)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
        post = self.post()
        await service.schedule(post)
        self.assertIsNone(post.auto_process_after)

    async def test_enable_boundary_leaves_old_new_post_manual(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now, settle_seconds=60)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
        post = self.post(created=now - timedelta(seconds=1))
        await service.schedule(post)
        self.assertIsNone(post.auto_process_after)

    async def test_bilingual_high_new_post_is_db_scheduled(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=1), settle_seconds=60)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
        post = self.post(created=now)
        await service.schedule(post)
        self.assertIsNotNone(post.auto_process_after)
        self.assertEqual(post.auto_processing_attempts, 0)

    async def test_material_resets_only_same_topic_pending_source(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        pending = self.post(created=now, text=BILINGUAL)
        pending.message_id = 90
        pending.auto_process_after = now
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=1), settle_seconds=60)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
        service.repo.pending_source.return_value = pending
        material = self.post(created=now, text="report_ru.docx")
        await service.schedule(material, media_count=1)
        service.repo.pending_source.assert_awaited_once_with(-1001, 10, 100)
        self.assertGreater(pending.auto_process_after, now)

    async def test_null_general_topic_is_not_scheduled(self):
        service = self.service()
        post = self.post(thread_id=None)
        await service.schedule(post)
        self.assertIsNone(post.auto_process_after)
        service.repo.settings.assert_not_awaited()

    async def test_manual_mode_is_not_scheduled(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=1), settle_seconds=30)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=False, processing_mode=TelegramProcessingMode.MANUAL, processing_mode_enabled_at=now - timedelta(seconds=1))
        post = self.post(created=now)
        await service.schedule(post)
        self.assertIsNone(post.auto_process_after)

    async def test_auto_publish_mode_is_db_scheduled(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=1), settle_seconds=30)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, processing_mode=TelegramProcessingMode.AUTO_PUBLISH, processing_mode_enabled_at=now - timedelta(seconds=1))
        post = self.post(created=now)
        await service.schedule(post)
        self.assertIsNotNone(post.auto_process_after)

    async def test_mode_boundary_rejects_older_post(self):
        service = self.service()
        now = datetime.now(timezone.utc)
        service.repo.settings.return_value = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(minutes=10), settle_seconds=30)
        service.repo.mapping.return_value = TelegramTopicPageMapping(chat_id=-1001, message_thread_id=10, parent_page_id=2, is_active=True, auto_draft_enabled=True, processing_mode=TelegramProcessingMode.AUTO_PUBLISH, processing_mode_enabled_at=now)
        post = self.post(created=now - timedelta(seconds=1))
        await service.schedule(post)
        self.assertIsNone(post.auto_process_after)


class AutoPublishProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_publish_rejects_non_strict_parser_results(self):
        now = datetime.now(timezone.utc)
        parent = BotPage(id=2, key="practice", status=PageStatus.PUBLISHED, is_active=True)
        mapping = TelegramTopicPageMapping(
            id=3, chat_id=-1001, message_thread_id=2, parent_page_id=2,
            is_active=True, auto_draft_enabled=True,
            processing_mode=TelegramProcessingMode.AUTO_PUBLISH,
            processing_mode_enabled_at=now - timedelta(seconds=1),
            auto_attach_materials=False,
        )
        config = TelegramImportSettings(
            chat_id=-1001, auto_draft_enabled=True,
            auto_draft_enabled_at=now - timedelta(seconds=2), settle_seconds=30,
        )
        invalid = (
            ParsedTelegramContent(TelegramContentParseKind.RU_ONLY, "RU", "Body", confidence=TelegramContentConfidence.HIGH),
            ParsedTelegramContent(TelegramContentParseKind.KK_ONLY, title_kk="KK", text_kk="Body", confidence=TelegramContentConfidence.HIGH),
            ParsedTelegramContent(TelegramContentParseKind.UNKNOWN),
            ParsedTelegramContent(
                TelegramContentParseKind.BILINGUAL, "RU", "Body", "KK", "Body",
                TelegramContentConfidence.MEDIUM,
            ),
        )
        for parsed in invalid:
            with self.subTest(kind=parsed.kind, confidence=parsed.confidence):
                post = TelegramContentPost(
                    id=10, chat_id=-1001, message_id=100, message_thread_id=2,
                    chat_type="supergroup", status=TelegramContentStatus.NEW,
                    text=BILINGUAL, telegram_created_at=now, media=[],
                )
                session = SimpleNamespace(get=AsyncMock(return_value=parent), add=Mock(), flush=AsyncMock())
                service = TelegramAutoDraftService(session, -1001, 42)  # type: ignore[arg-type]
                service.repo = SimpleNamespace(
                    settings=AsyncMock(return_value=config),
                    mapping=AsyncMock(return_value=mapping),
                )
                service.parser = SimpleNamespace(parse=Mock(return_value=parsed))
                with patch("app.services.telegram_auto_drafts.TelegramContentImportService.create_page", new=AsyncMock()) as create:
                    result = await service.process_claimed(post)
                self.assertIsNone(result)
                self.assertEqual(post.auto_last_error, "publication is not complete BILINGUAL/HIGH")
                create.assert_not_awaited()

    async def test_auto_publish_reuses_create_and_publish_services(self):
        now = datetime.now(timezone.utc)
        post = TelegramContentPost(id=10, chat_id=-1001, message_id=100, message_thread_id=2, chat_type="supergroup", status=TelegramContentStatus.NEW, text=BILINGUAL, telegram_created_at=now, media=[])
        parent = BotPage(id=2, key="practice", status=PageStatus.PUBLISHED, is_active=True)
        child = BotPage(id=4, key="reports", status=PageStatus.DRAFT, is_active=True, draft_revision_id=8)
        mapping = TelegramTopicPageMapping(id=3, chat_id=-1001, message_thread_id=2, parent_page_id=2, is_active=True, auto_draft_enabled=True, processing_mode=TelegramProcessingMode.AUTO_PUBLISH, processing_mode_enabled_at=now - timedelta(seconds=1), auto_attach_materials=False)
        config = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=2), settle_seconds=30)
        session = SimpleNamespace(get=AsyncMock(return_value=parent), add=Mock(), flush=AsyncMock())
        service = TelegramAutoDraftService(session, -1001, 42)  # type: ignore[arg-type]
        service.repo = SimpleNamespace(settings=AsyncMock(return_value=config), mapping=AsyncMock(return_value=mapping), topic_names=AsyncMock(return_value={2: "Практика"}))
        with patch("app.services.telegram_auto_drafts.TelegramContentRepository.media_count", new=AsyncMock(return_value=0)), patch("app.services.telegram_auto_drafts.TelegramContentImportService.create_page", new=AsyncMock(return_value=child)) as create, patch("app.services.telegram_auto_drafts.PageService.publish", new=AsyncMock()) as publish:
            result = await service.process_claimed(post)
        create.assert_awaited_once()
        publish.assert_awaited_once_with(child.id, 42)
        self.assertTrue(result.published)
        self.assertTrue(any(isinstance(call.args[0], TelegramAutoDraftLink) for call in session.add.call_args_list))
        self.assertTrue(any(isinstance(call.args[0], AuditLog) and call.args[0].action == "TELEGRAM_AUTO_PUBLISHED" for call in session.add.call_args_list))

    async def test_auto_draft_does_not_publish(self):
        now = datetime.now(timezone.utc)
        post = TelegramContentPost(id=10, chat_id=-1001, message_id=100, message_thread_id=2, chat_type="supergroup", status=TelegramContentStatus.NEW, text=BILINGUAL, telegram_created_at=now, media=[])
        parent = BotPage(id=2, key="practice", status=PageStatus.PUBLISHED, is_active=True)
        child = BotPage(id=4, key="reports", status=PageStatus.DRAFT, is_active=True, draft_revision_id=8)
        mapping = TelegramTopicPageMapping(id=3, chat_id=-1001, message_thread_id=2, parent_page_id=2, is_active=True, auto_draft_enabled=True, processing_mode=TelegramProcessingMode.AUTO_DRAFT, processing_mode_enabled_at=now - timedelta(seconds=1), auto_attach_materials=False)
        config = TelegramImportSettings(chat_id=-1001, auto_draft_enabled=True, auto_draft_enabled_at=now - timedelta(seconds=2), settle_seconds=30)
        session = SimpleNamespace(get=AsyncMock(return_value=parent), add=Mock(), flush=AsyncMock())
        service = TelegramAutoDraftService(session, -1001, 42)  # type: ignore[arg-type]
        service.repo = SimpleNamespace(settings=AsyncMock(return_value=config), mapping=AsyncMock(return_value=mapping), topic_names=AsyncMock(return_value={2: "Практика"}))
        with patch("app.services.telegram_auto_drafts.TelegramContentRepository.media_count", new=AsyncMock(return_value=0)), patch("app.services.telegram_auto_drafts.TelegramContentImportService.create_page", new=AsyncMock(return_value=child)), patch("app.services.telegram_auto_drafts.PageService.publish", new=AsyncMock()) as publish:
            result = await service.process_claimed(post)
        publish.assert_not_awaited()
        self.assertFalse(result.published)


class AutoDraftPublishLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_page_publish_never_creates_parent_button(self):
        page = BotPage(id=20, key="manual", status=PageStatus.DRAFT, is_active=True, draft_revision_id=30)
        page.draft_revision = BotPageRevision(id=30, page_id=20, title="Ручная страница", text="Текст", translations=[
            BotPageRevisionTranslation(language_code=LanguageCode.RU, title="Ручная страница", text="Текст"),
        ])
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, None, None]),
            add=Mock(), flush=AsyncMock(),
        )
        with patch("app.services.cms.ButtonService.create", new=AsyncMock()) as create:
            service = PageService(session)  # type: ignore[arg-type]
            service.repo = SimpleNamespace(by_id=AsyncMock(return_value=page))
            await service.publish(page.id, 42)
        create.assert_not_awaited()
        self.assertEqual(page.status, PageStatus.PUBLISHED)
        self.assertEqual(page.published_revision_id, page.draft_revision_id)
        self.assertFalse(any(isinstance(call.args[0], TelegramAutoDraftLink) for call in session.add.call_args_list))

    async def test_publish_link_creates_exactly_one_page_button(self):
        link = TelegramAutoDraftLink(id=1, source_post_id=3, child_page_id=4, parent_page_id=2)
        parent = BotPage(id=2, key="practice", status=PageStatus.PUBLISHED, is_active=True, published_revision_id=11, draft_revision_id=12)
        old_button = BotButton(id=7, page_id=2, text="Старая кнопка", action_type=ButtonAction.PAGE, target="old", position=0, row=0, is_active=True, translations=[])
        child = BotPage(id=4, key="reports", status=PageStatus.DRAFT, is_active=True)
        child.draft_revision_id = 8
        child.draft_revision = BotPageRevision(id=8, page_id=4, title="Отчёты", text="Текст", translations=[
            BotPageRevisionTranslation(language_code=LanguageCode.RU, title="Отчёты", text="Текст"),
            BotPageRevisionTranslation(language_code=LanguageCode.KK, title="Есептер", text="Мәтін"),
        ])
        revision = child.draft_revision
        button = BotButton(id=9, page_id=2, text="Отчёты", action_type=ButtonAction.PAGE, target="reports", position=1, row=1, is_active=True, translations=[
            BotButtonTranslation(language_code=LanguageCode.RU, text="Отчёты"),
            BotButtonTranslation(language_code=LanguageCode.KK, text="Есептер"),
        ])
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[link, revision, None, None, link, revision, button, None]),
            get=AsyncMock(return_value=parent),
            add=Mock(),
            flush=AsyncMock(),
        )
        with patch("app.services.cms.ButtonService.create", new=AsyncMock(return_value=button)) as create:
            service = PageService(session)  # type: ignore[arg-type]
            service.repo = SimpleNamespace(by_id=AsyncMock(return_value=child))
            self.assertEqual(create.await_count, 0)
            await service.publish(child.id, 42)
            button.is_active = False
            await service.publish(child.id, 42)
        create.assert_awaited_once()
        self.assertEqual(link.button_id, 9)
        self.assertTrue(button.is_active)
        self.assertEqual((old_button.text, old_button.target, old_button.is_active), ("Старая кнопка", "old", True))
        self.assertEqual((parent.published_revision_id, parent.draft_revision_id), (11, 12))

    async def test_missing_link_is_recovered_from_auto_draft_audit(self):
        parent = BotPage(id=2, key="practice", status=PageStatus.PUBLISHED, is_active=True)
        child = BotPage(id=4, key="reports", status=PageStatus.DRAFT, is_active=True, draft_revision_id=8)
        revision = BotPageRevision(id=8, page_id=4, title="Отчёты", text="Текст", translations=[
            BotPageRevisionTranslation(language_code=LanguageCode.RU, title="Отчёты", text="Текст"),
            BotPageRevisionTranslation(language_code=LanguageCode.KK, title="Есептер", text="Мәтін"),
        ])
        child.draft_revision = revision
        source = TelegramContentPost(id=3, chat_id=-1001, message_id=10, message_thread_id=2, chat_type="supergroup", status=TelegramContentStatus.DRAFT_CREATED, created_page_id=4)
        audit = AuditLog(id=5, admin_id=42, action="TELEGRAM_AUTO_DRAFT_CREATED", entity_type="BotPage", entity_id="4", new_data={"source_post_id": 3, "parent_page_id": 2})
        mapping = TelegramTopicPageMapping(id=6, chat_id=-1001, message_thread_id=2, parent_page_id=2, is_active=True, auto_draft_enabled=True, auto_attach_materials=True)
        button = BotButton(id=9, page_id=2, text="Отчёты", action_type=ButtonAction.PAGE, target="reports", position=1, row=1, is_active=True, translations=[])
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[None, audit, mapping, revision, None, None]),
            get=AsyncMock(side_effect=[source, parent]), add=Mock(), flush=AsyncMock(),
        )
        with patch("app.services.cms.ButtonService.create", new=AsyncMock(return_value=button)) as create:
            service = PageService(session)  # type: ignore[arg-type]
            service.repo = SimpleNamespace(by_id=AsyncMock(return_value=child))
            await service.publish(child.id, 42)
        create.assert_awaited_once()
        recovered = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], TelegramAutoDraftLink))
        self.assertEqual((recovered.child_page_id, recovered.parent_page_id, recovered.source_post_id), (4, 2, 3))
        self.assertEqual(recovered.button_id, 9)

    async def test_publish_queues_knowledge_sync(self):
        page = BotPage(
            id=20,
            key="news",
            status=PageStatus.DRAFT,
            is_active=True,
            draft_revision_id=30,
        )

        page.draft_revision = BotPageRevision(
            id=30,
            page_id=20,
            title="Новости",
            text="Текст новости",
            translations=[
                BotPageRevisionTranslation(
                    language_code=LanguageCode.RU,
                    title="Новости",
                    text="Текст новости",
                ),
            ],
        )

        session = SimpleNamespace(
            scalar=AsyncMock(return_value=None),
            add=Mock(),
            flush=AsyncMock(),
        )

        service = PageService(session)
        service.repo = SimpleNamespace(
            by_id=AsyncMock(return_value=page),
        )

        with patch.object(
            service,
            "_queue_knowledge_sync",
            new_callable=AsyncMock,
        ) as sync_mock:
            await service.publish(page.id, 42)

        sync_mock.assert_awaited_once()
        self.assertEqual(page.status, PageStatus.PUBLISHED)
        self.assertEqual(page.published_revision_id, 30)
