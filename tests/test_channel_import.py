import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetChatMember
from app.ai.provider import TokenUsage
from app.ai.service import AIService, ChannelContentPreparation
from app.callbacks import ChannelPostCallback
from app.database.models import TelegramContentMedia, TelegramContentPost
from app.database.models.enums import MediaType, PageStatus, TelegramContentStatus
from app.handlers.channel import ConfiguredGroupFilter, OfficialGroupContentFilter, _ingest, _notify_admins, supported_content
from app.bot import handle_update_error
from app.services.channel_import import IngestResult, TelegramContentImportService, message_media, source_text, suggested_title
from app.utils.presentation import html_text


def message(*, channel=-1001, message_id=1, text=None, caption=None, group=None, thread=None, photo=None, document=None, chat_type="supergroup", from_user=None, sender_chat=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=channel, type=chat_type, title="KUAM"), message_id=message_id, text=text, caption=caption,
        media_group_id=group, message_thread_id=thread, photo=photo, document=document, date=datetime.now(timezone.utc),
        from_user=from_user, sender_chat=sender_chat,
    )


class FakeSession:
    def __init__(self):
        self.added = []
        self.next_id = 1

    def add(self, value):
        self.added.append(value)
        if getattr(value, "id", None) is None:
            value.id = self.next_id
            self.next_id += 1

    async def flush(self):
        return None


class FakeRepository:
    def __init__(self, post=None):
        self.post = post

    async def by_source(self, chat_id, message_id):
        if self.post and (self.post.message_id == message_id or any(item.source_message_id == message_id for item in self.post.media)):
            return self.post
        return None

    async def by_group(self, chat_id, media_group_id, message_thread_id):
        return self.post if self.post and self.post.media_group_id == media_group_id and self.post.message_thread_id == message_thread_id else None

    async def media_state(self, post_id, source_message_id):
        items = self.post.__dict__.get("media", []) if self.post and self.post.id == post_id else []
        return len(items), any(item.source_message_id == source_message_id for item in items)


class TelegramImportNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cms_off_suppresses_new_publication_notification(self):
        settings = SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=True, super_admin_id=42)
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch(
            "app.handlers.channel.TelegramAutoDraftRepository.notifications_enabled",
            new=AsyncMock(return_value=False),
        ):
            await _notify_admins(bot, object(), settings, 7, "Publication")  # type: ignore[arg-type]
        bot.send_message.assert_not_awaited()

    async def test_cms_on_sends_even_when_legacy_env_default_is_off(self):
        settings = SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=False, super_admin_id=42)
        bot = SimpleNamespace(send_message=AsyncMock())
        session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        with patch(
            "app.handlers.channel.TelegramAutoDraftRepository.notifications_enabled",
            new=AsyncMock(return_value=True),
        ):
            await _notify_admins(bot, session, settings, 7, "Publication")  # type: ignore[arg-type]
        bot.send_message.assert_awaited_once()


class ChannelIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_post_is_persisted_without_ai(self):
        session = FakeSession()
        service = TelegramContentImportService(session)  # type: ignore[arg-type]
        service.repo = FakeRepository()  # type: ignore[assignment]
        result = await service.ingest(message(text="Приём в магистратуру"))  # type: ignore[arg-type]
        self.assertTrue(result.created)
        self.assertEqual(result.post.status, TelegramContentStatus.NEW)
        self.assertEqual(source_text(result.post), "Приём в магистратуру")
        self.assertEqual(result.media_count, 0)
        self.assertIn(result.post, session.added)
        self.assertFalse(any("Gemini" in type(item).__name__ for item in session.added))

    async def test_duplicate_message_reuses_existing_post(self):
        post = TelegramContentPost(id=4, chat_id=-1001, message_id=8, chat_type="channel", status=TelegramContentStatus.NEW, text="old", media=[])
        service = TelegramContentImportService(FakeSession())  # type: ignore[arg-type]
        service.repo = FakeRepository(post)  # type: ignore[assignment]
        result = await service.ingest(message(message_id=8, text="same"))  # type: ignore[arg-type]
        self.assertFalse(result.created)
        self.assertEqual(result.post.id, 4)
        self.assertEqual(result.media_count, 0)

    async def test_edited_new_updates_but_processed_does_not(self):
        post = TelegramContentPost(id=4, chat_id=-1001, message_id=8, chat_type="supergroup", status=TelegramContentStatus.NEW, text="old", media=[])
        service = TelegramContentImportService(FakeSession())  # type: ignore[arg-type]
        service.repo = FakeRepository(post)  # type: ignore[assignment]
        result = await service.ingest(message(message_id=8, text="new"), edited=True)  # type: ignore[arg-type]
        self.assertEqual(post.text, "new")
        self.assertEqual(result.media_count, 0)
        post.status = TelegramContentStatus.DRAFT_CREATED
        await service.ingest(message(message_id=8, text="must not replace"), edited=True)  # type: ignore[arg-type]
        self.assertEqual(post.text, "new")

    async def test_media_group_is_one_candidate_with_multiple_media(self):
        first_media = TelegramContentMedia(source_message_id=10, type=MediaType.PHOTO, telegram_file_id="a", position=0)
        post = TelegramContentPost(id=5, chat_id=-1001, message_id=10, media_group_id="album", message_thread_id=7, chat_type="supergroup", status=TelegramContentStatus.NEW, media=[first_media])
        service = TelegramContentImportService(FakeSession())  # type: ignore[arg-type]
        service.repo = FakeRepository(post)  # type: ignore[assignment]
        photo = [SimpleNamespace(file_id="b", file_unique_id="ub", file_size=100)]
        result = await service.ingest(message(message_id=11, group="album", thread=7, photo=photo))  # type: ignore[arg-type]
        self.assertFalse(result.created)
        self.assertEqual(result.media_count, 2)

    async def test_same_album_id_in_different_topics_is_not_mixed(self):
        first = TelegramContentPost(id=5, chat_id=-1001, message_id=10, media_group_id="album", message_thread_id=7, chat_type="supergroup", status=TelegramContentStatus.NEW, media=[])
        service = TelegramContentImportService(FakeSession())  # type: ignore[arg-type]
        service.repo = FakeRepository(first)  # type: ignore[assignment]
        result = await service.ingest(message(message_id=12, group="album", thread=8, text="other topic"))  # type: ignore[arg-type]
        self.assertTrue(result.created)
        self.assertEqual(result.post.message_thread_id, 8)

    async def test_null_thread_id_is_supported(self):
        service = TelegramContentImportService(FakeSession())  # type: ignore[arg-type]
        service.repo = FakeRepository()  # type: ignore[assignment]
        result = await service.ingest(message(message_id=13, thread=None, text="general"))  # type: ignore[arg-type]
        self.assertIsNone(result.post.message_thread_id)

    async def test_photo_and_document_metadata(self):
        photo = message_media(message(photo=[SimpleNamespace(file_id="p", file_unique_id="u", file_size=12)]))  # type: ignore[arg-type]
        document = message_media(message(document=SimpleNamespace(file_id="d", file_unique_id="du", file_name="a.pdf", file_size=42)))  # type: ignore[arg-type]
        self.assertEqual(photo[0], MediaType.PHOTO)  # type: ignore[index]
        self.assertEqual(document[0], MediaType.DOCUMENT)  # type: ignore[index]
        self.assertEqual(document[3], "a.pdf")  # type: ignore[index]

    async def test_photo_and_document_ingestion_counts_one(self):
        values = (
            message(photo=[SimpleNamespace(file_id="p", file_unique_id="u", file_size=12)]),
            message(document=SimpleNamespace(file_id="d", file_unique_id="du", file_name="a.pdf", file_size=42)),
        )
        for value in values:
            session = FakeSession()
            service = TelegramContentImportService(session)  # type: ignore[arg-type]
            service.repo = FakeRepository()  # type: ignore[assignment]
            result = await service.ingest(value)  # type: ignore[arg-type]
            self.assertEqual(result.media_count, 1)
            self.assertEqual(sum(isinstance(item, TelegramContentMedia) for item in session.added), 1)

    async def test_other_channel_is_ignored_server_side(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        with patch("app.handlers.channel.TelegramContentImportService.ingest", new=AsyncMock()) as ingest:
            await _ingest(message(channel=-2002), object(), settings, object(), edited=False)  # type: ignore[arg-type]
            ingest.assert_not_awaited()

    async def test_configured_channel_calls_ingestion_once_and_no_ai(self):
        post = TelegramContentPost(id=1, chat_id=-1001, message_id=1, chat_type="channel", status=TelegramContentStatus.NEW, text="x")
        settings = SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=False)
        with patch("app.handlers.channel.TelegramContentImportService.ingest", new=AsyncMock(return_value=IngestResult(post, True, 0))) as ingest, patch("app.handlers.channel._notify_admins", new=AsyncMock()):
            await _ingest(message(text="channel post", chat_type=ChatType.CHANNEL), object(), settings, object(), edited=False)  # type: ignore[arg-type]
            ingest.assert_awaited_once()

    async def test_live_group_flow_never_touches_lazy_media_and_notifies_after_ingest(self):
        events = []

        class GuardedPost:
            id = 91
            status = TelegramContentStatus.NEW
            text = "Official group publication"
            caption = None

            @property
            def media(self):
                raise AssertionError("lazy relationship must not be accessed")

        async def ingest(*args, **kwargs):
            events.append("saved")
            return IngestResult(GuardedPost(), True, 1)  # type: ignore[arg-type]

        async def notify(*args, **kwargs):
            events.append("notified")

        settings = SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=True)
        value = message(
            text="Official group publication",
            chat_type=ChatType.SUPERGROUP,
            from_user=SimpleNamespace(id=7, is_bot=False),
        )
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)))
        self.assertTrue(await OfficialGroupContentFilter()(value, settings, bot))
        with (
            patch("app.handlers.channel.TelegramContentImportService.ingest", new=AsyncMock(side_effect=ingest)) as ingest_mock,
            patch("app.handlers.channel._notify_admins", new=AsyncMock(side_effect=notify)) as notify_mock,
            patch.object(AIService, "prepare_channel_content", new=AsyncMock()) as ai_mock,
        ):
            result = await _ingest(value, object(), settings, bot, edited=False)  # type: ignore[arg-type]
        self.assertEqual(result.media_count, 1)  # type: ignore[union-attr]
        self.assertEqual(events, ["saved", "notified"])
        ingest_mock.assert_awaited_once()
        notify_mock.assert_awaited_once()
        ai_mock.assert_not_awaited()

    async def test_edited_message_does_not_notify(self):
        post = TelegramContentPost(id=1, chat_id=-1001, message_id=1, chat_type="supergroup", status=TelegramContentStatus.NEW, text="edited")
        settings = SimpleNamespace(content_chat_id=-1001, content_channel_notify_admins=True)
        with (
            patch("app.handlers.channel.TelegramContentImportService.ingest", new=AsyncMock(return_value=IngestResult(post, False, 0))),
            patch("app.handlers.channel._notify_admins", new=AsyncMock()) as notify,
        ):
            result = await _ingest(message(text="edited"), object(), settings, object(), edited=True)  # type: ignore[arg-type]
        self.assertEqual(result.media_count, 0)  # type: ignore[union-attr]
        notify.assert_not_awaited()


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    async def generate_structured(self, **kwargs):
        self.calls += 1
        return self.output, TokenUsage(10, 20, 30)

    async def close(self): pass


class ChannelAITests(unittest.IsolatedAsyncioTestCase):
    async def _prepare(self, output):
        provider = FakeProvider(output)
        service = AIService(provider, 0)  # type: ignore[arg-type]
        service._record = AsyncMock()  # type: ignore[method-assign]
        value = await service.prepare_channel_content(object(), user_id=1, source="Только исходный пост")  # type: ignore[arg-type]
        return provider, value

    async def test_structured_page_uses_one_call(self):
        provider, value = await self._prepare({"recommended_type":"PAGE","reason_short":"Справочная информация","title_ru":"Приём","text_ru":"Текст","title_kk":"Қабылдау","text_kk":"Мәтін"})
        self.assertEqual(provider.calls, 1)
        self.assertIsInstance(value, ChannelContentPreparation)
        self.assertEqual(value.recommended_type, "PAGE")

    async def test_structured_faq_uses_one_call(self):
        provider, value = await self._prepare({"recommended_type":"FAQ","reason_short":"Вопрос и ответ","question_ru":"Как?","answer_ru":"Так.","question_kk":"Қалай?","answer_kk":"Осылай."})
        self.assertEqual(provider.calls, 1)
        self.assertEqual(value.recommended_type, "FAQ")

    async def test_ignore_is_only_recommendation(self):
        provider, value = await self._prepare({"recommended_type":"IGNORE","reason_short":"Временная новость"})
        self.assertEqual(provider.calls, 1)
        self.assertEqual(value.recommended_type, "IGNORE")


class ChannelSafetyTests(unittest.TestCase):
    def test_callback_data_stays_below_telegram_limit(self):
        packed = ChannelPostCallback(action="edit_question_kk", item_id=2_147_483_647, page=9999, status="DRAFT_CREATED").pack()
        self.assertLessEqual(len(packed.encode()), 64)

    def test_html_is_escaped(self):
        self.assertEqual(html_text("IT <2026> & KUAM"), "IT &lt;2026&gt; &amp; KUAM")

    def test_title_is_deterministic(self):
        self.assertEqual(suggested_title("\n Магистратура 2026\nПодробности"), "Магистратура 2026")

    def test_page_model_defaults_to_draft_in_service_contract(self):
        self.assertEqual(PageStatus.DRAFT.value, "DRAFT")

    def test_service_message_without_supported_content_is_noise(self):
        value = message(text=None, photo=None, document=None)
        value.new_chat_members = [SimpleNamespace(id=1)]
        self.assertFalse(supported_content(value))


class GroupAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_administrator_and_owner_are_allowed(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        for status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))
            value = message(text="official", from_user=SimpleNamespace(id=7, is_bot=False), chat_type=ChatType.SUPERGROUP)
            self.assertTrue(await OfficialGroupContentFilter()(value, settings, bot))

    async def test_non_admin_statuses_are_rejected(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        for status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED, ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
            bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))
            value = message(text="participant", from_user=SimpleNamespace(id=7, is_bot=False), chat_type=ChatType.GROUP)
            self.assertFalse(await OfficialGroupContentFilter()(value, settings, bot))

    async def test_anonymous_admin_same_chat_is_allowed_foreign_sender_chat_is_not(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        own = message(text="official", sender_chat=SimpleNamespace(id=-1001), chat_type=ChatType.SUPERGROUP)
        foreign = message(text="foreign", sender_chat=SimpleNamespace(id=-2002), chat_type=ChatType.SUPERGROUP)
        self.assertTrue(await OfficialGroupContentFilter()(own, settings, bot))
        self.assertFalse(await OfficialGroupContentFilter()(foreign, settings, bot))
        bot.get_chat_member.assert_not_awaited()

    async def test_private_other_chat_and_bot_messages_are_rejected(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        private = message(channel=-1001, text="private", chat_type=ChatType.PRIVATE, from_user=SimpleNamespace(id=7, is_bot=False))
        other = message(channel=-2002, text="other", chat_type=ChatType.SUPERGROUP, from_user=SimpleNamespace(id=7, is_bot=False))
        bot_message = message(text="bot", chat_type=ChatType.SUPERGROUP, from_user=SimpleNamespace(id=7, is_bot=True))
        self.assertFalse(await OfficialGroupContentFilter()(private, settings, bot))
        self.assertFalse(await OfficialGroupContentFilter()(other, settings, bot))
        self.assertFalse(await OfficialGroupContentFilter()(bot_message, settings, bot))

    async def test_admin_check_api_failure_fails_closed(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        error = TelegramNetworkError(method=GetChatMember(chat_id=-1001, user_id=7), message="network")
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=error))
        value = message(text="official", from_user=SimpleNamespace(id=7, is_bot=False), chat_type=ChatType.SUPERGROUP)
        self.assertFalse(await OfficialGroupContentFilter()(value, settings, bot))

    async def test_configured_group_filter_isolated_from_private_and_other_groups(self):
        settings = SimpleNamespace(content_chat_id=-1001)
        content_group = message(channel=-1001, text="content", chat_type=ChatType.SUPERGROUP)
        private = message(channel=-1001, text="private", chat_type=ChatType.PRIVATE)
        other_group = message(channel=-2002, text="other", chat_type=ChatType.GROUP)
        filter_ = ConfiguredGroupFilter()
        self.assertTrue(await filter_(content_group, settings))
        self.assertFalse(await filter_(private, settings))
        self.assertFalse(await filter_(other_group, settings))


class GlobalErrorHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_supergroup_and_channel_errors_only_log(self):
        for chat_type, field in (
            (ChatType.GROUP, "message"),
            (ChatType.SUPERGROUP, "edited_message"),
            (ChatType.CHANNEL, "channel_post"),
        ):
            origin = SimpleNamespace(chat=SimpleNamespace(type=chat_type), answer=AsyncMock())
            values = {name: None for name in ("message", "edited_message", "channel_post", "edited_channel_post", "callback_query")}
            values[field] = origin
            event = SimpleNamespace(update=SimpleNamespace(**values), exception=RuntimeError("boom"))
            logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
            self.assertTrue(await handle_update_error(event, logger))
            origin.answer.assert_not_awaited()

    async def test_private_error_keeps_user_message(self):
        origin = SimpleNamespace(chat=SimpleNamespace(type=ChatType.PRIVATE), answer=AsyncMock())
        update = SimpleNamespace(
            message=origin, edited_message=None, channel_post=None,
            edited_channel_post=None, callback_query=None,
        )
        event = SimpleNamespace(update=update, exception=RuntimeError("boom"))
        logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
        self.assertTrue(await handle_update_error(event, logger))
        origin.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
