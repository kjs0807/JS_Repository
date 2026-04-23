"""RobustExplorer 단위 테스트."""
import json
import os
import pytest
from pathlib import Path
from src.backtester.robust_explorer import (
    RobustExplorer,
    combo_key,
    params_key,
    append_jsonl,
    load_existing_jsonl,
)


class TestKeyHelpers:
    def test_params_key_stable(self):
        """동일 dict → 동일 key."""
        p1 = {"entry_period": 20, "stop_atr": 2.0}
        p2 = {"stop_atr": 2.0, "entry_period": 20}
        assert params_key(p1) == params_key(p2)

    def test_combo_key_unique(self):
        k1 = combo_key("B", "BTCUSDT", "1h", {"x": 1})
        k2 = combo_key("B", "BTCUSDT", "1h", {"x": 2})
        k3 = combo_key("B", "ETHUSDT", "1h", {"x": 1})
        assert k1 != k2
        assert k1 != k3
        assert k2 != k3


class TestJsonlPersistence:
    def test_append_and_load(self, tmp_path):
        f = tmp_path / "results.jsonl"
        append_jsonl(f, {"variant": "B", "symbol": "BTC", "tf": "1h",
                          "params": {"x": 1}, "sharpe": 1.0})
        append_jsonl(f, {"variant": "B", "symbol": "BTC", "tf": "1h",
                          "params": {"x": 2}, "sharpe": 2.0})
        results, done_keys = load_existing_jsonl(f)
        assert len(results) == 2
        assert len(done_keys) == 2

    def test_load_empty_file(self, tmp_path):
        f = tmp_path / "missing.jsonl"
        results, done_keys = load_existing_jsonl(f)
        assert results == []
        assert done_keys == set()


class TestRobustExplorerBasics:
    def test_create_explorer(self, tmp_path):
        explorer = RobustExplorer(
            name="test",
            output_dir=tmp_path,
        )
        assert explorer.name == "test"
        assert explorer.output_dir == tmp_path
        assert explorer.stop_requested is False

    def test_jsonl_file_paths(self, tmp_path):
        explorer = RobustExplorer(name="test", output_dir=tmp_path)
        assert explorer.jsonl_path("coarse") == tmp_path / "coarse_results.jsonl"
        assert explorer.jsonl_path("fine") == tmp_path / "fine_results.jsonl"

    def test_request_stop(self, tmp_path):
        explorer = RobustExplorer(name="test", output_dir=tmp_path)
        explorer.request_stop()
        assert explorer.stop_requested is True
