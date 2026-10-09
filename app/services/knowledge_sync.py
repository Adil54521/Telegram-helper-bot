import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.index import KnowledgeIndexService
from app.database.models import AIKnowledgeSyncJob, AuditLog, BotPage, FAQItem

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


async def recover_knowledge_jobs(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await session.execute(
            update(AIKnowledgeSyncJob)
            .where(AIKnowledgeSyncJob.status == "RUNNING")
            .values(status="PENDING", next_attempt_at=datetime.now(timezone.utc))
        )
        await session.commit()


async def process_knowledge_job(
    factory: async_sessionmaker[AsyncSession],
    index: KnowledgeIndexService | None,
    actor: int,
) -> bool:
    if index is None:
        return False
    now = datetime.now(timezone.utc)
    async with factory() as claim_session:
        job = await claim_session.scalar(
            select(AIKnowledgeSyncJob)
            .where(AIKnowledgeSyncJob.status == "PENDING", AIKnowledgeSyncJob.next_attempt_at <= now)
            .order_by(AIKnowledgeSyncJob.next_attempt_at, AIKnowledgeSyncJob.id)
            .with_for_update(skip_locked=True).limit(1)
        )
        if not job:
            return False
        job.status = "RUNNING"
        job_id, expected_revision, faq_id = job.id, (job.faq_revision_id if job.faq_id else job.revision_id), job.faq_id
        await claim_session.commit()
    try:
        async with factory() as session:
            page = await session.get(BotPage, job.page_id) if job.page_id else None
            faq = await session.get(FAQItem, faq_id) if faq_id else None
            if faq_id is None and (not page or page.published_revision_id != expected_revision):
                current = await session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
                if current and page and page.published_revision_id:
                    current.revision_id = page.published_revision_id
                    current.status = "PENDING"
                    current.next_attempt_at = now
                elif current:
                    await session.delete(current)
                await session.commit()
                return True
            if faq_id is not None and not faq:
                current = await session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
                if current:
                    await session.delete(current)
                await session.commit()
                return True
            if faq_id is not None and faq.current_revision_id != expected_revision:
                current = await session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
                if current:
                    current.faq_revision_id = faq.current_revision_id
                    current.status = "PENDING"
                    current.next_attempt_at = now
                await session.commit()
                return True
            result = await index.sync_all(session)
            published_revision = await session.scalar(select(BotPage.published_revision_id).where(BotPage.id == page.id)) if page else None
            if page and published_revision != expected_revision:
                await session.rollback()
                async with factory() as retry_session:
                    current = await retry_session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
                    if current:
                        current.revision_id = published_revision
                        current.status = "PENDING"
                        current.next_attempt_at = now
                        await retry_session.commit()
                return True
            current_faq_revision = await session.scalar(select(FAQItem.current_revision_id).where(FAQItem.id == faq.id)) if faq else None
            if faq and current_faq_revision != expected_revision:
                await session.rollback()
                async with factory() as retry_session:
                    current = await retry_session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
                    if current:
                        current.faq_revision_id = current_faq_revision
                        current.status = "PENDING"
                        current.next_attempt_at = now
                        await retry_session.commit()
                return True
            current = await session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
            if current:
                current.status = "COMPLETED"
                current.last_error = None
            session.add(AuditLog(
                admin_id=actor, action="AI_KNOWLEDGE_SYNC_COMPLETED", entity_type="FAQItem" if faq else "BotPage",
                entity_id=str(faq.id if faq else page.id), new_data={"revision_id": expected_revision, "chunks": result.total},
            ))
            await session.commit()
        return True
    except Exception as error:
        logger.warning("Knowledge sync failed job_id=%s error=%s", job_id, type(error).__name__)
        async with factory() as session:
            current = await session.get(AIKnowledgeSyncJob, job_id, with_for_update=True)
            if current:
                current.attempts += 1
                current.status = "PENDING" if current.attempts < MAX_ATTEMPTS else "FAILED"
                current.next_attempt_at = now + timedelta(seconds=min(300, 30 * current.attempts))
                current.last_error = type(error).__name__[:255]
                session.add(AuditLog(
                    admin_id=actor, action="AI_KNOWLEDGE_SYNC_FAILED", entity_type="FAQItem" if current.faq_id else "BotPage",
                    entity_id=str(current.faq_id or current.page_id), new_data={"revision_id": current.revision_id, "error_type": type(error).__name__, "attempt": current.attempts},
                ))
                await session.commit()
        return True


async def queue_faq_knowledge_sync(session: AsyncSession, faq_id: int, actor: int, *, revision_id: int | None = None) -> None:
    if revision_id is None:
        revision_id = await session.scalar(select(FAQItem.current_revision_id).where(FAQItem.id == faq_id))
    if revision_id is None:
        return
    job = await session.scalar(
        select(AIKnowledgeSyncJob).where(AIKnowledgeSyncJob.faq_id == faq_id).with_for_update()
    )
    if job is None:
        job = AIKnowledgeSyncJob(faq_id=faq_id, page_id=None, revision_id=None, faq_revision_id=revision_id)
        session.add(job)
    else:
        job.faq_revision_id = revision_id
        job.status = "PENDING"
        job.attempts = 0
        job.next_attempt_at = datetime.now(timezone.utc)
        job.last_error = None
    session.add(AuditLog(
        admin_id=actor, action="AI_KNOWLEDGE_SYNC_QUEUED", entity_type="FAQItem",
        entity_id=str(faq_id), new_data={"faq_id": faq_id},
    ))
