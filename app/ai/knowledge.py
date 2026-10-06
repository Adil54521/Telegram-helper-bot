import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import BotPage, BotPageRevision, FAQItem
from app.database.models.enums import PageStatus
from app.database.repositories.knowledge import KnowledgeChunkRepository
from app.ai.provider import AIProvider, AIProviderError
from app.utils.i18n import translated

MAX_RESULTS = 3
SEMANTIC_CANDIDATES = 12
MIN_SEMANTIC_SCORE = 0.55
MAX_CONTEXT_CHARS = 6000
MAX_ITEM_CHARS = 1600
WORD_RE = re.compile(r"[\wа-яёәіңғүұқөһ]+", re.IGNORECASE)
STOP_WORDS = {"и", "в", "на", "по", "о", "об", "а", "как", "что", "какие", "для", "мен", "және", "туралы", "қандай", "қалай", "не", "бар"}
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KnowledgeItem:
    kind: str
    entity_id: int
    title: str
    content: str
    fallback: bool = False
    score: float = 0
    chunk_index: int | None = None
    semantic_score: float = 0
    lexical_score: float = 0


def _normalize_word(word: str) -> str:
    word = word.lower().replace("ё", "е")
    roots = {
        "программ": "программ",
        "поступ": "поступ",
        "контакт": "контакт",
        "телефон": "телефон",
        "образоват": "образоват",
        "бағдарлам": "бағдарлам",
        "қабылда": "қабылда",
        "байланыс": "байланыс",
    }
    for prefix, root in roots.items():
        if word.startswith(prefix):
            return root
    return word


def _tokens(value: str) -> set[str]:
    return {
        _normalize_word(word)
        for word in WORD_RE.findall(value)
        if len(word) > 1 and word.lower() not in STOP_WORDS
    }


class KnowledgeService:
    def __init__(
        self,
        embedding_provider: AIProvider | None = None,
        *,
        semantic_enabled: bool = False,
        min_semantic_score: float = MIN_SEMANTIC_SCORE,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.semantic_enabled = semantic_enabled
        self.min_semantic_score = min_semantic_score

    async def search(self, session: AsyncSession, query: str, language: str, limit: int = MAX_RESULTS) -> list[KnowledgeItem]:
        items, _ = await self.search_with_mode(session, query, language, limit)
        return items

    async def search_with_mode(
        self, session: AsyncSession, query: str, language: str, limit: int = MAX_RESULTS,
    ) -> tuple[list[KnowledgeItem], str]:
        lexical = await self._lexical_search(session, query, language, max(limit, SEMANTIC_CANDIDATES))
        if not self.semantic_enabled or not self.embedding_provider:
            return lexical[:limit], "lexical"
        try:
            vector = await self.embedding_provider.embed_query(query)
            semantic = await self._semantic_search(session, vector, language)
        except (AIProviderError, OSError, RuntimeError) as error:
            logger.warning("Semantic retrieval unavailable; using lexical fallback: %s", error)
            return lexical[:limit], "lexical_fallback"
        return self._merge(query, semantic, lexical, limit), "hybrid"

    async def _semantic_search(self, session: AsyncSession, vector: list[float], language: str) -> list[KnowledgeItem]:
        repository = KnowledgeChunkRepository(session)
        candidates = await repository.semantic_candidates(vector, language, SEMANTIC_CANDIDATES)
        if language == "kk" and len(candidates) < MAX_RESULTS:
            fallback = await repository.semantic_candidates(vector, "ru", SEMANTIC_CANDIDATES)
            seen = {(item.chunk.source_type, item.chunk.source_id, item.chunk.chunk_index) for item in candidates}
            candidates.extend(item for item in fallback if (item.chunk.source_type, item.chunk.source_id, item.chunk.chunk_index) not in seen)
        return [
            KnowledgeItem(
                item.chunk.source_type, item.chunk.source_id, item.chunk.title, item.chunk.content,
                fallback=item.chunk.language_code != language, chunk_index=item.chunk.chunk_index,
                semantic_score=item.similarity,
            )
            for item in candidates
        ]

    async def _lexical_search(self, session: AsyncSession, query: str, language: str, limit: int) -> list[KnowledgeItem]:
        page_query = (
            select(BotPage)
            .where(BotPage.is_active.is_(True), BotPage.status == PageStatus.PUBLISHED, BotPage.published_revision_id.is_not(None))
            .options(selectinload(BotPage.published_revision).selectinload(BotPageRevision.translations))
        )
        pages = list((await session.scalars(page_query)).all())
        faq_query = select(FAQItem).where(FAQItem.is_active.is_(True)).options(selectinload(FAQItem.translations))
        faqs = list((await session.scalars(faq_query)).all())
        candidates: list[KnowledgeItem] = []
        for page in pages:
            revision = page.published_revision
            if not revision:
                continue
            value = translated(revision.translations, language)
            exact = value and str(getattr(value.language_code, "value", value.language_code)).lower() == language
            title, content = (value.title, value.text) if value else (revision.title, revision.text)
            candidates.append(KnowledgeItem("page", page.id, title, content, fallback=not bool(exact)))
        for faq in faqs:
            value = translated(faq.translations, language)
            exact = value and str(getattr(value.language_code, "value", value.language_code)).lower() == language
            title, content = (value.question, value.answer) if value else (faq.question, faq.answer)
            candidates.append(KnowledgeItem("faq", faq.id, title, content, fallback=not bool(exact)))

        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        for item in candidates:
            title_tokens = _tokens(item.title)
            content_tokens = _tokens(item.content)
            item.score = 3 * len(query_tokens & title_tokens) + len(query_tokens & content_tokens)
            combined_tokens = title_tokens | content_tokens
            item.score += len(query_tokens & combined_tokens)
            item.lexical_score = min(item.score / max(4, len(query_tokens) * 4), 1.0)
        return sorted((item for item in candidates if item.score > 0), key=lambda item: (-item.score, item.kind, item.entity_id))[:limit]

    def _merge(self, query: str, semantic: list[KnowledgeItem], lexical: list[KnowledgeItem], limit: int) -> list[KnowledgeItem]:
        lexical_by_source = {(item.kind, item.entity_id): item for item in lexical}
        query_normalized = normalize_for_match(query)
        merged: list[KnowledgeItem] = []
        for item in semantic:
            lexical_item = lexical_by_source.get((item.kind, item.entity_id))
            item.lexical_score = lexical_item.lexical_score if lexical_item else 0
            exact_boost = 0.08 if query_normalized and query_normalized in normalize_for_match(f"{item.title} {item.content}") else 0
            item.score = 0.75 * item.semantic_score + 0.25 * item.lexical_score + exact_boost
            if item.semantic_score >= self.min_semantic_score or item.lexical_score > 0:
                merged.append(item)
        represented = {(item.kind, item.entity_id) for item in merged}
        for item in lexical:
            if (item.kind, item.entity_id) not in represented:
                item.score = 0.25 * item.lexical_score
                merged.append(item)
        merged.sort(key=lambda item: (-item.score, item.kind, item.entity_id, item.chunk_index or 0))
        result: list[KnowledgeItem] = []
        per_source: dict[tuple[str, int], int] = {}
        for item in merged:
            key = item.kind, item.entity_id
            if per_source.get(key, 0) >= 2:
                continue
            result.append(item)
            per_source[key] = per_source.get(key, 0) + 1
            if len(result) >= limit:
                break
        return result

    def build_context(self, items: list[KnowledgeItem]) -> str:
        blocks: list[str] = []
        size = 0
        for index, item in enumerate(items, 1):
            content = item.content.strip()[:MAX_ITEM_CHARS]
            fallback = " [RU fallback]" if item.fallback else ""
            block = f"SOURCE {index} ({item.kind}){fallback}\nTITLE: {item.title}\nCONTENT:\n{content}"
            remaining = MAX_CONTEXT_CHARS - size
            if remaining <= 0:
                break
            blocks.append(block[:remaining])
            size += len(block) + 2
        return "\n\n".join(blocks)[:MAX_CONTEXT_CHARS]


def normalize_for_match(value: str) -> str:
    return " ".join(WORD_RE.findall(value.lower().replace("ё", "е")))
