from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AIKnowledgeChunk


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    chunk: AIKnowledgeChunk
    similarity: float


class KnowledgeChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def semantic_candidates(
        self,
        query_vector: list[float],
        language: str,
        limit: int,
    ) -> list[SemanticCandidate]:
        distance = AIKnowledgeChunk.embedding.cosine_distance(query_vector)
        result = await self.session.execute(
            select(AIKnowledgeChunk, (1 - distance).label("similarity"))
            .where(AIKnowledgeChunk.language_code == language)
            .order_by(distance)
            .limit(limit)
        )
        return [SemanticCandidate(chunk, float(similarity)) for chunk, similarity in result.tuples()]

