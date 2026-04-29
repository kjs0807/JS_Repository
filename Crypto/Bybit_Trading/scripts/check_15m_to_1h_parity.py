"""15m × 4 합성 1h vs Bybit direct 1h parity check (round 5 §6).

운영자가 1주 1회 수동 실행. 자동 fallback 안 함 — parity drift 발견 시
운영자가 수동으로 결정.

Usage:
    python -m scripts.check_15m_to_1h_parity --symbol BTCUSDT --bars 24
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.core.config import load_config


def synth_1h_from_15m(bars_15m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """4 consecutive 15m bars → 1 synthesized 1h bar (정각 경계 정렬).

    Input 봉들은 open_time 오름차순. 각 1h 윈도우의 open_time은 4*15m=3600000ms 배수.
    윈도우 미완성(4개 미만) 시 출력에서 제외.
    """
    out: List[Dict[str, Any]] = []
    bucket: List[Dict[str, Any]] = []
    for bar in bars_15m:
        ot = int(bar["open_time"])
        if not bucket and ot % 3_600_000 != 0:
            continue
        bucket.append(bar)
        if len(bucket) == 4:
            window_start = int(bucket[0]["open_time"])
            out.append({
                "open_time": window_start,
                "open": bucket[0]["open"],
                "high": max(b["high"] for b in bucket),
                "low": min(b["low"] for b in bucket),
                "close": bucket[-1]["close"],
                "volume": sum(b["volume"] for b in bucket),
            })
            bucket = []
    return out


def _compare_bars(synth: List[Dict[str, Any]], direct: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """합성과 직접 봉을 open_time 기준 매칭, 차이 검출."""
    direct_by_ot = {int(b["open_time"]): b for b in direct}
    diffs: List[Dict[str, Any]] = []
    for s in synth:
        d = direct_by_ot.get(int(s["open_time"]))
        if d is None:
            diffs.append({"open_time": s["open_time"], "issue": "missing in direct"})
            continue
        for field in ("open", "high", "low", "close", "volume"):
            sv, dv = float(s[field]), float(d[field])
            if abs(sv - dv) > max(1e-6, abs(dv) * 1e-6):
                diffs.append({
                    "open_time": s["open_time"], "field": field,
                    "synth": sv, "direct": dv, "delta": sv - dv,
                })
    return diffs


def main() -> None:
    parser = argparse.ArgumentParser(description="15m→1h parity check vs Bybit direct 1h")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--bars", type=int, default=24, help="비교할 1h 봉 수")
    args = parser.parse_args()

    cfg = load_config()
    rest = BybitRestClient(cfg.app.api_key, cfg.app.api_secret, cfg.app.base_url)

    n_15m = args.bars * 4 + 4
    bars_15m = rest.get_klines(symbol=args.symbol, interval="15", limit=n_15m)
    bars_15m.sort(key=lambda b: int(b["open_time"]))

    bars_1h_direct = rest.get_klines(symbol=args.symbol, interval="60", limit=args.bars + 2)
    bars_1h_direct.sort(key=lambda b: int(b["open_time"]))

    synth = synth_1h_from_15m(bars_15m)
    diffs = _compare_bars(synth, bars_1h_direct)

    print(f"Synthesized 1h bars: {len(synth)}")
    print(f"Direct 1h bars:      {len(bars_1h_direct)}")
    print(f"Differences:         {len(diffs)}")
    if diffs:
        print("\nFirst 10 diffs:")
        for d in diffs[:10]:
            print(f"  {d}")
        sys.exit(1)
    print("\nParity OK — no significant differences.")
    sys.exit(0)


if __name__ == "__main__":
    main()
