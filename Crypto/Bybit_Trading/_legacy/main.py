"""Bybit 암호화폐 선물 모의거래 시스템 메인 진입점.

사용법:
    python main.py backtest          # 백테스트 실행 (6개 전략 → Top 3 선정)
    python main.py trade             # 모의거래 시작 (Top 3 전략)
    python main.py collect           # 과거 데이터 수집
    python main.py dashboard         # GUI 대시보드 실행
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR, DB_PATH, LOGS_DIR
from config.settings import AppSettings, RiskParams, backtest_config

# 로그 디렉토리 생성
Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(LOGS_DIR) / "main.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# =============================================================================
# 커맨드: backtest
# =============================================================================

def cmd_backtest(args: argparse.Namespace) -> None:
    """백테스트 실행 커맨드.

    6개 전략 × 10개 심볼 백테스트 → Top 3 자동 선정 → JSON 저장

    Args:
        args: CLI 인수
    """
    logger.info("=== 백테스트 모드 시작 ===")
    try:
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_backtest.py")]
        if args.strategy:
            cmd += ["--strategy", args.strategy]
        if args.symbols:
            cmd += ["--symbols"] + args.symbols
        if args.start:
            cmd += ["--start", args.start]
        if args.end:
            cmd += ["--end", args.end]
        result = subprocess.run(cmd, check=False)
        sys.exit(result.returncode)
    except Exception as exc:
        logger.error("백테스트 실행 실패: %s", exc)
        sys.exit(1)


# =============================================================================
# 커맨드: collect
# =============================================================================

def cmd_collect(args: argparse.Namespace) -> None:
    """과거 데이터 수집 커맨드.

    Args:
        args: CLI 인수
    """
    logger.info("=== 데이터 수집 모드 시작 ===")
    try:
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "initial_data_load.py")]
        if args.symbols:
            cmd += ["--symbols"] + args.symbols
        if args.days:
            cmd += ["--days", str(args.days)]
        result = subprocess.run(cmd, check=False)
        sys.exit(result.returncode)
    except Exception as exc:
        logger.error("데이터 수집 실패: %s", exc)
        sys.exit(1)


# =============================================================================
# 커맨드: trade
# =============================================================================

def cmd_trade(args: argparse.Namespace) -> None:
    """모의거래 실행 커맨드.

    4개 전략(PairsTrading, BBKCSqueeze, IchimokuCloud, RSIMACDStrategy)을
    TradingEngine으로 통합 운용하며 Bybit Demo API 모의거래를 시작한다.

    Args:
        args: CLI 인수
    """
    logger.info("=== 모의거래 모드 시작 (TradingEngine v2) ===")

    # 동적 심볼/상품 스펙 초기화
    from config.symbol_manager import init_symbol_manager
    from config.products import fetch_products_from_api
    sm = init_symbol_manager(top_n=100, pairs_n=30)
    logger.info(
        "심볼 유니버스: 전체=%d개, 페어=%d개, 전략고정=%d개",
        len(sm.all_symbols), len(sm.pairs_universe), len(sm.strategy_symbols),
    )
    fetch_products_from_api()

    # 싱글턴 settings를 symbol_manager 반영된 상태로 재생성
    # WS 구독 대상 = strategy_symbols(13) ∪ pairs_universe(30)
    import config.settings as _cfg
    _cfg.settings = _cfg.AppSettings()
    trade_symbols = list(sm.pairs_universe)
    for sym in sm.strategy_symbols:
        if sym not in trade_symbols:
            trade_symbols.append(sym)
    _cfg.settings.symbols = trade_symbols
    settings = _cfg.settings
    risk_params = RiskParams()

    logger.info(
        "설정: URL=%s, 심볼=%d개, 레버리지=%dx",
        settings.base_url, len(settings.symbols), settings.leverage
    )

    # 모듈 임포트
    try:
        from api.rest_client import BybitRestClient
        from api.ws_client import BybitWebSocketClient
        from db.db_manager import DBManager
        from risk.risk_manager import RiskManager
        from paper_engine.trading_engine import TradingEngine
    except ImportError as exc:
        logger.error("모듈 임포트 실패: %s", exc)
        sys.exit(1)

    # 컴포넌트 초기화
    rest_client = BybitRestClient(base_url=settings.base_url)
    db = DBManager(DB_PATH)
    db.initialize()  # signal_log, trade_log 테이블 보장

    # gap 자동 수집 (마지막 봉 ~ 현재)
    from utils.data_gap import fill_data_gap
    logger.info("데이터 gap 확인 및 수집 시작 (%d개 심볼)...", len(settings.symbols))
    fill_data_gap(db, settings.symbols)
    logger.info("데이터 gap 수집 완료")
    risk_mgr = RiskManager(risk_params, initial_capital=backtest_config.initial_capital, leverage=settings.leverage)

    # 통합 트레이딩 엔진 생성
    engine = TradingEngine(
        db=db,
        rest_client=rest_client,
        risk_manager=risk_mgr,
        leverage=settings.leverage,
    )
    logger.info("TradingEngine 초기화 완료 (4전략: PairsTrading / BBKCSqueeze / IchimokuCloud / RSIMACDStrategy)")

    # WebSocket 연결 + 이벤트 루프
    ws_client = BybitWebSocketClient(ws_url=settings.ws_url)

    def on_kline_closed(symbol: str, interval: str, kline: dict) -> None:
        """봉 확정 이벤트 핸들러.

        WebSocket에서 15분봉이 확정되면 TradingEngine.on_new_bar_15m()으로 위임한다.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: 타임프레임 (예: "15")
            kline: 완성된 봉 데이터 딕셔너리
        """
        # 15분봉만 처리
        if interval != "15":
            return

        try:
            bar = {
                "open_time": int(kline.get("start", 0)),
                "open":  float(kline.get("open",  0)),
                "high":  float(kline.get("high",  0)),
                "low":   float(kline.get("low",   0)),
                "close": float(kline.get("close", 0)),
                "volume": float(kline.get("volume", 0)),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("봉 데이터 파싱 실패 %s: %s", symbol, exc)
            return

        engine.on_new_bar_15m(symbol, bar)

    ws_client.on_kline_closed = on_kline_closed

    try:
        logger.info("WebSocket 연결 시작: %s", settings.ws_url)
        ws_client.start(
            symbols=settings.symbols,
            intervals=["15"],
        )
        logger.info("모의거래 실행 중... (Ctrl+C로 중지)")

        import time
        while True:
            # 엔진 상태 주기적 로그 (60초)
            time.sleep(60)
            status = engine.get_status()
            risk_st = status["risk_status"]
            logger.info(
                "엔진 상태: 에퀴티=%.0f, 일일PnL=%.2f, DD=%.2f%%, 포지션=%d건, 처리봉=%d",
                risk_st["equity"], status["daily_pnl"],
                risk_st["drawdown_pct"], status["position_count"],
                status["total_bars_processed"],
            )

            # 심볼 유니버스 변경 시 자동 재시작
            if engine._restart_requested:
                logger.info("=== 심볼 유니버스 변경 감지 → 프로세스 재시작 ===")
                ws_client.stop()
                try:
                    engine.save_state()
                except Exception as exc:
                    logger.warning("재시작 전 상태 저장 실패: %s", exc)
                # 동일 명령으로 프로세스 교체 (execv)
                import os
                os.execv(sys.executable, [sys.executable] + sys.argv)
    except KeyboardInterrupt:
        logger.info("모의거래 중단 (Ctrl+C)")
    finally:
        ws_client.stop()
        # 최종 상태 출력
        try:
            final_status = engine.get_status()
            logger.info("최종 상태: %s", final_status)
        except Exception as exc:
            logger.warning("최종 상태 조회 실패: %s", exc)
        logger.info("WebSocket 연결 종료")


# =============================================================================
# 커맨드: dashboard
# =============================================================================

def cmd_dashboard(args: argparse.Namespace) -> None:
    """GUI 대시보드 실행 커맨드.

    대시보드 창을 생성하고 DB를 주입한 뒤 mainloop를 시작한다.
    모의거래 시작/중지는 대시보드 개요 탭 버튼으로 제어한다.

    Args:
        args: CLI 인수
    """
    logger.info("=== 대시보드 모드 시작 ===")

    try:
        import tkinter as tk
    except ImportError:
        logger.error("tkinter를 사용할 수 없습니다.")
        sys.exit(1)

    try:
        from dashboard.app import create_dashboard
        from db.db_manager import DBManager
    except ImportError as exc:
        logger.error("대시보드 모듈 임포트 실패: %s", exc)
        sys.exit(1)

    # DB 초기화
    db = DBManager(DB_PATH)
    db.initialize()

    # 대시보드 생성 및 DB 주입
    root, dashboard = create_dashboard()
    dashboard.set_db(db)

    logger.info("대시보드 시작 (모의거래는 개요 탭에서 시작)")
    root.mainloop()
    logger.info("대시보드 종료")


# =============================================================================
# CLI 파서
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성.

    Returns:
        ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Bybit 암호화폐 선물 모의거래 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
커맨드 예시:
  python main.py backtest                     # 전체 백테스트 실행
  python main.py backtest --strategy BB_Squeeze_MR --symbols BTCUSDT ETHUSDT
  python main.py collect                      # 과거 데이터 수집
  python main.py collect --days 90            # 최근 90일 수집
  python main.py trade                        # 모의거래 시작
  python main.py dashboard                    # GUI 대시보드 실행
        """,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="커맨드")

    # ── backtest ──────────────────────────────────────────────────
    bt_parser = subparsers.add_parser(
        "backtest", help="백테스트 실행 (6개 전략 → Top 3 선정)"
    )
    bt_parser.add_argument(
        "--strategy",
        choices=[
            "BB_Squeeze_MR", "RSI_MACD_TF", "Ichimoku_TF",
            "KAMA_MR", "PairZScore_MR", "Volume_TF", "all",
        ],
        default="all",
        help="실행할 전략 (기본: all)",
    )
    bt_parser.add_argument("--symbols", nargs="+", help="백테스트 심볼 목록")
    bt_parser.add_argument("--start", help="시작일 YYYY-MM-DD")
    bt_parser.add_argument("--end", help="종료일 YYYY-MM-DD")

    # ── collect ───────────────────────────────────────────────────
    col_parser = subparsers.add_parser("collect", help="과거 OHLCV 데이터 수집")
    col_parser.add_argument("--symbols", nargs="+", help="수집할 심볼 목록")
    col_parser.add_argument("--days", type=int, help="수집 기간 (일수)")

    # ── trade ─────────────────────────────────────────────────────
    subparsers.add_parser("trade", help="모의거래 시작 (Top 3 전략 실행)")

    # ── dashboard ─────────────────────────────────────────────────
    subparsers.add_parser("dashboard", help="GUI 대시보드 실행")

    return parser


def main() -> None:
    """메인 진입점."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "backtest": cmd_backtest,
        "collect": cmd_collect,
        "trade": cmd_trade,
        "dashboard": cmd_dashboard,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
