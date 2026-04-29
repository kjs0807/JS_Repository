"""bbkc_exit_eval.judge() baseline-relative delta rule tests."""
from scripts.bbkc_exit_eval import judge


def _base(wf=4, r=0.05, dd=0.10, n=100):
    return {"wf_oos_positive": wf, "wf_total": 9, "mean_r_per_trade": r,
            "max_dd": dd, "trade_count": n, "mean_oos_pnl": 200.0}


def _summary(base, **cells):
    """summary[cell_id][symbol] -> metrics dict."""
    out = {"F0": {"BTCUSDT": base}}
    for cid, syms in cells.items():
        out[cid] = syms
    return out


def test_F0_returns_BASELINE():
    base = _base()
    s = _summary(base)
    judged = judge(s)
    assert judged["F0"]["BTCUSDT"]["verdict"] == "BASELINE"
    assert judged["F0"]["BTCUSDT"]["warning"] is False


def test_no_F0_baseline_returns_UNKNOWN():
    """If --cell skipped F0, non-F0 cells lose their reference -> UNKNOWN."""
    s = {"TF_default": {"BTCUSDT": _base(wf=5, r=0.10, dd=0.08)}}
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "UNKNOWN"


def test_strong_promote_pos_plus_2_r_geq_dd_leq():
    base = _base(wf=4, r=0.05, dd=0.10, n=100)
    cell = _base(wf=6, r=0.10, dd=0.08, n=100)
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "STRONG_PROMOTE"


def test_promote_pos_plus_1_r_geq():
    base = _base(wf=4, r=0.05, dd=0.10)
    cell = _base(wf=5, r=0.06, dd=0.12)   # DD worse → only PROMOTE not STRONG
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "PROMOTE"


def test_neutral_within_thresholds():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=4, r=0.06)   # |Δwf|=0, |Δr|=0.01
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "NEUTRAL"


def test_kill_pos_minus_2():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=2, r=0.05)
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "KILL"


def test_kill_r_drop_more_than_threshold():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=4, r=-0.10)   # Δr = -0.15 < -0.05
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "KILL"


def test_warning_flag_when_trade_count_below_half_baseline():
    base = _base(wf=4, r=0.05, n=100)
    cell = _base(wf=5, r=0.06, n=40)   # 40 < 100 × 0.5
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["warning"] is True
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "PROMOTE"


# ── integrate_label: per-cell roll-up tests (round 4 §6.2) ────────────────


from scripts.bbkc_exit_eval import integrate_label


def _per_sym(verdict: str, warning: bool = False) -> dict:
    return {"verdict": verdict, "warning": warning}


def test_integrate_label_F0_returns_BASELINE():
    by_sym = {
        "ETHUSDT": _per_sym("BASELINE"),
        "BTCUSDT": _per_sym("BASELINE"),
        "AVAXUSDT": _per_sym("BASELINE"),
    }
    assert integrate_label("F0", by_sym) == "BASELINE"


def test_integrate_label_eth_warning_routes_to_MIXED():
    """ETH PROMOTE with warning=True must NOT reach ROBUST_PROMOTE."""
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE", warning=True),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_PROMOTE_MIXED"


def test_integrate_label_eth_only_promote_when_one_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_ONLY_PROMOTE"


def test_integrate_label_eth_only_promote_when_both_others_KILL():
    """Both BTC and AVAX KILL → still ETH_ONLY_PROMOTE (not DAMAGING since ETH gains)."""
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("KILL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_ONLY_PROMOTE"


def test_integrate_label_eth_promote_mixed_when_other_warning_no_kill():
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("NEUTRAL", warning=True),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_PROMOTE_MIXED"


def test_integrate_label_robust_promote_when_all_safe():
    by_sym = {
        "ETHUSDT": _per_sym("STRONG_PROMOTE"),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("BASELINE"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ROBUST_PROMOTE"


def test_integrate_label_damaging_when_eth_no_promote_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("NEUTRAL"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "DAMAGING"


def test_integrate_label_no_signal_when_eth_no_promote_no_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("NEUTRAL"),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "NO_SIGNAL"
