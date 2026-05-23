"""Render the Beda modes grid summary as a compact HTML report."""

from __future__ import annotations

import csv
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "runs" / "beda_modes_grid" / "grid_summary.csv"
OUTPUT = ROOT / "runs" / "beda_modes_grid" / "index.html"


def pct(value: str) -> str:
    return f"{float(value) * 100:.2f}%"


def num(value: str) -> str:
    return f"{float(value):.2f}"


def cls(value: str) -> str:
    return "pos" if float(value) > 0 else "neg"


def main() -> int:
    rows = list(csv.DictReader(SUMMARY.open(encoding="utf-8")))
    rows.sort(key=lambda r: (float(r["total_return"]), float(r["sharpe"])), reverse=True)
    best_by_mode: dict[str, dict[str, str]] = {}
    for row in rows:
        best_by_mode.setdefault(row["mode"], row)

    def table(items: list[dict[str, str]]) -> str:
        body = []
        for row in items:
            body.append(
                "<tr>"
                f"<td>{html.escape(row['candidate'])}</td>"
                f"<td>{html.escape(row['mode'])}</td>"
                f"<td>{html.escape(row['tf'])}</td>"
                f"<td class='{cls(row['total_return'])}'>{pct(row['total_return'])}</td>"
                f"<td class='{cls(row['sharpe'])}'>{num(row['sharpe'])}</td>"
                f"<td>{pct(row['max_drawdown'])}</td>"
                f"<td>{html.escape(row['fills'])}</td>"
                f"<td><code>{html.escape(Path(row['run_dir']).name)}</code></td>"
                "</tr>"
            )
        return "\n".join(body)

    doc = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Beda Modes Grid</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    h1, h2 {{ margin-bottom: 8px; }}
    p {{ color: #566573; }}
    table {{ border-collapse: collapse; width: 100%; margin: 18px 0 34px; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e5e8e8; padding: 9px 10px; text-align: right; }}
    th:nth-child(2), td:nth-child(2), th:nth-child(8), td:nth-child(8) {{ text-align: left; }}
    th {{ background: #f8f9f9; color: #2c3e50; position: sticky; top: 0; }}
    .neg {{ color: #b03a2e; font-weight: 650; }}
    .pos {{ color: #117a65; font-weight: 650; }}
    code {{ background: #f4f6f7; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Beda Band + Bollinger Modes Grid</h1>
  <p>30 candidates, 1m / 5m where applicable, 2026-01-01 to 2026-05-07. Winner condition: return &gt; 0 and Sharpe &gt; 0.</p>
  <h2>Top 10 Runs</h2>
  <table>
    <thead><tr><th>#</th><th>Mode</th><th>TF</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Fills</th><th>Run</th></tr></thead>
    <tbody>{table(rows[:10])}</tbody>
  </table>
  <h2>Best By Mode</h2>
  <table>
    <thead><tr><th>#</th><th>Mode</th><th>TF</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Fills</th><th>Run</th></tr></thead>
    <tbody>{table(list(best_by_mode.values()))}</tbody>
  </table>
  <h2>All Runs</h2>
  <table>
    <thead><tr><th>#</th><th>Mode</th><th>TF</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Fills</th><th>Run</th></tr></thead>
    <tbody>{table(rows)}</tbody>
  </table>
</body>
</html>
"""
    OUTPUT.write_text(doc, encoding="utf-8")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
