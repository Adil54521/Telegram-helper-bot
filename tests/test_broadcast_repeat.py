import unittest
from pathlib import Path


class BroadcastRepeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path("app/handlers/admin/broadcasts.py").read_text(encoding="utf-8")

    def test_repeat_only_populates_existing_fsm(self) -> None:
        start = self.source.index("async def repeat(")
        end = self.source.index("async def repeat_text", start)
        block = self.source[start:end]
        self.assertIn("source.text", block)
        self.assertIn("source.media_file_id", block)
        self.assertIn("CreateBroadcast.confirm", block)
        self.assertNotIn("Broadcast(", block)
        self.assertNotIn("run_broadcast", block)

    def test_repeat_recounts_recipients(self) -> None:
        start = self.source.index("async def repeat(")
        end = self.source.index("async def repeat_text", start)
        self.assertIn("recipient_count()", self.source[start:end])

    def test_final_confirm_uses_existing_send_flow(self) -> None:
        self.assertIn("run_broadcast(bot,session_factory,item.id", self.source)
        self.assertIn('metadata["source_broadcast_id"]', self.source)
