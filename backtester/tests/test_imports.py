"""PR 1 import 그린 검증 (spec §20 PR 1 완료 조건).

- `from backtester.core import ...` 모든 공개 심볼 import 가능
- BacktestError 계층이 정의되어 있다
- BacktestResult가 requested_run_id / resolved_run_id / run_dir 필드를 가진다
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def test_core_public_imports() -> None:
    """core 패키지의 공개 심볼이 모두 import 가능."""
    from backtester.core import (
        BacktestError,
        BacktestResult,
        BarPathModel,
        ClosePosition,
        ConfigError,
        DataError,
        ExecutionError,
        Fill,
        FullPosition,
        InstrumentError,
        MarketSnapshot,
        OrderIntent,
        RiskError,
        RunDirectoryError,
        ScaleIn,
        SizeSpec,
        TargetNotional,
        TargetUnits,
        TargetWeight,
    )

    # 가져온 심볼이 실제로 사용 가능한지 (mypy/ruff F401 회피 + 실제 형 확인)
    assert issubclass(ConfigError, BacktestError)
    assert issubclass(RunDirectoryError, BacktestError)
    assert issubclass(DataError, BacktestError)
    assert issubclass(InstrumentError, BacktestError)
    assert issubclass(RiskError, BacktestError)
    assert issubclass(ExecutionError, BacktestError)

    assert BarPathModel.PESSIMISTIC.value == "pessimistic"

    # SizeSpec union 멤버 모두 실체 존재
    assert TargetWeight is not None
    assert TargetNotional is not None
    assert TargetUnits is not None
    assert FullPosition is not None
    assert ClosePosition is not None
    assert ScaleIn is not None
    # SizeSpec은 union 타입 alias이므로 단순 truthiness 확인
    assert SizeSpec is not None
    # BacktestResult도 공개 API에 포함
    assert BacktestResult is not None

    # 인스턴스 생성도 가능해야 함
    fill = Fill(
        timestamp=datetime(2026, 1, 1),
        symbol="BTCUSDT",
        price=Decimal("50000"),
        size=Decimal("0.1"),
        side="buy",
        fee=Decimal("2.5"),
        fee_currency="USDT",
        order_id="ord_1",
        intent_reason="entry",
    )
    assert fill.symbol == "BTCUSDT"

    snap = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 1, 1),
        open=Decimal("50000"),
        high=Decimal("50100"),
        low=Decimal("49900"),
        close=Decimal("50050"),
        volume=Decimal("123.4"),
    )
    assert snap.mark_price is None  # Phase 1
    assert snap.funding_rate is None
    assert snap.open_interest is None

    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
    )
    assert intent.tif == "GTC"  # Phase 1 기본
    assert intent.expires_at is None


def test_backtest_error_hierarchy() -> None:
    """spec §12 예외 계층."""
    from backtester.core import (
        BacktestError,
        ConfigError,
        DataError,
        ExecutionError,
        InstrumentError,
        RiskError,
        RunDirectoryError,
    )

    # 6개 서브클래스 모두 BacktestError 상속
    for cls in (
        DataError,
        InstrumentError,
        RiskError,
        ExecutionError,
        RunDirectoryError,
        ConfigError,
    ):
        assert issubclass(cls, BacktestError)
        # 인스턴스화 가능
        e = cls("test")
        assert isinstance(e, BacktestError)
        assert str(e) == "test"


def test_backtest_result_required_fields() -> None:
    """spec §20 PR 1: BacktestResult에 requested_run_id, resolved_run_id, run_dir 필드 존재."""
    from backtester.core import BacktestResult

    field_names = {f.name for f in dataclasses.fields(BacktestResult)}
    assert "requested_run_id" in field_names
    assert "resolved_run_id" in field_names
    assert "run_dir" in field_names

    result = BacktestResult(
        requested_run_id="btc_test",
        resolved_run_id="btc_test_2",
        run_dir=Path("/tmp/runs/btc_test_2"),
        final_equity=Decimal("12345.67"),
        total_return=Decimal("0.2345"),
        num_fills=10,
        num_intents=12,
        config_path=Path("/tmp/runs/btc_test_2/config.json"),
        events_path=Path("/tmp/runs/btc_test_2/events.jsonl"),
    )
    # auto_suffix 시 requested != resolved
    assert result.requested_run_id != result.resolved_run_id
    assert result.run_dir == Path("/tmp/runs/btc_test_2")
