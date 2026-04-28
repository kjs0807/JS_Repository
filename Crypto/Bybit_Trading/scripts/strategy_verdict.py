"""Generic strategy verdict report generator.

Usage:
    python scripts/strategy_verdict.py BBKCSqueeze

읽는 파일: logs/research/<strategy_snake>/{fine_best,walkforward,overfit}.json
출력: docs/superpowers/strategies/<strategy_snake>_verdict.md + _params.json
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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


def snake_case(name: str) -> str:
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def judge(fine, wf, ov):
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
    if ov and ov["verdict"] not in CRITERIA["allowed_overfit"]:
        checks.append(f"Overfit {ov['verdict']}")
        passed = False
    return {"passed": passed, "issues": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", help="Strategy name (CamelCase)")
    args = parser.parse_args()

    snake = snake_case(args.strategy)
    output_dir = PROJECT_ROOT / "logs" / "research" / snake
    doc_dir = PROJECT_ROOT.parent.parent / "docs" / "superpowers" / "strategies"

    fine_file = output_dir / "fine_best.json"
    if not fine_file.exists():
        print(f"ERROR: {fine_file} not found")
        sys.exit(1)

    fine_best = json.load(open(fine_file))
    wf_data = json.load(open(output_dir / "walkforward.json")) if (output_dir / "walkforward.json").exists() else []
    ov_data = json.load(open(output_dir / "overfit.json")) if (output_dir / "overfit.json").exists() else []

    wf_map = {(r["variant"], r["symbol"], r["tf"]): r for r in wf_data}
    ov_map = {(r["variant"], r["symbol"], r["tf"]): r for r in ov_data}

    survivors, eliminated = [], []
    for fine in fine_best:
        if "error" in fine: continue
        key = (fine["variant"], fine["symbol"], fine["tf"])
        wf = wf_map.get(key)
        ov = ov_map.get(key)
        verdict = judge(fine, wf, ov)
        entry = {**fine, "wf": wf, "overfit": ov, "verdict": verdict}
        if verdict["passed"]:
            survivors.append(entry)
        else:
            eliminated.append(entry)

    lines = [
        f"# {args.strategy} — 최종 판정 리포트",
        "",
        f"**검증 조합**: {len(fine_best)} 개 ({len(survivors)} 통과 / {len(eliminated)} 탈락)",
        "",
        "## 통과 기준",
        "",
    ]
    for k, v in CRITERIA.items():
        lines.append(f"- {k}: {v}")
    lines.extend(["", "## 생존자", ""])
    if survivors:
        lines.append("| Symbol | TF | Sharpe | PF | MDD | 거래수 | OOS ret |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in survivors:
            wf = s["wf"]
            oos_ret = f"{wf['avg_oos_retention']:.1%}" if wf else "N/A"
            lines.append(
                f"| {s['symbol']} | {s['tf']} | {s['sharpe']:.3f} | "
                f"{s['profit_factor']:.2f} | {s['max_dd']:.2%} | "
                f"{s['trades']} | {oos_ret} |"
            )
    else:
        lines.append("*생존자 없음*")

    lines.extend(["", "## 탈락", "", "| Symbol | TF | 사유 |", "|---|---|---|"])
    for e in eliminated:
        issues = "; ".join(e["verdict"]["issues"])
        lines.append(f"| {e['symbol']} | {e['tf']} | {issues} |")

    doc_dir.mkdir(parents=True, exist_ok=True)
    verdict_file = doc_dir / f"{snake}_verdict.md"
    verdict_file.write_text("\n".join(lines), encoding="utf-8")

    params_file = doc_dir / f"{snake}_params.json"
    with open(params_file, "w") as f:
        json.dump({"survivors": survivors, "eliminated_count": len(eliminated)}, f, indent=2)

    print(f"생존자: {len(survivors)}")
    print(f"탈락: {len(eliminated)}")
    print(f"리포트: {verdict_file}")


if __name__ == "__main__":
    main()
