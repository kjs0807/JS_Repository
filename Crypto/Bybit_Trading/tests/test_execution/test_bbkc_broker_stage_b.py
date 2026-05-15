"""Stage B-1 / B-2 / B-3: BbkcBroker leverage + weights + kill switch.

The tests bypass ``BbkcBroker.__init__`` (which calls REST during
``_fetch_instrument_specs``) and wire up attributes manually so we can
test the leverage / sizing / kill-switch logic in isolation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.bbkc_demo_broker import BbkcBroker
from src.runtime.kill_switch import KillSwitch, FLAG_FILENAME


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_broker(
    *,
    leverage: int = 3,
    risk: RiskConfig | None = None,
    per_sym: dict | None = None,
    kill_switch=None,
) -> BbkcBroker:
    """Hand-rolled broker that skips REST calls during construction."""
    broker = BbkcBroker.__new__(BbkcBroker)
    broker._rest = MagicMock()
    broker._alert = None
    broker._risk = MagicMock()
    broker._risk.config = risk or RiskConfig(max_position_pct=0.05)
    broker._leverage = leverage
    broker._initial_capital = 50_000.0
    broker._positions = {}
    broker._equity = 50_000.0
    broker._run_dir = Path(".")
    broker._orders_path = Path("/dev/null")
    broker._symbols_allowed = {"BTCUSDT", "ETHUSDT"}
    broker._qty_step = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._min_qty = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._per_symbol_max_pos_pct = dict(per_sym) if per_sym else {}
    broker._kill_switch = kill_switch
    return broker


# ---------------------------------------------------------------------------
# B-2: per-symbol weights
# ---------------------------------------------------------------------------
class TestPerSymbolWeights:
    def test_default_uniform_falls_back_to_risk_max_position_pct(self):
        broker = _make_broker(risk=RiskConfig(max_position_pct=0.05))
        # 50000 * 0.05 * 3 / 80000 = 0.09375 -> rounded down to 0.093
        qty = broker.calc_legacy_notional_qty("BTCUSDT", 80_000.0)
        assert pytest.approx(qty, rel=1e-3) == 0.093

    def test_per_symbol_override_wins(self):
        broker = _make_broker(per_sym={"BTCUSDT": 0.10, "ETHUSDT": 0.30})
        # BTC: 50000 * 0.10 * 3 / 80000 = 0.1875 -> 0.187
        qty_btc = broker.calc_legacy_notional_qty("BTCUSDT", 80_000.0)
        assert pytest.approx(qty_btc, rel=1e-3) == 0.187
        # ETH: 50000 * 0.30 * 3 / 2500 = 18.0 -> 18.0
        qty_eth = broker.calc_legacy_notional_qty("ETHUSDT", 2_500.0)
        assert pytest.approx(qty_eth, rel=1e-3) == 18.0

    def test_symbol_without_entry_falls_back_to_risk(self):
        # ETH has a per-symbol entry; BTC does not -> BTC uses risk.
        broker = _make_broker(
            risk=RiskConfig(max_position_pct=0.05),
            per_sym={"ETHUSDT": 0.30},
        )
        qty_btc = broker.calc_legacy_notional_qty("BTCUSDT", 80_000.0)
        # 50000 * 0.05 * 3 / 80000 = 0.09375 -> 0.093
        assert pytest.approx(qty_btc, rel=1e-3) == 0.093

    def test_entry_price_zero_returns_zero(self):
        broker = _make_broker(per_sym={"BTCUSDT": 0.10})
        assert broker.calc_legacy_notional_qty("BTCUSDT", 0.0) == 0.0


# ---------------------------------------------------------------------------
# B-3: kill switch integration
# ---------------------------------------------------------------------------
class TestKillSwitchIntegration:
    def test_no_kill_switch_means_orders_proceed(self, monkeypatch):
        # baseline: super().buy gets called when no kill switch.
        broker = _make_broker(kill_switch=None)
        called = []
        # Replace LiveBroker.buy via the MRO so we can observe the pass-through.
        from src.execution.live_broker import LiveBroker
        monkeypatch.setattr(
            LiveBroker, "buy",
            lambda self, *a, **kw: called.append(("buy", a, kw)) or "OID1",
        )
        broker._log_order = lambda *a, **kw: None  # silence audit IO
        oid = broker.buy("BTCUSDT", 0.01, stop_loss=75_000.0)
        assert oid == "OID1"
        assert called and called[0][0] == "buy"

    def test_kill_switch_engaged_blocks_buy(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        (run_dir / FLAG_FILENAME).touch()
        ks = KillSwitch(run_dir=run_dir)
        broker = _make_broker(kill_switch=ks)
        from src.execution.live_broker import LiveBroker
        called = []
        monkeypatch.setattr(
            LiveBroker, "buy",
            lambda self, *a, **kw: called.append("SHOULD-NOT-RUN") or "X",
        )
        broker._log_order = lambda *a, **kw: None
        assert broker.buy("BTCUSDT", 0.01, stop_loss=75_000.0) == ""
        assert called == []

    def test_kill_switch_engaged_blocks_sell(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        (run_dir / FLAG_FILENAME).touch()
        ks = KillSwitch(run_dir=run_dir)
        broker = _make_broker(kill_switch=ks)
        from src.execution.live_broker import LiveBroker
        called = []
        monkeypatch.setattr(
            LiveBroker, "sell",
            lambda self, *a, **kw: called.append("SHOULD-NOT-RUN") or "X",
        )
        broker._log_order = lambda *a, **kw: None
        assert broker.sell("BTCUSDT", 0.01, stop_loss=82_000.0) == ""
        assert called == []

    def test_kill_switch_does_not_affect_close(self, monkeypatch, tmp_path):
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        (run_dir / FLAG_FILENAME).touch()
        ks = KillSwitch(run_dir=run_dir)
        broker = _make_broker(kill_switch=ks)
        from src.execution.live_broker import LiveBroker
        called = []
        monkeypatch.setattr(
            LiveBroker, "close",
            lambda self, sym, reason="": called.append((sym, reason)) or "CLOSE_OID",
        )
        broker._log_order = lambda *a, **kw: None
        # Existing positions must still be closeable while new entries are blocked.
        oid = broker.close("BTCUSDT", reason="trail")
        assert oid == "CLOSE_OID"
        assert called == [("BTCUSDT", "trail")]

    def test_kill_switch_does_not_affect_update_stop(self, monkeypatch, tmp_path):
        """update_stop comes from LiveBroker and isn't gated. The kill
        switch must NOT silently break trail/BE management of open positions."""
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        (run_dir / FLAG_FILENAME).touch()
        ks = KillSwitch(run_dir=run_dir)
        broker = _make_broker(kill_switch=ks)
        # Spot-check: the broker class does NOT override update_stop, and
        # _check_kill_switch is not in its call path.
        assert "update_stop" not in BbkcBroker.__dict__


# ---------------------------------------------------------------------------
# B-1: ensure_leverage_set
# ---------------------------------------------------------------------------
class TestEnsureLeverageSet:
    def test_success_path(self):
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.return_value = True
        broker._rest.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionIdx": 1, "leverage": "3", "size": "0"},
            {"symbol": "BTCUSDT", "positionIdx": 2, "leverage": "3", "size": "0"},
            {"symbol": "ETHUSDT", "positionIdx": 1, "leverage": "3", "size": "0"},
            {"symbol": "ETHUSDT", "positionIdx": 2, "leverage": "3", "size": "0"},
        ]
        broker.ensure_leverage_set(["BTCUSDT", "ETHUSDT"])
        # both symbols got a set_leverage call:
        sent = [
            (a[0], a[1]) for a, _ in
            [c[0:2] for c in broker._rest.set_leverage.call_args_list]
        ]
        assert sent == [("BTCUSDT", 3), ("ETHUSDT", 3)]

    def test_set_leverage_raises_proceeds_to_readback_and_passes(self):
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.side_effect = RuntimeError("leverage not modified")
        # The set call raised, but read-back says we are already at 3x
        # -> we should accept silently (idempotent re-arm).
        broker._rest.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionIdx": 1, "leverage": "3", "size": "0"},
            {"symbol": "BTCUSDT", "positionIdx": 2, "leverage": "3", "size": "0"},
        ]
        broker.ensure_leverage_set(["BTCUSDT"])  # must not raise

    def test_mismatch_raises_runtime_error(self):
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.return_value = False
        broker._rest.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionIdx": 1, "leverage": "10", "size": "0"},
            {"symbol": "BTCUSDT", "positionIdx": 2, "leverage": "10", "size": "0"},
        ]
        with pytest.raises(RuntimeError) as excinfo:
            broker.ensure_leverage_set(["BTCUSDT"])
        assert "leverage mismatch" in str(excinfo.value).lower()

    def test_missing_position_row_raises(self):
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.return_value = True
        broker._rest.get_positions.return_value = []   # nothing returned
        with pytest.raises(RuntimeError) as excinfo:
            broker.ensure_leverage_set(["BTCUSDT"])
        assert "no position row" in str(excinfo.value).lower()

    def test_unparseable_leverage_field_raises(self):
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.return_value = True
        broker._rest.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionIdx": 1, "leverage": "", "size": "0"},
        ]
        with pytest.raises(RuntimeError) as excinfo:
            broker.ensure_leverage_set(["BTCUSDT"])
        assert "unparseable" in str(excinfo.value).lower()

    def test_decimal_leverage_string_accepted(self):
        """Bybit can return '3.00' instead of '3' - must still match."""
        broker = _make_broker(leverage=3)
        broker._rest.set_leverage.return_value = True
        broker._rest.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionIdx": 1, "leverage": "3.00", "size": "0"},
            {"symbol": "BTCUSDT", "positionIdx": 2, "leverage": "3.00", "size": "0"},
        ]
        broker.ensure_leverage_set(["BTCUSDT"])   # must not raise

    def test_empty_symbol_list_is_noop(self):
        broker = _make_broker(leverage=3)
        broker.ensure_leverage_set([])
        broker._rest.set_leverage.assert_not_called()
        broker._rest.get_positions.assert_not_called()
