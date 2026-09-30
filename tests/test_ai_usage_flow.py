from decimal import Decimal
import unittest

from app.ai.provider import AIProviderResponse, TokenUsage
from app.ai.service import AIService


class FakeSession:
    def __init__(self) -> None:
        self.added = []

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


class EmptyKnowledge:
    async def search(self, session, question: str, language: str):
        return []


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return AIProviderResponse("OK", TokenUsage(10, 3, 13))

    async def close(self) -> None:
        return None


class AIUsageFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_zero_records_usage_without_gemini(self) -> None:
        session = FakeSession()
        provider = FakeProvider()
        service = AIService(provider, 0, EmptyKnowledge())

        result = await service.answer(
            session,
            user_id=7,
            question="unknown",
            language="ru",
        )

        self.assertIsNone(result)
        self.assertEqual(provider.calls, 0)
        usage = session.added[0]
        self.assertEqual(usage.request_type, "USER")
        self.assertFalse(usage.gemini_called)
        self.assertEqual(usage.total_tokens, 0)
        self.assertTrue(usage.success)

    async def test_health_check_is_stored_separately(self) -> None:
        session = FakeSession()
        provider = FakeProvider()
        service = AIService(
            provider,
            0,
            EmptyKnowledge(),
            input_price_per_1m=Decimal("0.30"),
            output_price_per_1m=Decimal("2.50"),
        )

        await service.health_check(session, user_id=9)

        self.assertEqual(provider.calls, 1)
        usage = session.added[0]
        self.assertEqual(usage.request_type, "HEALTH_CHECK")
        self.assertTrue(usage.gemini_called)
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.output_tokens, 3)
