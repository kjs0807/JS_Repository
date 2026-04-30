"""백테스터 예외 계층 (spec §12).

| 클래스                | 카테고리      | 의미                                   |
|-----------------------|---------------|----------------------------------------|
| BacktestError         | base          | 모든 백테스터 예외의 루트              |
| DataError             | Halt          | 데이터 무결성/스키마 위반              |
| InstrumentError       | Fatal         | Instrument 정의 결함                   |
| RiskError             | Recoverable   | RiskManager 거부                       |
| ExecutionError        | Recoverable   | ExecutionModel 체결 실패               |
| RunDirectoryError     | Fatal         | Run 디렉토리 충돌(on_run_exists=fail)  |
| ConfigError           | Fatal         | BacktestConfig.__post_init__ 검증 실패 |
"""

from __future__ import annotations


class BacktestError(Exception):
    """백테스터 예외 계층의 루트."""


class DataError(BacktestError):
    """데이터 무결성/스키마 위반. Halt 카테고리."""


class InstrumentError(BacktestError):
    """Instrument 정의 결함. Fatal 카테고리."""


class RiskError(BacktestError):
    """RiskManager가 주문을 거부. Recoverable 카테고리."""


class ExecutionError(BacktestError):
    """ExecutionModel이 체결 실패. Recoverable 카테고리."""


class RunDirectoryError(BacktestError):
    """Run 디렉토리 충돌(on_run_exists='fail' 정책). Fatal 카테고리."""


class ConfigError(BacktestError):
    """BacktestConfig.__post_init__ 검증 실패. Engine 시작 전 차단. Fatal 카테고리."""
