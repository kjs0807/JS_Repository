"""Donchian 전략 최종 판정 리포트 생성."""
from __future__ import annotations
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "logs" / "research" / "donchian"
DOC_DIR = Path("C:/Users/ceoji/Desktop/python_ibks") / "docs" / "superpowers" / "strategies"

CRITERIA = {
    "min_trades": 30,
    "min_profit_factor": 1.3,
    "min_win_rate": 0.35,
    "max_drawdown": 0.30,
    "min_sharpe": 0.8,
    "min_oos_retention": 0.50,
    "min_oos_positive_pct": 0.60,
    "allowed_overfit": ["CLEAN", "WARNING"],
}


def judge(fine, wf, overfit):
    checks = []
    passed = True
    if fine["trades"] < CRITERIA["min_trades"]:
        checks.append(f"거래수 부족 ({fine['trades']})")
        passed = False
    if fine["profit_factor"] < CRITERIA["min_profit_factor"]:
        checks.append(f"PF 부족 ({fine['profit_factor']:.2f})")
        passed = False
    if fine["win_rate"] < CRITERIA["min_win_rate"]:
        checks.append(f"승률 부족 ({fine['win_rate']:.1%})")
        passed = False
    if fine["max_dd"] > CRITERIA["max_drawdown"]:
        checks.append(f"MDD 초과 ({fine['max_dd']:.2%})")
        passed = False
    if fine["sharpe"] < CRITERIA["min_sharpe"]:
        checks.append(f"Sharpe 부족 ({fine['sharpe']:.3f})")
        passed = False
    if wf and wf["avg_oos_retention"] < CRITERIA["min_oos_retention"]:
        checks.append(f"OOS retention 부족 ({wf['avg_oos_retention']:.1%})")
        passed = False
    if wf and wf["oos_positive_pct"] < CRITERIA["min_oos_positive_pct"]:
        checks.append(f"OOS 양수비율 부족 ({wf['oos_positive_pct']:.1%})")
        passed = False
    if overfit and overfit["verdict"] not in CRITERIA["allowed_overfit"]:
        checks.append(f"Overfit {overfit['verdict']}")
        passed = False
    return {"passed": passed, "issues": checks}


def main():
    with open(OUTPUT_DIR / "fine_best.json") as f:
        fine_best = json.load(f)
    wf_data = json.load(open(OUTPUT_DIR / "walkforward.json")) if (OUTPUT_DIR / "walkforward.json").exists() else []
    overfit_data = json.load(open(OUTPUT_DIR / "overfit.json")) if (OUTPUT_DIR / "overfit.json").exists() else []

    wf_map = {(r["variant"], r["symbol"], r["tf"]): r for r in wf_data}
    ov_map = {(r["variant"], r["symbol"], r["tf"]): r for r in overfit_data}

    survivors = []
    eliminated = []
    for fine in fine_best:
        key = (fine["variant"], fine["symbol"], fine["tf"])
        wf = wf_map.get(key)
        ov = ov_map.get(key)
        verdict = judge(fine, wf, ov)
        entry = {**fine, "wf": wf, "overfit": ov, "verdict": verdict}
        if verdict["passed"]:
            survivors.append(entry)
        else:
            eliminated.append(entry)

    doc_lines = [
        "# Donchian Breakout — 최종 판정 리포트",
        "",
        f"**검증 일자**: 2026-04-11",
        f"**검증 조합**: {len(fine_best)} 개 ({len(survivors)} 통과 / {len(eliminated)} 탈락)",
        "",
        "## 통과 기준",
        "",
        f"- 거래 수 ≥ {CRITERIA['min_trades']}",
        f"- Profit Factor ≥ {CRITERIA['min_profit_factor']}",
        f"- 승률 ≥ {CRITERIA['min_win_rate']:.0%}",
        f"- MDD ≤ {CRITERIA['max_drawdown']:.0%}",
        f"- Sharpe ≥ {CRITERIA['min_sharpe']}",
        f"- OOS retention ≥ {CRITERIA['min_oos_retention']:.0%}",
        f"- OOS 양수 비율 ≥ {CRITERIA['min_oos_positive_pct']:.0%}",
        f"- Overfit != OVERFIT",
        "",
        "## 통과 전략 (생존자)",
        "",
    ]
    if survivors:
        doc_lines.append("| Variant | Symbol | TF | Sharpe | PF | MDD | 거래수 | OOS ret |")
        doc_lines.append("|---|---|---|---|---|---|---|---|")
        for s in survivors:
            wf = s["wf"]
            oos_ret = f"{wf['avg_oos_retention']:.1%}" if wf else "N/A"
            doc_lines.append(
                f"| {s['variant']} | {s['symbol']} | {s['tf']} | "
                f"{s['sharpe']:.3f} | {s['profit_factor']:.2f} | {s['max_dd']:.2%} | "
                f"{s['trades']} | {oos_ret} |"
            )
    else:
        doc_lines.append("*통과 전략 없음*")

    doc_lines.extend([
        "",
        "## 탈락 전략",
        "",
        "| Variant | Symbol | TF | 탈락 사유 |",
        "|---|---|---|---|",
    ])
    for e in eliminated:
        issues = "; ".join(e["verdict"]["issues"])
        doc_lines.append(f"| {e['variant']} | {e['symbol']} | {e['tf']} | {issues} |")

    DOC_DIR.mkdir(parents=True, exist_ok=True)
    verdict_file = DOC_DIR / "donchian_breakout_verdict.md"
    verdict_file.write_text("\n".join(doc_lines), encoding="utf-8")

    params_file = DOC_DIR / "donchian_breakout_params.json"
    with open(params_file, "w", encoding="utf-8") as f:
        json.dump({"survivors": survivors, "eliminated_count": len(eliminated)}, f, indent=2)

    print(f"생존자: {len(survivors)}")
    print(f"탈락: {len(eliminated)}")
    print(f"리포트: {verdict_file}")


if __name__ == "__main__":
    main()
