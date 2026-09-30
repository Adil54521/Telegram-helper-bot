import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import BotPage, BotPageRevision, FAQItem
from app.database.models.enums import PageStatus
from app.utils.i18n import translated

MAX_RESULTS = 3
MAX_CONTEXT_CHARS = 6000
MAX_ITEM_CHARS = 1600
WORD_RE = re.compile(r"[\wа-яёәіңғүұқөһ]+", re.IGNORECASE)
STOP_WORDS = {"и", "в", "на", "по", "о", "об", "а", "как", "что", "какие", "для", "мен", "және", "туралы", "қандай", "қалай", "не", "бар"}


@dataclass(slots=True)
class KnowledgeItem:
    kind: str
    entity_id: int
    title: str
    content: str
    fallback: bool = False
    score: int = 0


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
    async def search(self, session: AsyncSession, query: str, language: str, limit: int = MAX_RESULTS) -> list[KnowledgeItem]:
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
        return sorted((item for item in candidates if item.score > 0), key=lambda item: (-item.score, item.kind, item.entity_id))[:limit]

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
