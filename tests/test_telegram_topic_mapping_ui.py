from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.callbacks import ChannelPostCallback
from app.database.models import BotButton, BotPage, BotPageRevision, TelegramTopicPageMapping
from app.database.repositories.telegram_auto_drafts import TopicMappingListItem
from app.database.models.enums import PageStatus, TelegramProcessingMode
from app.handlers.admin.channel_posts import mapping_confirm, mapping_create, mapping_select_page, render_mappings, topic_label
from app.handlers.channel import capture_forum_topic_metadata, supported_content


class TelegramTopicMappingCallbackTests(unittest.IsolatedAsyncioTestCase):
    THREAD_IDS = (2, 30, 32, 2_147_483_647)

    @staticmethod
    def callback():
        return SimpleNamespace(
            from_user=SimpleNamespace(id=700),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

    @staticmethod
    def page() -> BotPage:
        page = BotPage(id=17, key="practice", status=PageStatus.PUBLISHED, is_active=True)
        page.draft_revision = BotPageRevision(id=21, page_id=17, title="Практика", text="Описание")
        return page

    async def test_full_mapping_flow_uses_explicit_numeric_thread_id(self):
        for thread_id in self.THREAD_IDS:
            with self.subTest(thread_id=thread_id):
                callback = self.callback()
                page = self.page()
                page_repo = SimpleNamespace(all=AsyncMock(return_value=[page]), by_id=AsyncMock(return_value=page))
                auto_repo = SimpleNamespace(
                    mapping=AsyncMock(return_value=None), mappings=AsyncMock(return_value=[]),
                    topic_names=AsyncMock(return_value={thread_id: "Практика"}),
                )
                session = SimpleNamespace(add=Mock(), flush=AsyncMock())
                settings = SimpleNamespace(content_chat_id=-100123)
                with patch("app.handlers.admin.channel_posts.PageRepository", return_value=page_repo), patch(
                    "app.handlers.admin.channel_posts.TelegramAutoDraftRepository", return_value=auto_repo
                ):
                    await mapping_select_page(
                        callback,
                        ChannelPostCallback(action="map_topic", item_id=thread_id),
                        session,  # type: ignore[arg-type]
                        settings,
                    )
                    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
                    select_payload = markup.inline_keyboard[0][0].callback_data
                    back_payload = markup.inline_keyboard[-1][0].callback_data
                    selected = ChannelPostCallback.unpack(select_payload)
                    back = ChannelPostCallback.unpack(back_payload)
                    self.assertEqual(selected.action, "map_page")
                    self.assertEqual(selected.item_id, 17)
                    self.assertEqual(selected.thread_id, thread_id)
                    self.assertEqual(selected.status, "NEW")
                    self.assertEqual(back.action, "map_add")
                    self.assertLessEqual(len(select_payload.encode("utf-8")), 64)

                    await mapping_confirm(callback, selected, session, settings)  # type: ignore[arg-type]
                    confirm_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
                    confirm = ChannelPostCallback.unpack(confirm_markup.inline_keyboard[0][0].callback_data)
                    confirm_back = ChannelPostCallback.unpack(confirm_markup.inline_keyboard[1][0].callback_data)
                    self.assertEqual(confirm.action, "map_confirm")
                    self.assertEqual(confirm.item_id, 17)
                    self.assertEqual(confirm.thread_id, thread_id)
                    self.assertEqual(confirm_back.action, "map_topic")
                    self.assertEqual(confirm_back.item_id, thread_id)

                    await mapping_create(callback, confirm, session, settings)  # type: ignore[arg-type]

                created = next(
                    call.args[0] for call in session.add.call_args_list
                    if isinstance(call.args[0], TelegramTopicPageMapping)
                )
                self.assertEqual(created.chat_id, -100123)
                self.assertEqual(created.message_thread_id, thread_id)
                self.assertEqual(created.parent_page_id, 17)
                self.assertFalse(any(isinstance(call.args[0], BotButton) for call in session.add.call_args_list))

    def test_largest_mapping_payload_is_below_telegram_limit(self):
        payloads = [
            ChannelPostCallback(action="map_topic", item_id=thread_id).pack()
            for thread_id in self.THREAD_IDS
        ] + [
            ChannelPostCallback(action="map_page", item_id=2_147_483_647, thread_id=thread_id).pack()
            for thread_id in self.THREAD_IDS
        ] + [
            ChannelPostCallback(action="map_confirm", item_id=2_147_483_647, thread_id=thread_id).pack()
            for thread_id in self.THREAD_IDS
        ]
        maximum = max(len(payload.encode("utf-8")) for payload in payloads)
        self.assertLessEqual(maximum, 64)

    def test_known_and_unknown_topic_labels(self):
        self.assertEqual(topic_label(2, "Практика"), "Практика")
        self.assertEqual(topic_label(32, None), "📂 Тема #32")

    async def test_mapping_list_uses_presentation_dto_without_page_relationship(self):
        callback = self.callback()
        items = [
            TopicMappingListItem(1, -100123, 2, "💼 Практика", 17, "practice", "Практика", True, True, TelegramProcessingMode.AUTO_DRAFT, True),
            TopicMappingListItem(2, -100123, 32, None, 18, "library", "library", True, False, TelegramProcessingMode.MANUAL, False),
        ]
        repo = SimpleNamespace(mappings=AsyncMock(return_value=items))
        with patch("app.handlers.admin.channel_posts.TelegramAutoDraftRepository", return_value=repo):
            await render_mappings(callback, object(), SimpleNamespace(content_chat_id=-100123))  # type: ignore[arg-type]
        rendered = callback.message.edit_text.await_args.args[0]
        self.assertIn("💼 Практика", rendered)
        self.assertIn("↳ 📄 Практика", rendered)
        self.assertIn("📂 Тема #32", rendered)
        self.assertIn("↳ 📄 library", rendered)


class ForumTopicMetadataTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message(*, thread_id: int, created: str | None = None, edited: str | None = None):
        return SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            message_thread_id=thread_id,
            forum_topic_created=SimpleNamespace(name=created) if created is not None else None,
            forum_topic_edited=SimpleNamespace(name=edited) if edited is not None else None,
            text=None, caption=None, photo=None, document=None,
        )

    async def test_created_and_edited_events_upsert_same_stable_identity(self):
        repo = SimpleNamespace(upsert_topic_name=AsyncMock())
        with patch("app.handlers.channel.TelegramAutoDraftRepository", return_value=repo):
            created = self.message(thread_id=2, created="Практика")
            edited = self.message(thread_id=2, edited="Практика и трудоустройство")
            await capture_forum_topic_metadata(created, object())  # type: ignore[arg-type]
            await capture_forum_topic_metadata(edited, object())  # type: ignore[arg-type]
        self.assertEqual(repo.upsert_topic_name.await_args_list[0].args, (-100123, 2, "Практика"))
        self.assertEqual(repo.upsert_topic_name.await_args_list[1].args, (-100123, 2, "Практика и трудоустройство"))
        mapping = TelegramTopicPageMapping(chat_id=-100123, message_thread_id=2, parent_page_id=17)
        self.assertEqual((mapping.chat_id, mapping.message_thread_id, mapping.parent_page_id), (-100123, 2, 17))

    def test_forum_service_event_is_not_supported_publication_content(self):
        self.assertFalse(supported_content(self.message(thread_id=2, created="Практика")))
