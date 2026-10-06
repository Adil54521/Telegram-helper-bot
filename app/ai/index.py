import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.provider import AIProvider
from app.database.models import AIKnowledgeChunk, BotPage, BotPageRevision, FAQItem
from app.database.models.enums import PageStatus

CHUNK_SIZE = 1600
CHUNK_OVERLAP = 180
EMBEDDING_BATCH_SIZE = 16
SPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    source_type: str
    source_id: int
    revision_id: int | None
    language_code: str
    chunk_index: int
    title: str
    content: str
    content_hash: str

    @property
    def identity(self) -> tuple[str, int, int | None, str, int]:
        return self.source_type, self.source_id, self.revision_id, self.language_code, self.chunk_index


@dataclass(frozen=True, slots=True)
class SyncResult:
    total: int
    created_or_updated: int
    unchanged: int
    deleted: int


def normalize_text(value: str | None) -> str:
    lines = [SPACE_RE.sub(" ", line).strip() for line in (value or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def split_chunks(value: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = normalize_text(value)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if boundary > start:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _hash(spec: tuple[object, ...]) -> str:
    return hashlib.sha256("\x1f".join(str(value) for value in spec).encode("utf-8")).hexdigest()


def make_specs(
    source_type: str,
    source_id: int,
    revision_id: int | None,
    language: str,
    title: str,
    content: str,
) -> list[ChunkSpec]:
    clean_title = normalize_text(title)
    return [
        ChunkSpec(
            source_type, source_id, revision_id, language, index, clean_title, chunk,
            _hash((source_type, source_id, revision_id, language, index, clean_title, chunk)),
        )
        for index, chunk in enumerate(split_chunks(content))
    ]


def changed_specs(specs: list[ChunkSpec], existing: list[AIKnowledgeChunk], embedding_model: str) -> list[ChunkSpec]:
    existing_map = {
        (item.source_type, item.source_id, item.revision_id, item.language_code, item.chunk_index): item
        for item in existing
    }
    return [
        item for item in specs
        if item.identity not in existing_map
        or existing_map[item.identity].content_hash != item.content_hash
        or existing_map[item.identity].embedding_model != embedding_model
    ]


class KnowledgeIndexService:
    def __init__(self, provider: AIProvider, embedding_model: str, batch_size: int = EMBEDDING_BATCH_SIZE) -> None:
        self.provider = provider
        self.embedding_model = embedding_model
        self.batch_size = batch_size

    async def collect(self, session: AsyncSession) -> list[ChunkSpec]:
        pages = list((await session.scalars(
            select(BotPage)
            .where(BotPage.is_active.is_(True), BotPage.status == PageStatus.PUBLISHED, BotPage.published_revision_id.is_not(None))
            .options(selectinload(BotPage.published_revision).selectinload(BotPageRevision.translations))
        )).all())
        faqs = list((await session.scalars(
            select(FAQItem).where(FAQItem.is_active.is_(True)).options(selectinload(FAQItem.translations))
        )).all())
        specs: list[ChunkSpec] = []
        for page in pages:
            revision = page.published_revision
            if not revision:
                continue
            specs.extend(make_specs("page", page.id, revision.id, "ru", revision.title, revision.text))
            for translation in revision.translations:
                language = str(getattr(translation.language_code, "value", translation.language_code)).lower()
                if language == "kk":
                    specs.extend(make_specs("page", page.id, revision.id, "kk", translation.title, translation.text))
        for faq in faqs:
            specs.extend(make_specs("faq", faq.id, None, "ru", faq.question, faq.answer))
            for translation in faq.translations:
                language = str(getattr(translation.language_code, "value", translation.language_code)).lower()
                if language == "kk":
                    specs.extend(make_specs("faq", faq.id, None, "kk", translation.question, translation.answer))
        return specs

    async def preview(self, session: AsyncSession) -> SyncResult:
        specs = await self.collect(session)
        existing = list((await session.scalars(select(AIKnowledgeChunk))).all())
        identities = {item.identity for item in specs}
        changed = len(changed_specs(specs, existing, self.embedding_model))
        return SyncResult(len(specs), changed, len(specs) - changed, sum(self._identity(item) not in identities for item in existing))

    async def sync_all(self, session: AsyncSession) -> SyncResult:
        specs = await self.collect(session)
        existing = list((await session.scalars(select(AIKnowledgeChunk))).all())
        changed = changed_specs(specs, existing, self.embedding_model)
        vectors: list[list[float]] = []
        for offset in range(0, len(changed), self.batch_size):
            batch = changed[offset:offset + self.batch_size]
            vectors.extend(await self.provider.embed_documents([f"Title: {item.title}\nContent: {item.content}" for item in batch]))
        desired = {item.identity for item in specs}
        stale_ids = [item.id for item in existing if self._identity(item) not in desired]
        if stale_ids:
            await session.execute(delete(AIKnowledgeChunk).where(AIKnowledgeChunk.id.in_(stale_ids)))
        for item, vector in zip(changed, vectors, strict=True):
            statement = insert(AIKnowledgeChunk).values(
                source_type=item.source_type, source_id=item.source_id, revision_id=item.revision_id,
                language_code=item.language_code, chunk_index=item.chunk_index, title=item.title,
                content=item.content, content_hash=item.content_hash, embedding_model=self.embedding_model,
                embedding=vector,
            ).on_conflict_do_update(
                constraint="uq_ai_knowledge_chunk_identity",
                set_={
                    "title": item.title, "content": item.content, "content_hash": item.content_hash,
                    "embedding_model": self.embedding_model, "embedding": vector, "updated_at": func.now(),
                },
            )
            await session.execute(statement)
        await session.flush()
        return SyncResult(len(specs), len(changed), len(specs) - len(changed), len(stale_ids))

    @staticmethod
    def _identity(item: AIKnowledgeChunk) -> tuple[str, int, int | None, str, int]:
        return item.source_type, item.source_id, item.revision_id, item.language_code, item.chunk_index
