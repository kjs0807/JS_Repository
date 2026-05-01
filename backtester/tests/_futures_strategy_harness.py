"""Futures Strategy Harness (Phase 2.5 PR T).

``StrategyHarness`` (PR F) 의 5 contracts 위에 futures 특화 contracts 를 추가:
6. **Bracket lifecycle**: bracket 발행이 있으면 ORDER_ADDED payload 에 parent /
   oco_group_id 가 일관되게 보존된다.
7. **Reduce-only audit**: 청산 intent 중 reduce_only=True flag 가 EventLog 에 보존.
8. **Funding boundary deterministic**: funding_models 가 설정돼 있으면 SETTLE 이벤트
   ts 가 8h 정수배.
9. **Liquidation safety**: liquidation_price 가 설정된 포지션이라도 봉 OHLC 가 도달
   하지 않으면 LIQUIDATION 이벤트 미발생 (false positive 차단).

본 파일은 ``test_*`` 접두사가 아니므로 pytest 가 직접 수집하지 않는다 — 다른 테스트
파일이 ``FuturesStrategyHarness`` 를 import 해서 사용한다. PR T 에서 BBKC legacy
compat 가 첫 통과 사례.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from tests._strategy_harness import HarnessSpec, StrategyHarness


class FuturesStrategyHarness(StrategyHarness):
    """Crypto futures 전략 회귀 — 5 base + 4 futures contracts."""

    def __init__(self, spec: HarnessSpec) -> None:
        super().__init__(spec)

    # ---------- 6. Bracket lifecycle ---------------------------------------

    def assert_bracket_lifecycle_consistent(self) -> None:
        """ORDER_ADDED 의 parent_order_id / oco_group_id 가 일관되게 보존."""
        result = self._run("bracket")
        reader = EventLogReader(result.events_path)
        added = list(reader.by_type(EventType.ORDER_ADDED))
        seen_parents: set[str] = set()
        for e in added:
            payload = e.payload
            parent = payload.get("parent_order_id")
            oco = payload.get("oco_group_id")
            order_id = payload["order_id"]
            if parent is None:
                # 부모 entry — oco 도 None
                assert oco is None, f"entry order {order_id} has unexpected oco={oco}"
            else:
                # 자식 — oco_group_id 가 "oco_{parent}" 형식
                assert oco == f"oco_{parent}", (
                    f"child {order_id} has oco={oco} but expected oco_{parent}"
                )
                seen_parents.add(parent)

    # ---------- 7. Reduce-only audit ---------------------------------------

    def assert_reduce_only_intents_preserved(self) -> None:
        """전략이 reduce_only=True intent 를 발행하면 INTENT_CREATED payload 에 보존."""
        result = self._run("reduce_only")
        reader = EventLogReader(result.events_path)
        intents = list(reader.by_type(EventType.INTENT_CREATED))
        for e in intents:
            intent = e.payload["intent"]
            # 정상 직렬화: reduce_only key 가 dict 에 있어야 함
            assert "reduce_only" in intent, (
                f"INTENT_CREATED missing reduce_only key: {intent}"
            )

    # ---------- 8. Funding boundary deterministic --------------------------

    def assert_funding_boundary_deterministic(
        self,
        *,
        interval_hours: int = 8,
    ) -> None:
        """SETTLE 이벤트 ts 가 ``interval_hours`` 정수배 — funding boundary 정확."""
        result = self._run("funding")
        reader = EventLogReader(result.events_path)
        settles = list(reader.by_type(EventType.SETTLE))
        for e in settles:
            ts: datetime = e.ts
            assert ts.hour % interval_hours == 0
            assert ts.minute == 0
            assert ts.second == 0
            assert ts.microsecond == 0

    # ---------- 9. Liquidation safety --------------------------------------

    def assert_no_false_positive_liquidation(
        self,
        *,
        max_drawdown_pct: Decimal = Decimal("0.5"),
    ) -> None:
        """fixture 데이터의 가격 변동이 ``max_drawdown_pct`` 보다 작으면 LIQUIDATION
        이벤트 발생 안 해야 함 (false positive 차단).
        """
        result = self._run("liq_safety")
        reader = EventLogReader(result.events_path)
        liqs = list(reader.by_type(EventType.LIQUIDATION))
        # PR T BBKC fixture 는 max ~10% 변동 — leverage=3, mmr=0.005 면 liq ≈ 67% 하락
        # 필요. 따라서 LIQUIDATION 미발생.
        if liqs:
            # 원인 표시
            raise AssertionError(
                f"unexpected LIQUIDATION events for benign fixture: {len(liqs)}"
            )

    # ---------- 일괄 ------------------------------------------------------

    def assert_futures_all(self) -> None:
        """5 base + 4 futures contracts 모두."""
        self.assert_all()
        self.assert_bracket_lifecycle_consistent()
        self.assert_reduce_only_intents_preserved()
        # 8 / 9 는 fixture 가 funding_models / margin_model 을 가질 때만 의미 있음.
        # caller 가 명시적으로 호출하도록 둔다.
