"""Tests for src/ml/types.py."""
from src.ml.types import MTFData, PatternEvent, LabelConfig
from src.core.types import BarSeries


def _empty_series(symbol="BTCUSDT", tf="1h") -> BarSeries:
    return BarSeries(symbol=symbol, timeframe=tf, bars=[])


class TestMTFData:
    def test_construction(self):
        primary = _empty_series("BTCUSDT", "1h")
        h4 = _empty_series("BTCUSDT", "4h")
        d1 = _empty_series("BTCUSDT", "1d")
        mtf = MTFData(
            symbol="BTCUSDT",
            primary_tf="1h",
            series={"1h": primary, "4h": h4, "1d": d1},
        )
        assert mtf.symbol == "BTCUSDT"
        assert mtf.primary_tf == "1h"
        assert mtf.get_primary() is primary
        assert mtf.series["4h"] is h4

    def test_frozen(self):
        mtf = MTFData(
            symbol="BTCUSDT",
            primary_tf="1h",
            series={"1h": _empty_series()},
        )
        try:
            mtf.symbol = "ETHUSDT"  # type: ignore
            assert False, "MTFData should be frozen"
        except Exception:
            pass


class TestPatternEvent:
    def test_construction(self):
        ev = PatternEvent(
            timestamp_ms=1700000000000,
            bar_index=42,
            symbol="BTCUSDT",
            direction="long",
            metadata={"divergence_strength": 0.8},
        )
        assert ev.bar_index == 42
        assert ev.direction == "long"
        assert ev.metadata["divergence_strength"] == 0.8

    def test_hashable(self):
        ev = PatternEvent(
            timestamp_ms=1, bar_index=0, symbol="X",
            direction="long", metadata={},
        )
        s = {ev}
        assert ev in s


class TestLabelConfig:
    def test_defaults(self):
        cfg = LabelConfig(tp_pct=0.04, sl_pct=0.02, max_holding_bars=24)
        assert cfg.tp_pct == 0.04
        assert cfg.sl_pct == 0.02
        assert cfg.max_holding_bars == 24
        assert cfg.label_type == "triple_barrier_binary"
        assert cfg.timeout_class == "negative"
