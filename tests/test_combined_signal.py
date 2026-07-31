import json
import unittest
from datetime import date
from pathlib import Path

from generate_combined_signal import build_combined, classify


ROOT = Path(__file__).resolve().parents[1]


class CombinedSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.japan = json.loads(
            (ROOT / "signals/latest_signal.json").read_text(encoding="utf-8")
        )
        cls.us = json.loads(
            (ROOT / "signal_system/output/latest_signal.json").read_text(
                encoding="utf-8"
            )
        )
        cls.config = json.loads(
            (ROOT / "portfolio_config.json").read_text(encoding="utf-8")
        )

    def test_targets_sum_to_one(self):
        result = build_combined(
            self.japan, self.us, self.config, today=date(2026, 7, 31)
        )
        self.assertAlmostEqual(
            sum(row["target_weight"] for row in result["instructions"]), 1.0
        )

    def test_top_level_sleeves_are_applied(self):
        result = build_combined(
            self.japan, self.us, self.config, today=date(2026, 7, 31)
        )
        by_asset = {row["asset"]: row for row in result["instructions"]}
        self.assertAlmostEqual(
            by_asset["TQQQ"]["target_weight"],
            0.2 * self.us["target_weights"]["TQQQ"],
        )
        self.assertAlmostEqual(by_asset["JPY_CASH"]["target_weight"], 0.4)

    def test_stale_source_blocks_ok_status(self):
        result = build_combined(
            self.japan, self.us, self.config, today=date(2026, 8, 20)
        )
        self.assertEqual(result["status"], "STALE")
        self.assertTrue(result["warnings"])

    def test_action_threshold(self):
        self.assertEqual(classify(0.10, 0.101, 0.2), "維持")
        self.assertEqual(classify(0.10, 0.103, 0.2), "買い増し")


if __name__ == "__main__":
    unittest.main()
