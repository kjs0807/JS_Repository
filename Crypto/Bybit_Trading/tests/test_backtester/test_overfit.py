"""backtester/overfit.py 단위 테스트."""
import pytest
import numpy as np
from src.backtester.overfit import OverfitDetector, OverfitVerdict

class TestOverfitDetector:
    def setup_method(self):
        self.detector = OverfitDetector()

    def test_permutation_test_significant(self):
        np.random.seed(42)
        pnl_list = [10.0 + np.random.randn() * 2 for _ in range(100)]
        p = self.detector.permutation_test(pnl_list, n_shuffles=500)
        assert p < 0.10

    def test_permutation_test_random_pnl(self):
        np.random.seed(42)
        pnl_list = [np.random.randn() * 10 for _ in range(100)]
        p = self.detector.permutation_test(pnl_list, n_shuffles=500)
        assert p > 0.01

    def test_param_sensitivity_stable(self):
        assert self.detector.param_sensitivity({"a=1": 1.0, "a=2": 0.98, "a=3": 0.95}) < 0.5

    def test_param_sensitivity_unstable(self):
        assert self.detector.param_sensitivity({"a=1": 2.0, "a=2": 0.1, "a=3": -1.0}) > 0.5

    def test_detect_returns_verdict(self):
        np.random.seed(42)
        pnl_list = [10.0 + np.random.randn() * 2 for _ in range(100)]
        verdict = self.detector.detect(pnl_list, {"a=1": 1.0, "a=2": 0.95, "a=3": 0.90}, n_shuffles=200)
        assert isinstance(verdict, OverfitVerdict)
        assert verdict.verdict in ("CLEAN", "WARNING", "OVERFIT")

    def test_verdict_has_details(self):
        np.random.seed(42)
        verdict = self.detector.detect([10.0+np.random.randn()*2 for _ in range(50)],
                                       {"a=1": 1.0, "a=2": 0.9}, n_shuffles=100)
        assert hasattr(verdict, "p_value") and hasattr(verdict, "sensitivity")
