"""Abstract pattern interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from src.ml.types import MTFData, PatternEvent


class BasePattern(ABC):
    """Base class for all ML patterns.

    Concrete patterns must define class attributes:
        name: unique identifier (used in artifact paths)
        version: semver string (recorded in meta.json)
        timeframes: list of TFs the pattern reads (e.g. ["1h", "4h", "1d"])
        direction: "long" | "short" | "both"
        warmup_bars: minimum primary-TF bars before detect_at can fire

    And must implement:
        detect_at(mtf, i) → Optional[PatternEvent]
        extract_features(event, mtf) → Dict[str, float]
    """

    name: str
    version: str
    timeframes: List[str]
    direction: Literal["long", "short", "both"]
    warmup_bars: int

    @abstractmethod
    def detect_at(self, mtf: MTFData, i: int) -> Optional[PatternEvent]:
        """Return a PatternEvent if the trigger fires at primary index i, else None.

        MUST only access data at indices ≤ i. Higher-TF data MUST be accessed via
        src.ml.helpers.mtf_align.get_confirmed.

        Responsibility: detection only. Do not compute ML features here.
        """

    @abstractmethod
    def extract_features(self, event: PatternEvent, mtf: MTFData) -> Dict[str, float]:
        """Compute ML features at the event's bar.

        MUST only access data at indices ≤ event.bar_index.
        Responsibility: feature computation only. Do not re-run detection logic.
        """
