"""Tests for BasePattern abstract interface."""
import pytest

from src.ml.patterns.base import BasePattern
from src.ml.types import MTFData, PatternEvent


def test_cannot_instantiate_base():
    with pytest.raises(TypeError):
        BasePattern()  # type: ignore


def test_concrete_must_implement_detect_at_and_extract_features():
    class IncompletePattern(BasePattern):
        name = "incomplete"
        version = "0.1.0"
        timeframes = ["1h"]
        direction = "long"
        warmup_bars = 10
        # missing detect_at and extract_features

    with pytest.raises(TypeError):
        IncompletePattern()  # type: ignore


def test_minimal_concrete_pattern():
    class TrivialPattern(BasePattern):
        name = "trivial"
        version = "0.1.0"
        timeframes = ["1h"]
        direction = "long"
        warmup_bars = 0

        def detect_at(self, mtf, i):
            if i == 5:
                return PatternEvent(
                    timestamp_ms=0, bar_index=i, symbol="X",
                    direction="long", metadata={},
                )
            return None

        def extract_features(self, event, mtf):
            return {"x": 1.0}

    p = TrivialPattern()
    assert p.name == "trivial"
    assert p.detect_at(mtf=None, i=4) is None  # type: ignore
    ev = p.detect_at(mtf=None, i=5)             # type: ignore
    assert ev is not None
    assert p.extract_features(ev, mtf=None) == {"x": 1.0}  # type: ignore
