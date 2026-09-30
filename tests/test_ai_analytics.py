from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.services.ai_analytics import AIAnalyticsPeriod, calculate_cost, period_start


class AIAnalyticsTests(unittest.TestCase):
    def test_calculate_cost_uses_decimal_rates(self) -> None:
        cost = calculate_cost(
            input_tokens=14_820,
            output_tokens=2_940,
            input_price_per_1m=Decimal("0.30"),
            output_price_per_1m=Decimal("2.50"),
        )

        self.assertEqual(cost.input_cost, Decimal("0.004446"))
        self.assertEqual(cost.output_cost, Decimal("0.007350"))
        self.assertEqual(cost.total_cost, Decimal("0.011796"))

    def test_period_start_today_is_utc_midnight(self) -> None:
        now = datetime(2026, 9, 30, 14, 45, tzinfo=timezone.utc)

        self.assertEqual(
            period_start(AIAnalyticsPeriod.TODAY, now),
            datetime(2026, 9, 30, tzinfo=timezone.utc),
        )
        self.assertIsNone(period_start(AIAnalyticsPeriod.ALL, now))
