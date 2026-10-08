import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.service import AIService
from app.callbacks import ChannelPostCallback
from app.database.models import PageAttachment, TelegramContentMedia, TelegramContentPost
from app.database.models.enums import LanguageCode, MediaType, TelegramContentStatus
from app.handlers.admin.channel_posts import material_change, prepare_manual
from app.services.telegram_publication_grouper import (
    MAX_GROUPING_LOOKAHEAD,
    PublicationGroupingResult,
    SelectedPublicationMaterial,
    TelegramPublicationGrouper,
    is_short_material_caption,
    suggest_material_language,
)
from app.services.channel_import import TelegramContentImportService


CONTENT_A = """5️⃣ ПРАКТИКА БОЙЫНША ЕСЕП ҮЛГІЛЕРІ

Қазақ және орыс тілдеріндегі практика есебінің үлгілерін төменнен жүктеп алуға болады.

5️⃣ ОБРАЗЦЫ ОТЧЁТОВ ПО ПРАКТИКЕ

Образцы отчётов на казахском и русском языках можно скачать ниже."""

CONTENT_B = """6️⃣ ПРАКТИКА БОЙЫНША МІНЕЗДЕМЕ ҮЛГІСІ

Практика бойынша мінездеме үлгісін төменнен жүктеп алуға болады.

6️⃣ ОБРАЗЕЦ ХАРАКТЕРИСТИКИ ПО ПРАКТИКЕ

Образец характеристики по практике можно скачать ниже."""


def post(
    post_id: int,
    message_id: int,
    *,
    text: str | None = None,
    caption: str | None = None,
    thread: int | None = None,
    chat_id: int = -1001,
    status: TelegramContentStatus = TelegramContentStatus.NEW,
    files: tuple[tuple[MediaType, str | None], ...] = (),
) -> TelegramContentPost:
    media = [
        TelegramContentMedia(
            id=post_id * 10 + index,
            content_post_id=post_id,
            source_message_id=message_id,
            type=media_type,
            telegram_file_id=f"file-{post_id}-{index}",
            file_unique_id=f"unique-{post_id}-{index}",
            file_name=file_name,
            file_size=100,
            position=index,
        )
        for index, (media_type, file_name) in enumerate(files, start=1)
    ]
    return TelegramContentPost(
        id=post_id,
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=thread,
        chat_type="supergroup",
        status=status,
        text=text,
        caption=caption,
        media=media,
    )


class FakeRepository:
    def __init__(self, following):
        self.following = following
        self.call = None

    async def following_posts(self, chat_id, message_thread_id, after_message_id, limit):
        self.call = (chat_id, message_thread_id, after_message_id, limit)
        return self.following


class TelegramPublicationGrouperTests(unittest.IsolatedAsyncioTestCase):
    async def group(self, source, following):
        repository = FakeRepository(following)
        result = await TelegramPublicationGrouper(repository).group(source)  # type: ignore[arg-type]
        if repository.call:
            self.assertEqual(repository.call[-1], MAX_GROUPING_LOOKAHEAD)
        return result

    async def test_real_kuam_boundaries(self):
        source_a = post(1, 100, text=CONTENT_A)
        kk = post(2, 101, files=((MediaType.DOCUMENT, "отчет казахский.docx"),))
        ru = post(3, 102, files=((MediaType.DOCUMENT, "отчет русский.docx"),))
        source_b = post(4, 103, text=CONTENT_B)
        final = post(5, 104, files=((MediaType.DOCUMENT, "характеристика.pdf"),))
        first = await self.group(source_a, [kk, ru, source_b, final])
        second = await self.group(source_b, [final])
        self.assertEqual([item.message_id for item in first.materials], [101, 102])
        self.assertEqual([item.message_id for item in second.materials], [104])

    async def test_ru_only_stops_scan(self):
        boundary = post(3, 102, text="НОВАЯ ПУБЛИКАЦИЯ\n\nПодробная информация для студентов университета.")
        result = await self.group(post(1, 100, text=CONTENT_A), [boundary, post(4, 103, files=((MediaType.DOCUMENT, "later.pdf"),))])
        self.assertEqual(result.materials, ())

    async def test_kk_only_stops_scan(self):
        boundary = post(3, 102, text="ЖАҢА ЖАРИЯЛАНЫМ\n\nҚұрметті студенттер үшін толық ақпарат жарияланды.")
        result = await self.group(post(1, 100, text=CONTENT_A), [boundary, post(4, 103, files=((MediaType.DOCUMENT, "later.pdf"),))])
        self.assertEqual(result.materials, ())

    async def test_significant_unknown_text_stops_scan(self):
        boundary = post(3, 102, text="Important mixed announcement 2027 with enough standalone content and details")
        result = await self.group(post(1, 100, text=CONTENT_A), [boundary, post(4, 103, files=((MediaType.DOCUMENT, "later.pdf"),))])
        self.assertEqual(result.materials, ())

    async def test_document_and_photo_are_candidates(self):
        document = post(2, 101, files=((MediaType.DOCUMENT, "report.pdf"),))
        photo = post(3, 102, files=((MediaType.PHOTO, None),))
        result = await self.group(post(1, 100, text=CONTENT_A), [document, photo])
        self.assertEqual([item.files[0].media_type for item in result.materials], ["DOCUMENT", "PHOTO"])

    async def test_short_document_caption_remains_candidate(self):
        material = post(2, 101, caption="Русский отчет", files=((MediaType.DOCUMENT, "report.docx"),))
        result = await self.group(post(1, 100, text=CONTENT_A), [material])
        self.assertEqual(len(result.materials), 1)

    async def test_long_document_caption_stops(self):
        caption = "Это самостоятельная публикация с подробным описанием нового события для всех студентов университета, содержащая полноценный информационный абзац и важные сведения о мероприятии."
        material = post(2, 101, caption=caption, files=((MediaType.DOCUMENT, "report.docx"),))
        result = await self.group(post(1, 100, text=CONTENT_A), [material])
        self.assertEqual(result.materials, ())

    async def test_same_chat_and_topic_are_required(self):
        wrong_chat = post(2, 101, chat_id=-2002, thread=10, files=((MediaType.DOCUMENT, "wrong.pdf"),))
        wrong_topic = post(3, 102, thread=11, files=((MediaType.DOCUMENT, "wrong-topic.pdf"),))
        right = post(4, 103, thread=10, files=((MediaType.DOCUMENT, "right.pdf"),))
        result = await self.group(post(1, 100, text=CONTENT_A, thread=10), [wrong_chat, wrong_topic, right])
        self.assertEqual([item.message_id for item in result.materials], [103])

    async def test_null_topic_only_accepts_null(self):
        topic = post(2, 101, thread=10, files=((MediaType.DOCUMENT, "topic.pdf"),))
        general = post(3, 102, thread=None, files=((MediaType.DOCUMENT, "general.pdf"),))
        result = await self.group(post(1, 100, text=CONTENT_A), [topic, general])
        self.assertEqual([item.message_id for item in result.materials], [102])

    async def test_processed_and_ignored_are_not_candidates(self):
        ignored = post(2, 101, status=TelegramContentStatus.IGNORED, files=((MediaType.DOCUMENT, "ignored.pdf"),))
        processed = post(3, 102, status=TelegramContentStatus.DRAFT_CREATED, files=((MediaType.DOCUMENT, "used.pdf"),))
        result = await self.group(post(1, 100, text=CONTENT_A), [ignored, processed])
        self.assertEqual(result.materials, ())

    async def test_lookahead_is_bounded(self):
        candidates = [post(index, 100 + index, files=((MediaType.DOCUMENT, f"{index}.pdf"),)) for index in range(2, 20)]
        result = await self.group(post(1, 100, text=CONTENT_A), candidates)
        self.assertEqual(len(result.materials), MAX_GROUPING_LOOKAHEAD)

    async def test_media_group_is_one_candidate_with_multiple_files(self):
        album = post(2, 101, files=((MediaType.PHOTO, None), (MediaType.PHOTO, None)))
        result = await self.group(post(1, 100, text=CONTENT_A), [album])
        self.assertEqual(len(result.materials), 1)
        self.assertEqual(len(result.materials[0].files), 2)

    async def test_file_only_source_does_not_group_recursively(self):
        source = post(1, 100, caption="report.pdf", files=((MediaType.DOCUMENT, "report.pdf"),))
        result = await self.group(source, [post(2, 101, files=((MediaType.DOCUMENT, "next.pdf"),))])
        self.assertEqual(result.materials, ())

    async def test_state_contains_no_orm_objects(self):
        result = await self.group(post(1, 100, text=CONTENT_A), [post(2, 101, files=((MediaType.DOCUMENT, "report.pdf"),))])
        state = result.materials[0].to_state()
        self.assertIsInstance(state["files"], list)
        self.assertTrue(all(isinstance(file, dict) for file in state["files"]))
        self.assertFalse(any(isinstance(value, (TelegramContentPost, TelegramContentMedia)) for value in state.values()))


class MaterialLanguageTests(unittest.TestCase):
    def test_ru_markers(self):
        for value in ("отчет русский.docx", "report_ru.pdf", "РУС отчет.pdf"):
            self.assertEqual(suggest_material_language(value), LanguageCode.RU)

    def test_kk_markers(self):
        for value in ("отчет казахский.docx", "report_kk.pdf", "Қазақша үлгі.docx", "file_kz.pdf"):
            self.assertEqual(suggest_material_language(value), LanguageCode.KK)

    def test_ambiguous_and_substring_false_positives(self):
        self.assertIsNone(suggest_material_language("common_report.pdf"))
        self.assertIsNone(suggest_material_language("структура.docx"))
        self.assertIsNone(suggest_material_language("ru_kk_report.pdf"))

    def test_caption_heuristic(self):
        self.assertTrue(is_short_material_caption("Отчет", ("report.pdf",)))
        self.assertTrue(is_short_material_caption("report.pdf", ("report.pdf",)))
        self.assertFalse(is_short_material_caption("Очень длинное самостоятельное описание " * 10, ("report.pdf",)))


class MaterialSelectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_and_language_override_update_only_fsm(self):
        source = post(1, 100, text=CONTENT_A)
        candidate = (await TelegramPublicationGrouper(FakeRepository([
            post(2, 101, files=((MediaType.DOCUMENT, "report_ru.pdf"),))
        ])).group(source)).materials[0]  # type: ignore[arg-type]
        grouping = PublicationGroupingResult(source.id, (candidate,))
        state_data = {
            "channel_material_source_id": 1,
            "channel_materials": [candidate.to_state()],
        }
        state = SimpleNamespace(get_data=AsyncMock(return_value=state_data), update_data=AsyncMock())
        callback = SimpleNamespace(message=None, answer=AsyncMock())
        settings = SimpleNamespace(content_chat_id=-1001)
        with (
            patch("app.handlers.admin.channel_posts.load_post", new=AsyncMock(return_value=source)),
            patch("app.handlers.admin.channel_posts.group_post", new=AsyncMock(return_value=grouping)),
            patch("app.handlers.admin.channel_posts.render_material_selection", new=AsyncMock()),
        ):
            await material_change(callback, ChannelPostCallback(action="m_toggle", item_id=2), object(), settings, state)  # type: ignore[arg-type]
        toggled = state.update_data.await_args.kwargs["channel_materials"][0]
        self.assertFalse(toggled["selected"])

        state_data["channel_materials"] = [candidate.to_state(language=LanguageCode.RU)]
        state.get_data.return_value = state_data
        with (
            patch("app.handlers.admin.channel_posts.load_post", new=AsyncMock(return_value=source)),
            patch("app.handlers.admin.channel_posts.group_post", new=AsyncMock(return_value=grouping)),
            patch("app.handlers.admin.channel_posts.render_material_selection", new=AsyncMock()),
        ):
            await material_change(callback, ChannelPostCallback(action="m_kk", item_id=2), object(), settings, state)  # type: ignore[arg-type]
        changed = state.update_data.await_args.kwargs["channel_materials"][0]
        self.assertEqual(changed["selected_language"], LanguageCode.KK.value)

    async def test_page_prefill_passes_material_metadata_without_ai(self):
        source = post(1, 100, text=CONTENT_A)
        candidate = (await TelegramPublicationGrouper(FakeRepository([
            post(2, 101, files=((MediaType.DOCUMENT, "report_ru.pdf"),))
        ])).group(source)).materials[0]  # type: ignore[arg-type]
        grouping = PublicationGroupingResult(source.id, (candidate,))
        callback = SimpleNamespace(message=SimpleNamespace(), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), update_data=AsyncMock())
        settings = SimpleNamespace(content_chat_id=-1001)
        with (
            patch("app.handlers.admin.channel_posts.load_post", new=AsyncMock(return_value=source)),
            patch("app.handlers.admin.channel_posts.group_post", new=AsyncMock(return_value=grouping)),
            patch("app.handlers.admin.channel_posts.remember_wizard", new=AsyncMock()),
            patch("app.handlers.admin.channel_posts.show_draft", new=AsyncMock()),
            patch.object(AIService, "prepare_channel_content", new=AsyncMock()) as ai_mock,
        ):
            await prepare_manual(callback, ChannelPostCallback(action="manual_page", item_id=1), object(), settings, state, "PAGE")  # type: ignore[arg-type]
        data = state.update_data.await_args.kwargs
        self.assertEqual(data["channel_materials"][0]["content_post_id"], 2)
        self.assertNotIsInstance(data["channel_materials"][0], TelegramContentPost)
        ai_mock.assert_not_awaited()

    def test_callback_payload_stays_under_limit(self):
        packed = ChannelPostCallback(action="m_toggle", item_id=2_147_483_647, page=9999, status="DRAFT_CREATED").pack()
        self.assertLessEqual(len(packed.encode()), 64)


class FakeCreateSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


class FinalValidationRepository(FakeRepository):
    def __init__(self, source, following, locked):
        super().__init__(following)
        self.source = source
        self.locked = locked

    async def by_id(self, item_id, chat_id, lock=False):
        return self.source if item_id == self.source.id and chat_id == self.source.chat_id else None

    async def by_ids_for_update(self, item_ids, chat_id):
        return [item for item in self.locked if item.id in item_ids and item.chat_id == chat_id]


class MaterialFinalValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forged_candidate_is_rejected_before_page_creation(self):
        source = post(1, 100, text=CONTENT_A)
        service = TelegramContentImportService(FakeCreateSession())  # type: ignore[arg-type]
        service.repo = FinalValidationRepository(source, [], [])  # type: ignore[assignment]
        with patch("app.services.channel_import.PageService.create", new=AsyncMock()) as create:
            with self.assertRaisesRegex(ValueError, "устарел"):
                await service.create_page(
                    1, -1001, 7,
                    title_ru="Title", text_ru="Body",
                    materials=(SelectedPublicationMaterial(999),),
                )
        create.assert_not_awaited()

    async def test_candidate_used_by_concurrent_flow_is_rejected(self):
        source = post(1, 100, text=CONTENT_A)
        suggested = post(2, 101, files=((MediaType.DOCUMENT, "report.pdf"),))
        used = post(2, 101, status=TelegramContentStatus.DRAFT_CREATED, files=((MediaType.DOCUMENT, "report.pdf"),))
        service = TelegramContentImportService(FakeCreateSession())  # type: ignore[arg-type]
        service.repo = FinalValidationRepository(source, [suggested], [used])  # type: ignore[assignment]
        with patch("app.services.channel_import.PageService.create", new=AsyncMock()) as create:
            with self.assertRaisesRegex(ValueError, "уже использован"):
                await service.create_page(
                    1, -1001, 7,
                    title_ru="Title", text_ru="Body",
                    materials=(SelectedPublicationMaterial(2),),
                )
        create.assert_not_awaited()

    async def test_success_adds_existing_attachments_and_marks_material_processed(self):
        source = post(1, 100, text=CONTENT_A)
        material = post(2, 101, files=((MediaType.DOCUMENT, "report.pdf"),))
        session = FakeCreateSession()
        service = TelegramContentImportService(session)  # type: ignore[arg-type]
        service.repo = FinalValidationRepository(source, [material], [material])  # type: ignore[assignment]
        page = SimpleNamespace(id=50, draft_revision_id=60)
        with patch("app.services.channel_import.PageService.create", new=AsyncMock(return_value=page)):
            result = await service.create_page(
                1, -1001, 7,
                title_ru="Title", text_ru="Body",
                materials=(SelectedPublicationMaterial(2, LanguageCode.RU),),
            )
        self.assertIs(result, page)
        attachments = [item for item in session.added if isinstance(item, PageAttachment)]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].telegram_file_id, material.media[0].telegram_file_id)
        self.assertEqual(material.status, TelegramContentStatus.DRAFT_CREATED)
        self.assertEqual(material.created_page_id, 50)


if __name__ == "__main__":
    unittest.main()
