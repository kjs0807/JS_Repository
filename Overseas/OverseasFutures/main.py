"""OverseasFutures — 해외선물 통합 Paper Trading 시스템.

Usage:
    python main.py                    # GUI 대시보드 실행
    python main.py init-db            # DB 초기화
    python main.py collect-daily      # 일봉 수집
    python main.py collect-daily --symbol VG  # 특정 종목만
    python main.py poll              # 현재가 폴링 시작 (CLI)
    python main.py ws               # WebSocket 실시간 (체결+호가)
    python main.py status            # 거래소 상태 확인
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

# Project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH, LOGS_DIR, STATE_FILE
from config.products import PRODUCTS

logger = logging.getLogger("OverseasFutures")


def setup_logging(level=logging.INFO):
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f"app_{datetime.now():%Y%m%d}.log")
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def cmd_init_db(args):
    from db.init_db import initialize_database

    initialize_database(
        db_path=args.db_path or DB_PATH,
        force_recreate=args.force,
    )


def cmd_collect_daily(args):
    from config.settings import KISConfig
    from api.auth import TokenManager
    from api.rest_client import KISRestClient
    from collector.daily_ohlcv import DailyCollector

    config = KISConfig()
    token_mgr = TokenManager(config)
    client = KISRestClient(config, token_mgr)
    collector = DailyCollector(client, args.db_path or DB_PATH)

    end_date = args.end_date or datetime.now().strftime("%Y%m%d")
    start_date = args.start_date or (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

    if args.symbol:
        product = PRODUCTS.get(args.symbol)
        if product is None:
            logger.error("Unknown symbol: %s", args.symbol)
            return
        collector.collect_symbol(
            product.symbol, product.kis_code, product.exch_cd, start_date, end_date
        )
    else:
        collector.collect_all(start_date, end_date)


def cmd_status(args):
    from scheduler.exchange_hours import get_open_exchanges

    now = datetime.now()
    print(f"\n현재 시각 (KST): {now:%Y-%m-%d %H:%M:%S}")
    print(f"\n{'거래소':<8} {'상태':<6} {'상품수'}")
    print("-" * 30)
    open_exchanges = get_open_exchanges(now)
    for exch in ["EUREX", "OSE", "HKEx", "ASX", "FTX"]:
        is_open = exch in open_exchanges
        status = "열림" if is_open else "닫힘"
        count = len([p for p in PRODUCTS.values() if p.exch_cd == exch])
        marker = "●" if is_open else "○"
        print(f"  {marker} {exch:<6} {status:<6} {count}개")
    print(f"\n등록 상품: {len(PRODUCTS)}개")
    print(f"열린 거래소: {len(open_exchanges)}개")


def cmd_ws(args):
    """WebSocket 실시간 수신 (체결가 + 호가 5단계)."""
    from config.settings import KISConfig
    from api.auth import TokenManager
    from api.ws_client import KISWebSocketClient, TradeData, OrderbookData
    from collector.bar_resampler import BarResampler
    from collector.vwap_calculator import IntradayVWAPCalculator
    from db.init_db import initialize_database

    # DB 초기화 + 틱 아카이브
    db_path = args.db_path or DB_PATH
    initialize_database(db_path=db_path)
    _run_tick_archive(db_path, keep_days=3)

    config = KISConfig()

    # KIS 종목코드 → 루트심볼 매핑
    code_to_root = {p.kis_code: sym for sym, p in PRODUCTS.items()}

    # 일중 VWAP 계산기 (per symbol)
    vwap_calcs: dict = {}
    for sym in PRODUCTS:
        vwap_calcs[sym] = IntradayVWAPCalculator(sym, sd_period=20)

    # 1분봉 리샘플러 (체결가 → 정밀 봉 + VWAP)
    resamplers: dict = {}
    for sym in PRODUCTS:
        def make_cb(s):
            def cb(bar):
                # 봉 완성 시 VWAP 지표 갱신
                calc = vwap_calcs.get(s)
                bar_date = bar.start_time.strftime("%Y%m%d")
                if calc:
                    vs = calc.on_bar(
                        bar.open, bar.high, bar.low, bar.close,
                        bar.volume, bar_date,
                    )
                    logger.info(
                        "[1m] %s %s C=%.2f V=%d VWAP=%.2f SD=%.4f [%.2f ~ %.2f]",
                        s, bar.start_time.strftime("%H:%M"),
                        bar.close, bar.volume,
                        vs.vwap, vs.vwap_sd, vs.lower_1sd, vs.upper_1sd,
                    )
                bar_resampler = resamplers.get(s)
                if bar_resampler:
                    bar_resampler.save_bar_to_db(bar, db_path)
            return cb
        resamplers[sym] = BarResampler(
            sym, timeframe_minutes=1, on_bar_complete=make_cb(sym)
        )

    # 체결 콜백
    def on_trade(trade: TradeData) -> None:
        root = code_to_root.get(trade.symbol, trade.symbol)
        print(
            f"[체결] {root:>4} {trade.price:>12,.2f} x{trade.quantity:<4} "
            f"vol={trade.volume:<8} {trade.recv_time[:6]}"
        )
        resampler = resamplers.get(root)
        if resampler:
            resampler.on_tick(trade.price, trade.quantity, trade.timestamp)

    # 호가 콜백
    def on_orderbook(ob: OrderbookData) -> None:
        root = code_to_root.get(ob.symbol, ob.symbol)
        if ob.bids and ob.asks:
            best_bid = ob.bids[0]["price"]
            best_ask = ob.asks[0]["price"]
            spread = best_ask - best_bid if best_ask and best_bid else 0
            print(
                f"[호가] {root:>4} "
                f"bid={best_bid:>12,.2f}({ob.bids[0]['qty']:>4}) "
                f"ask={best_ask:>12,.2f}({ob.asks[0]['qty']:>4}) "
                f"spread={spread:,.2f}"
            )

    ws_client = KISWebSocketClient(
        config=config,
        on_trade=on_trade,
        on_orderbook=on_orderbook,
        db_path=db_path,
        save_ticks_to_db=True,
    )
    ws_client.set_code_mapping(code_to_root)

    # 구독 종목 선택
    if args.symbol:
        product = PRODUCTS.get(args.symbol)
        if product is None:
            logger.error("Unknown symbol: %s", args.symbol)
            return
        symbols = [product.kis_code]
    else:
        symbols = [p.kis_code for p in PRODUCTS.values()]

    def shutdown(sig, frame):
        logger.info("종료 신호 수신...")
        ws_client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info(
        "WebSocket 실시간 시작: %d개 종목 (Ctrl+C로 종료)", len(symbols)
    )
    ws_client.start(symbols)

    # 메인 스레드 대기 + 주기적 통계 출력
    try:
        while ws_client.is_running:
            time.sleep(60)
            stats = ws_client.get_stats()
            logger.info(
                "WS 통계: 체결=%d 호가=%d 구독=%d",
                stats["trade_count"],
                stats["orderbook_count"],
                stats["subscriptions"],
            )
    except KeyboardInterrupt:
        pass
    finally:
        ws_client.stop()


def cmd_poll(args):
    """CLI 모드 폴링."""
    from config.settings import KISConfig
    from api.auth import TokenManager
    from api.rest_client import KISRestClient
    from collector.realtime_poller import RealtimePoller
    from scheduler.poll_scheduler import PollScheduler

    config = KISConfig()
    token_mgr = TokenManager(config)
    client = KISRestClient(config, token_mgr)

    def on_tick(symbol, price, volume, timestamp):
        print(f"[{timestamp:%H:%M:%S}] {symbol}: {price:,.2f} (vol={volume})")

    poller = RealtimePoller(client, args.db_path or DB_PATH, on_tick_callback=on_tick)
    scheduler = PollScheduler(poller)

    def shutdown(sig, frame):
        logger.info("종료 신호 수신...")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("폴링 시작 (Ctrl+C로 종료)")
    scheduler.start(interval_sec=args.interval or 30)


def _run_tick_archive(db_path: str, keep_days: int = 3) -> None:
    """앱 시작 시 오래된 틱 데이터를 Parquet로 아카이브."""
    try:
        from db.tick_archiver import TickArchiver
        archiver = TickArchiver(db_path=db_path, archive_dir=os.path.join(os.path.dirname(db_path), "archive"))
        result = archiver.archive_old_ticks(keep_days=keep_days)
        for table, info in result.items():
            if info["archived"] > 0:
                logger.info("[아카이브] %s: %d건 → %s", table, info["archived"], info["file"])
    except Exception as e:
        logger.warning("틱 아카이브 실패 (무시하고 계속): %s", e)


def cmd_gui(args):
    """GUI 대시보드 실행."""
    import tkinter as tk
    from config.settings import KISConfig
    from api.auth import TokenManager
    from api.rest_client import KISRestClient
    from collector.realtime_poller import RealtimePoller
    from collector.bar_resampler import BarResampler
    from scheduler.poll_scheduler import PollScheduler
    from paper_engine.virtual_account import VirtualAccount
    from paper_engine.order_manager import OrderManager
    from paper_engine.fill_simulator import FillSimulator
    from paper_engine.position_tracker import PositionTracker
    from paper_engine.state_persistence import StatePersistence
    from strategy import TradeConnector
    from strategy.dual_bollinger.events import EventType
    from dashboard.app import OverseasFuturesDashboard
    from db.init_db import initialize_database

    # DB 초기화 + 틱 아카이브
    db_path = args.db_path or DB_PATH
    initialize_database(db_path=db_path)
    _run_tick_archive(db_path, keep_days=3)

    # KIS API 클라이언트
    try:
        config = KISConfig()
        token_mgr = TokenManager(config)
        client = KISRestClient(config, token_mgr)
        api_ready = True
    except Exception as e:
        logger.warning("KIS API 초기화 실패 (오프라인 모드): %s", e)
        client = None
        api_ready = False

    # Paper Engine
    account = VirtualAccount()
    order_mgr = OrderManager()
    fill_sim = FillSimulator()
    pos_tracker = PositionTracker()
    state_persist = StatePersistence(STATE_FILE)

    # Strategy connector
    connector = TradeConnector()

    # Bar resamplers (per symbol)
    resamplers: dict = {}
    for sym in PRODUCTS:
        def make_callback(s):
            def cb(bar):
                connector.on_bar_complete(s, bar, datetime.now())
            return cb
        resamplers[sym] = BarResampler(
            sym, timeframe_minutes=60, on_bar_complete=make_callback(sym)
        )

    # Poller + Scheduler
    if api_ready:
        def on_tick(symbol, price, volume, timestamp):
            # Feed to bar resampler
            resampler = resamplers.get(symbol)
            if resampler:
                resampler.on_tick(price, volume, timestamp)
            # Check pending limit orders
            order_mgr.check_fills({symbol: price})

        poller = RealtimePoller(client, db_path, on_tick_callback=on_tick)
        scheduler = PollScheduler(poller)
    else:
        scheduler = None

    # Strategy → Paper order callback
    def on_strategy_event(event):
        product = PRODUCTS.get(event.symbol)
        if product is None:
            return

        if event.event_type == EventType.ENTRY_1ST:
            order_mgr.submit_market_order(
                event.symbol, event.side, event.qty,
                strategy="DualBB", event_type=event.event_type.value,
            )
            if event.side == "BUY":
                pos_tracker.open_position(event.symbol, "LONG", event.qty, event.price, product)
            else:
                pos_tracker.open_position(event.symbol, "SHORT", event.qty, event.price, product)
            account.reserve_margin(event.symbol, event.qty, product)

        elif event.event_type == EventType.ENTRY_2ND:
            order_mgr.submit_market_order(
                event.symbol, event.side, event.qty,
                strategy="DualBB", event_type=event.event_type.value,
            )
            pos = pos_tracker.get_position(event.symbol)
            if pos:
                total_cost = pos.avg_price * (pos.qty - event.qty) + event.price * event.qty
                pos.avg_price = total_cost / pos.qty
            account.reserve_margin(event.symbol, event.qty, product)

        elif event.event_type in (
            EventType.STOP_LOSS,
            EventType.FULL_EXIT,
            EventType.BAND_EXIT,
            EventType.OUTER_RSI_EXIT,
            EventType.TRAILING_STOP_EXIT,
            EventType.PARTIAL_EXIT,
        ):
            order_mgr.submit_market_order(
                event.symbol, event.side, event.qty,
                strategy="DualBB", event_type=event.event_type.value,
            )
            pnl = pos_tracker.close_position(event.symbol, event.price, event.qty, product)
            account.release_margin(event.symbol, event.qty, product)
            logger.info("[%s] PnL: %.2f %s", event.symbol, pnl, product.currency)

    connector.order_callback = on_strategy_event

    # Restore state
    saved = state_persist.load()
    if saved:
        try:
            account_data = saved.get("account")
            if account_data:
                account = VirtualAccount.from_dict(account_data)
            order_data = saved.get("orders")
            if order_data:
                order_mgr = OrderManager.from_dict(order_data)
            pos_data = saved.get("positions")
            if pos_data:
                pos_tracker = PositionTracker.from_dict(pos_data)
            fsm_data = saved.get("fsm_states")
            if fsm_data:
                connector.restore_states(fsm_data)
            logger.info("상태 복원 완료")
        except Exception as e:
            logger.warning("상태 복원 실패: %s", e)

    # GUI
    root = tk.Tk()
    dashboard = OverseasFuturesDashboard(root)
    dashboard.set_references(
        trade_manager=connector,
        virtual_account=account,
        poll_scheduler=scheduler,
        state_persistence=state_persist,
    )

    # Periodic GUI update
    def update_gui():
        # Update prices
        price_data = {}
        for sym, trader in connector.traders.items():
            if trader.last_price is not None:
                price_data[sym] = {"price": trader.last_price, "change": 0}
        dashboard.update_prices(price_data)

        # Update states
        dashboard.update_states(connector.get_states())

        # Update positions
        positions = {}
        for sym, pos in pos_tracker.positions.items():
            positions[sym] = {
                "side": pos.side,
                "qty": pos.qty,
                "avg_price": pos.avg_price,
                "unrealized_pnl": pos.unrealized_pnl,
                "currency": pos.currency,
            }
        dashboard.update_positions(positions)

        root.after(2000, update_gui)

    root.after(2000, update_gui)

    # Save state on close
    def on_closing():
        logger.info("앱 종료 — 상태 저장 중...")
        if scheduler:
            scheduler.stop()
        state_persist.save(
            account=account,
            order_manager=order_mgr,
            position_tracker=pos_tracker,
            fsm_states=connector.get_fsm_states_for_save(),
            bar_states={
                sym: r.to_dict()
                for sym, r in resamplers.items()
                if r.to_dict() is not None
            },
        )
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    logger.info("대시보드 시작")
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="OverseasFutures - 해외선물 Paper Trading 시스템"
    )
    parser.add_argument("--db-path", default=None, help="DB 경로")

    subparsers = parser.add_subparsers(dest="command")

    # init-db
    init_parser = subparsers.add_parser("init-db", help="DB 초기화")
    init_parser.add_argument("--force", action="store_true", help="기존 DB 삭제 후 재생성")

    # collect-daily
    collect_parser = subparsers.add_parser("collect-daily", help="일봉 데이터 수집")
    collect_parser.add_argument("--symbol", default=None, help="특정 종목만 (예: VG)")
    collect_parser.add_argument("--start-date", default=None, help="시작일 YYYYMMDD")
    collect_parser.add_argument("--end-date", default=None, help="종료일 YYYYMMDD")

    # status
    subparsers.add_parser("status", help="거래소 상태 확인")

    # poll
    poll_parser = subparsers.add_parser("poll", help="현재가 폴링 (CLI)")
    poll_parser.add_argument("--interval", type=float, default=30, help="폴링 간격 (초)")

    # ws
    ws_parser = subparsers.add_parser("ws", help="WebSocket 실시간 (체결+호가)")
    ws_parser.add_argument("--symbol", default=None, help="특정 종목만 (예: VG)")

    args = parser.parse_args()
    setup_logging()

    if args.command == "init-db":
        cmd_init_db(args)
    elif args.command == "collect-daily":
        cmd_collect_daily(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "poll":
        cmd_poll(args)
    elif args.command == "ws":
        cmd_ws(args)
    else:
        cmd_gui(args)


if __name__ == "__main__":
    main()
