import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.handlers.admin.attachments import listing


class _LazyLoadGuard:
    @property
    def attachments(self):
        raise AssertionError("listing must not load draft_revision.attachments")


class AttachmentListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_uses_explicit_repository_query(self) -> None:
        revision = _LazyLoadGuard()
        revision.id = 77
        page = SimpleNamespace(draft_revision=revision)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            message=object(),
            answer=AsyncMock(),
        )
        service = SimpleNamespace(ensure_editable_draft=AsyncMock(return_value=page))
        repository = SimpleNamespace(attachments=AsyncMock(return_value=[]))

        with (
            patch("app.handlers.admin.attachments.PageService", return_value=service),
            patch("app.handlers.admin.attachments.PageRepository", return_value=repository),
            patch("app.handlers.admin.attachments.safe_edit_text", new=AsyncMock()) as edit,
        ):
            await listing(callback, page_id=5, session=object(), current=0)

        repository.attachments.assert_awaited_once_with(77)
        edit.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
