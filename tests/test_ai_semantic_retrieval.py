import unittest
from types import SimpleNamespace

from app.ai.index import changed_specs, make_specs, normalize_text, split_chunks
from app.ai.knowledge import KnowledgeItem, KnowledgeService
from app.ai.provider import GeminiProvider
from app.database.models import AIKnowledgeChunk


class ChunkingTests(unittest.TestCase):
    def test_chunking_is_deterministic_and_overlapping(self) -> None:
        text = "Первый абзац. " * 80
        first = split_chunks(text, size=180, overlap=30)
        second = split_chunks(text, size=180, overlap=30)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 1)
        self.assertTrue(all(len(item) <= 180 for item in first))

    def test_normalization_and_hash_are_stable(self) -> None:
        self.assertEqual(normalize_text(" A   B\r\n\n C "), "A B\nC")
        left = make_specs("page", 1, 2, "ru", "Title", " Text  value ")
        right = make_specs("page", 1, 2, "ru", "Title", "Text value")
        self.assertEqual(left[0].content_hash, right[0].content_hash)

    def test_unchanged_chunk_is_not_scheduled_for_embedding(self) -> None:
        spec = make_specs("faq", 4, None, "ru", "Вопрос", "Ответ")[0]
        stored = AIKnowledgeChunk(
            source_type=spec.source_type, source_id=spec.source_id, revision_id=None,
            language_code=spec.language_code, chunk_index=spec.chunk_index,
            title=spec.title, content=spec.content, content_hash=spec.content_hash,
            embedding_model="gemini-embedding-2", embedding=[0.0] * 768,
        )
        self.assertEqual(changed_specs([spec], [stored], "gemini-embedding-2"), [])


class GeminiEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_embedding_response_is_validated(self) -> None:
        class Models:
            async def embed_content(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])

        models = Models()
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.embedding_model = "test-embedding"
        provider.embedding_dimension = 2
        provider.client = SimpleNamespace(aio=SimpleNamespace(models=models))
        result = await provider.embed_query("приёмная комиссия")
        self.assertEqual(result, [0.1, 0.2])
        self.assertEqual(models.kwargs["model"], "test-embedding")


class HybridRankingTests(unittest.TestCase):
    def test_hybrid_ranking_deduplicates_and_caps_source(self) -> None:
        service = KnowledgeService(semantic_enabled=True)
        semantic = [
            KnowledgeItem("page", 1, "Магистратура", "Условия поступления", semantic_score=0.91, chunk_index=0),
            KnowledgeItem("page", 1, "Магистратура", "Стоимость обучения", semantic_score=0.88, chunk_index=1),
            KnowledgeItem("page", 1, "Магистратура", "Контакты", semantic_score=0.86, chunk_index=2),
            KnowledgeItem("faq", 2, "Как поступить", "Подайте документы", semantic_score=0.80, chunk_index=0),
        ]
        lexical = [KnowledgeItem("faq", 2, "Как поступить", "Подайте документы", lexical_score=1.0)]
        result = service._merge("как поступить", semantic, lexical, 5)
        self.assertEqual(result[0].kind, "faq")
        self.assertEqual(sum(item.entity_id == 1 for item in result), 2)

    def test_low_semantic_noise_is_removed(self) -> None:
        service = KnowledgeService(semantic_enabled=True, min_semantic_score=0.6)
        result = service._merge(
            "общежитие",
            [KnowledgeItem("page", 1, "Спорт", "Секции", semantic_score=0.2)],
            [],
            3,
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
