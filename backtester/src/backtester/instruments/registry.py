"""InstrumentRegistry — symbol 기반 Instrument 조회.

Engine이 백테스트 시작 시 모든 instruments를 등록하고, Sizer/Risk/Execution이
필요할 때 symbol로 조회한다.
"""

from __future__ import annotations

from backtester.core.errors import InstrumentError
from backtester.instruments.base import Instrument


class InstrumentRegistry:
    """Symbol → Instrument 매핑 컨테이너."""

    def __init__(self) -> None:
        self._instruments: dict[str, Instrument] = {}

    def register(self, instrument: Instrument) -> None:
        """Instrument 등록. 중복 symbol 등록 시 InstrumentError raise."""
        if instrument.symbol in self._instruments:
            raise InstrumentError(
                f"Instrument already registered: {instrument.symbol!r}"
            )
        self._instruments[instrument.symbol] = instrument

    def get(self, symbol: str) -> Instrument:
        """등록되지 않은 symbol 조회 시 InstrumentError raise."""
        if symbol not in self._instruments:
            raise InstrumentError(
                f"Instrument not registered: {symbol!r}. "
                f"Registered: {sorted(self._instruments.keys())}"
            )
        return self._instruments[symbol]

    def has(self, symbol: str) -> bool:
        return symbol in self._instruments

    def all_symbols(self) -> list[str]:
        return sorted(self._instruments.keys())

    def __len__(self) -> int:
        return len(self._instruments)
