import unittest
from pathlib import Path


class FAQUXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path("app/handlers/admin/faq.py").read_text(encoding="utf-8")

    def test_faq_flow_does_not_send_service_messages(self) -> None:
        self.assertNotIn("message.answer(", self.source)
        self.assertNotIn("m.answer(", self.source)
        self.assertNotIn("cb.message.answer(", self.source)

    def test_document_messages_remain_for_template_and_export(self) -> None:
        self.assertEqual(self.source.count("answer_document("), 2)

    def test_import_reuses_wizard_and_consumes_input(self) -> None:
        begin = self.source.index("async def begin_import")
        receive = self.source.index("async def receive_import")
        self.assertIn("remember_wizard", self.source[begin:receive])
        self.assertIn("consume_input(message)", self.source[receive:])

    def test_import_cancel_uses_targeted_finish(self) -> None:
        cancel = self.source.index("async def cancel_import")
        self.assertIn("finish_wizard(state,*IMPORT_KEYS)", self.source[cancel:cancel + 300])
