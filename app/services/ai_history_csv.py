from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.entities import AIConversation
from app.database.repositories.ai_history import AIHistoryRepository
from app.services.csv_export import build_csv


EXPORT_BATCH_SIZE = 500
MAX_EXPORT_ROWS = 50_000
MAX_EXPORT_BYTES = 45 * 1024 * 1024


class AIHistoryExportError(Exception):
    pass


def export_ai_history_csv(
    records: Sequence[AIConversation],
) -> bytes:
    headers = [
        "ID",
        "User ID",
        "Question",
        "Answer",
        "Language",
        "Status",
        "Created At",
        "Answered At",
    ]

    rows = (
        (
            record.id,
            record.user_id,
            record.question,
            record.answer or "",
            record.language_code,
            record.status,
            record.created_at.isoformat()
            if record.created_at else "",
            record.answered_at.isoformat()
            if record.answered_at else "",
        )
        for record in records
    )

    return build_csv(headers, rows)


async def generate_ai_history_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
) -> bytes:
    repo = AIHistoryRepository(session)

    total = await repo.count(status=status)

    if total > MAX_EXPORT_ROWS:
        raise AIHistoryExportError(
            f"Слишком много записей: {total}. "
            f"Максимум: {MAX_EXPORT_ROWS}."
        )

    records = []
    offset = 0

    while offset < total:
        batch = await repo.list_recent(
            limit=min(EXPORT_BATCH_SIZE, total - offset),
            offset=offset,
            status=status,
        )

        if not batch:
            raise AIHistoryExportError(
                "Не удалось получить все записи."
            )

        records.extend(batch)
        offset += len(batch)

    content = export_ai_history_csv(records)

    if len(content) > MAX_EXPORT_BYTES:
        raise AIHistoryExportError(
            "CSV превышает допустимый размер."
        )

    return content