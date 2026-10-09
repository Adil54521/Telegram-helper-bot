import hashlib
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, FAQItem, FAQItemTranslation, FAQRevision
from app.database.models.enums import LanguageCode


def faq_content_fingerprint(question_ru: str, answer_ru: str, question_kk: str | None, answer_kk: str | None) -> str:
    normalized = "\x1f".join((question_ru.strip(), answer_ru.strip(), (question_kk or "").strip(), (answer_kk or "").strip()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FAQSnapshot:
    question_ru: str
    answer_ru: str
    question_kk: str | None
    answer_kk: str | None

    @property
    def fingerprint(self) -> str:
        return faq_content_fingerprint(self.question_ru, self.answer_ru, self.question_kk, self.answer_kk)


async def faq_snapshot(session: AsyncSession, faq: FAQItem) -> FAQSnapshot:
    translations = list((await session.scalars(
        select(FAQItemTranslation).where(FAQItemTranslation.faq_id == faq.id)
    )).all())
    ru = next((item for item in translations if item.language_code == LanguageCode.RU), None)
    kk = next((item for item in translations if item.language_code == LanguageCode.KK), None)
    return FAQSnapshot(
        ru.question if ru else faq.question,
        ru.answer if ru else faq.answer,
        kk.question if kk else None,
        kk.answer if kk else None,
    )


async def create_faq_revision(
    session: AsyncSession,
    faq: FAQItem,
    actor: int | None,
    *,
    source_type: str,
    source_post_id: int | None = None,
) -> FAQRevision:
    await session.flush()
    snapshot = await faq_snapshot(session, faq)
    current = await session.get(FAQRevision, faq.current_revision_id) if faq.current_revision_id else None
    if current and current.content_fingerprint == snapshot.fingerprint:
        return current
    version = int(await session.scalar(
        select(func.coalesce(func.max(FAQRevision.version), 0)).where(FAQRevision.faq_id == faq.id)
    ) or 0) + 1
    revision = FAQRevision(
        faq_id=faq.id,
        version=version,
        question_ru=snapshot.question_ru,
        answer_ru=snapshot.answer_ru,
        question_kk=snapshot.question_kk,
        answer_kk=snapshot.answer_kk,
        source_type=source_type,
        source_post_id=source_post_id,
        content_fingerprint=snapshot.fingerprint,
        created_by=actor,
    )
    session.add(revision)
    await session.flush()
    faq.current_revision_id = revision.id
    session.add(AuditLog(
        admin_id=actor or 0,
        action="FAQ_REVISION_CREATED",
        entity_type="FAQItem",
        entity_id=str(faq.id),
        new_data={"revision_id": revision.id, "version": version, "source_type": source_type, "source_post_id": source_post_id},
    ))
    return revision
