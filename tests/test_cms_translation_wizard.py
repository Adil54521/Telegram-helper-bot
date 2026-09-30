import unittest

from app.ai.service import AIService, FAQTranslation
from app.ai.provider import TokenUsage
from app.utils.wizard import finish_wizard


class FakeState:
    def __init__(self):
        self.data = {"wizard_chat_id": 1, "wizard_message_id": 2, "faq_question": "IT <2026> & KUAM", "unrelated": "keep"}
        self.state = "faq"

    async def get_data(self): return dict(self.data)
    async def set_data(self, value): self.data = value
    async def set_state(self, value): self.state = value


class StructuredProvider:
    def __init__(self): self.calls = 0
    async def generate_structured(self, **kwargs):
        self.calls += 1
        return [FAQTranslation(index=0, question_kk="Сұрақ", answer_kk="Жауап")], TokenUsage(10, 5, 15)
    async def close(self): pass


class FakeSession:
    def __init__(self): self.items = []
    def add(self, item): self.items.append(item)
    async def flush(self): pass


class CMSWizardTests(unittest.IsolatedAsyncioTestCase):
    async def test_finish_removes_only_wizard_data(self):
        state = FakeState()
        await finish_wizard(state, "faq_question")
        self.assertEqual(state.data, {"unrelated": "keep"})
        self.assertIsNone(state.state)

    async def test_single_faq_translation_uses_one_call(self):
        provider = StructuredProvider(); service = AIService(provider, 0)
        result = await service.translate_faq_batch(FakeSession(), user_id=1, rows=[{"question_ru": "Вопрос", "answer_ru": "Ответ"}])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result[0].question_kk, "Сұрақ")
