import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.callbacks import UserAttachmentCallback
from app.database.models.enums import MediaType
from app.handlers.user.attachments import back_to_page, current_page_materials, materials_keyboard, open_material
from app.keyboards.user import page_keyboard
from app.utils.i18n import attachment_caption


def attachment(index: int, *, active: bool = True, type_: MediaType = MediaType.DOCUMENT, revision_id: int = 10):
    return SimpleNamespace(id=index, file_name=f"file-{index}.pdf", type=type_, is_active=active, revision_id=revision_id, telegram_file_id=f"file-id-{index}", caption_ru="Подпись", caption_kk="Қолтаңба")


class UserMaterialsKeyboardTests(unittest.TestCase):
    def test_page_without_materials_has_no_button(self) -> None:
        markup = page_keyboard([], "ru", False, "page", 0, 7)
        self.assertFalse(any("Материалы" in button.text for row in markup.inline_keyboard for button in row))

    def test_page_counts_only_active_materials(self) -> None:
        values = [attachment(1), attachment(2, active=False)]
        active_count = sum(1 for item in values if item.is_active)
        markup = page_keyboard([], "ru", False, "page", active_count, 7)
        self.assertTrue(any(button.text == "📎 Материалы (1)" for row in markup.inline_keyboard for button in row))

    def test_material_pagination_boundaries(self) -> None:
        for count, pages in ((8, 1), (9, 2), (17, 3)):
            markup, result = materials_keyboard([attachment(i + 1) for i in range(count)], "ru", 7, 0)
            self.assertEqual(result.pages, pages)
            self.assertEqual(any(len(row) == 3 for row in markup.inline_keyboard), count > 8)
            material_rows = [row for row in markup.inline_keyboard if UserAttachmentCallback.unpack(row[0].callback_data).action == "open"]
            self.assertLessEqual(len(material_rows), 8)

    def test_callback_size_is_below_limit(self) -> None:
        packed = UserAttachmentCallback(action="open", page_id=9_223_372_036_854_775_807, item_id=9_223_372_036_854_775_807, page=999_999_999).pack()
        self.assertLess(len(packed.encode("utf-8")), 64)

    def test_ru_kk_captions_are_preserved(self) -> None:
        item = attachment(1)
        self.assertEqual(attachment_caption(item, "ru"), "Подпись")
        self.assertEqual(attachment_caption(item, "kk"), "Қолтаңба")

    def test_send_page_does_not_auto_send_files(self) -> None:
        from app.handlers.user import pages
        source = inspect.getsource(pages.send_page)
        self.assertNotIn("answer_document", source)
        self.assertNotIn("answer_photo", source)
        self.assertNotIn("published_revision.attachments", source)


class UserMaterialsHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_material_listing_uses_explicit_query_and_filters_inactive(self) -> None:
        class Revision:
            id = 10

            @property
            def attachments(self):
                raise AssertionError("lazy relationship must not be accessed")

        page = SimpleNamespace(id=7, is_active=True, published_revision_id=10, published_revision=Revision())
        repo = SimpleNamespace(by_id=AsyncMock(return_value=page), attachments=AsyncMock(return_value=[attachment(1), attachment(2, active=False)]))
        with patch("app.handlers.user.attachments.PageRepository", return_value=repo):
            returned_page, items = await current_page_materials(object(), 7)
        self.assertIs(returned_page, page)
        self.assertEqual([item.id for item in items], [1])
        repo.attachments.assert_awaited_once_with(10)

    async def _open(self, item, *, published_revision_id=10):
        message = SimpleNamespace(answer_document=AsyncMock(), answer_photo=AsyncMock())
        callback = SimpleNamespace(from_user=SimpleNamespace(id=1), message=message, answer=AsyncMock())
        page = SimpleNamespace(id=7, is_active=True, published_revision_id=published_revision_id)
        repo = SimpleNamespace(by_id=AsyncMock(return_value=page), attachment=AsyncMock(return_value=item))
        user = SimpleNamespace(content_language_code="ru")
        data = UserAttachmentCallback(action="open", page_id=7, item_id=1, page=0)
        with patch("app.handlers.user.attachments.PageRepository", return_value=repo), patch("app.handlers.user.attachments.upsert_user", new=AsyncMock(return_value=user)):
            await open_material(callback, data, object())
        return callback, message, repo

    async def test_open_document_uses_selected_file_id(self) -> None:
        callback, message, repo = await self._open(attachment(1))
        message.answer_document.assert_awaited_once_with("file-id-1", caption="Подпись")
        message.answer_photo.assert_not_awaited()
        repo.attachment.assert_awaited_once_with(10, 1)
        callback.answer.assert_awaited_once_with()

    async def test_open_photo_uses_selected_file_id(self) -> None:
        _, message, _ = await self._open(attachment(1, type_=MediaType.PHOTO))
        message.answer_photo.assert_awaited_once_with("file-id-1", caption="Подпись")

    async def test_inactive_attachment_is_not_sent(self) -> None:
        callback, message, _ = await self._open(attachment(1, active=False))
        message.answer_document.assert_not_awaited()
        callback.answer.assert_awaited_once()

    async def test_forged_or_old_revision_attachment_is_not_sent(self) -> None:
        callback, message, _ = await self._open(attachment(1, revision_id=9), published_revision_id=10)
        message.answer_document.assert_not_awaited()
        callback.answer.assert_awaited_once()

    async def test_back_does_not_change_navigation_or_track_view(self) -> None:
        callback = SimpleNamespace(from_user=SimpleNamespace(id=1), answer=AsyncMock())
        page = SimpleNamespace(id=7, key="program", is_active=True, published_revision_id=10)
        repo = SimpleNamespace(by_id=AsyncMock(return_value=page))
        settings = SimpleNamespace(ai_available=True)
        data = UserAttachmentCallback(action="back", page_id=7)
        with patch("app.handlers.user.attachments.PageRepository", return_value=repo), patch("app.handlers.user.attachments.send_page", new=AsyncMock()) as send:
            await back_to_page(callback, data, object(), settings)
        send.assert_awaited_once_with(callback, unittest.mock.ANY, "program", ai_available=True, track_view=False)


if __name__ == "__main__":
    unittest.main()
