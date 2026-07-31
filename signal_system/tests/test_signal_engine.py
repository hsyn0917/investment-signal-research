import math
import unittest

from signal_engine import (
    annualized_volatility,
    instruction_for_change,
    simple_moving_average,
    target_weight,
)


class SignalEngineTests(unittest.TestCase):
    def test_simple_moving_average_uses_only_history_through_end(self):
        values = [1.0, 2.0, 3.0, 100.0]
        self.assertEqual(simple_moving_average(values, end_index=2, window=3), 2.0)

    def test_risk_off_has_zero_weight(self):
        within, portfolio = target_weight(False, 0.50, 0.25, 0.50)
        self.assertEqual(within, 0.0)
        self.assertEqual(portfolio, 0.0)

    def test_target_weight_is_capped_at_sleeve_budget(self):
        within, portfolio = target_weight(True, 0.10, 0.25, 0.50)
        self.assertEqual(within, 1.0)
        self.assertEqual(portfolio, 0.50)

    def test_target_weight_scales_down_high_volatility(self):
        within, portfolio = target_weight(True, 0.50, 0.25, 0.50)
        self.assertAlmostEqual(within, 0.50)
        self.assertAlmostEqual(portfolio, 0.25)

    def test_instruction_transitions(self):
        self.assertEqual(instruction_for_change(0.20, 0.0, 0.5), "BUY")
        self.assertEqual(instruction_for_change(0.0, 0.20, 0.5), "EXIT_TO_CASH")
        self.assertEqual(instruction_for_change(0.25, 0.20, 0.5), "INCREASE")
        self.assertEqual(instruction_for_change(0.15, 0.20, 0.5), "REDUCE")
        self.assertEqual(instruction_for_change(0.202, 0.20, 0.5), "HOLD")

    def test_annualized_volatility_is_positive(self):
        prices = [100.0]
        for value in [0.01, -0.005, 0.008, -0.012, 0.006] * 4:
            prices.append(prices[-1] * (1.0 + value))
        vol = annualized_volatility(prices, end_index=20, window=20)
        self.assertTrue(math.isfinite(vol))
        self.assertGreater(vol, 0.01)


if __name__ == "__main__":
    unittest.main()
