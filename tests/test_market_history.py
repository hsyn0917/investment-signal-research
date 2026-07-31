import math
import unittest
from datetime import date, datetime, timedelta

from generate_market_history import ALL_TICKERS, build_market_history
from signal_system.signal_engine import PricePoint


class MarketHistoryTests(unittest.TestCase):
    def setUp(self):
        start = date(2024, 1, 1)
        self.series = {}
        for ticker_index, ticker in enumerate(ALL_TICKERS):
            points = []
            for index in range(340):
                day = start + timedelta(days=index)
                base = 100 + ticker_index * 20
                value = base * (1 + index * 0.001) * (
                    1 + 0.03 * math.sin(index / 4)
                )
                points.append(PricePoint(day, value, value))
            self.series[ticker] = points
        self.config = {
            "target_volatility": 0.25,
            "volatility_window": 20,
            "trend_window": 200,
            "japan_sleeve": 0.8,
            "us_letf_sleeve": 0.2,
        }

    def test_history_is_normalized_and_bounded(self):
        payload = build_market_history(
            self.series,
            self.config,
            observations=90,
            generated_at=datetime(2025, 1, 1),
        )
        self.assertEqual(payload["observation_count"], 90)
        first = payload["points"][0]
        self.assertEqual(first["n225_index"], 100)
        self.assertEqual(first["sp500_index"], 100)
        for point in payload["points"]:
            self.assertGreaterEqual(point["risk_asset_weight"], 0)
            self.assertLessEqual(point["risk_asset_weight"], 1)

    def test_missing_series_is_rejected(self):
        del self.series["JPY=X"]
        with self.assertRaises(ValueError):
            build_market_history(self.series, self.config)


if __name__ == "__main__":
    unittest.main()
