"""BacktestResult (spec §4.1).

requested vs resolved run_id를 모두 보존해서 사용자 의도와 실제 디렉토리를
모두 추적 가능하게 한다 (on_run_exists='auto_suffix' 정책 핵심).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class BacktestResult:
    """백테스트 실행 결과.

    `requested_run_id`는 사용자가 BacktestConfig에 명시한 원본,
    `resolved_run_id`는 on_run_exists 정책 적용 후 실제 사용된 디렉토리명.
    auto_suffix 발생 시 둘이 다르며, 사용자 노출은 항상 resolved 기반.

    `config_path`는 영속화된 **canonical** config 파일 경로:
    - Phase 1: `{run_dir}/config.json` (감사용 단방향).
    - Phase 1.5+: `{run_dir}/config.yaml` (양방향 round-trip via ``BacktestConfig.from_yaml``).
      Phase 1 audit json (`{run_dir}/config.json`) 도 동시에 영속화되며, 분석 도구는 yaml 우선
      / json fallback 순서로 읽는다 (`viz.run_chart._load_run_config`).
    """

    requested_run_id: str
    resolved_run_id: str
    run_dir: Path
    final_equity: Decimal
    total_return: Decimal
    num_fills: int
    num_intents: int
    config_path: Path
    events_path: Path
