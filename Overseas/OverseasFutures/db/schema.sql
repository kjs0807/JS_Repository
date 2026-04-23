-- OverseasFutures DB 스키마
-- DESIGN.md 9절 기반 확장 스키마 (호가 5단계, Paper Trading 테이블 포함)

-- ── 상품 마스터 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products_master (
    symbol           TEXT PRIMARY KEY,
    asset_class      TEXT NOT NULL,
    exchange         TEXT NOT NULL,
    exch_cd          TEXT,                -- KIS EXCH_CD ("EUREX", "OSE", "HKEx", "ASX", "FTX")
    name_en          TEXT NOT NULL,
    name_kr          TEXT,
    size_type        TEXT,
    contract_size    REAL,
    tick_size        REAL,
    tick_value       REAL,
    currency         TEXT NOT NULL,
    trading_hours    TEXT,
    kis_code         TEXT,
    kis_code_current TEXT,               -- 현재 근월물 KIS 종목코드
    expiry_date      TEXT,               -- 만기일 YYYYMMDD
    margin           REAL,
    point_value      REAL,               -- tick_value / tick_size
    is_core          INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_exchange   ON products_master (exchange);
CREATE INDEX IF NOT EXISTS idx_products_exch_cd    ON products_master (exch_cd);
CREATE INDEX IF NOT EXISTS idx_products_asset_class ON products_master (asset_class);

-- ── 일봉 OHLCV ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    symbol  TEXT    NOT NULL,
    date    TEXT    NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  INTEGER,
    oi      INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_symbol ON ohlcv_daily (symbol);
CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_date   ON ohlcv_daily (date);

-- ── 분봉 OHLCV ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlcv_intraday (
    symbol    TEXT    NOT NULL,
    datetime  TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,   -- "1m", "5m", "15m", "60m"
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    INTEGER,
    PRIMARY KEY (symbol, datetime, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_intraday_symbol    ON ohlcv_intraday (symbol);
CREATE INDEX IF NOT EXISTS idx_ohlcv_intraday_datetime  ON ohlcv_intraday (datetime);
CREATE INDEX IF NOT EXISTS idx_ohlcv_intraday_timeframe ON ohlcv_intraday (timeframe);

-- ── 실시간 틱 (호가 5단계) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS realtime_ticks (
    symbol      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    price       REAL,
    volume      INTEGER,
    bid1        REAL,   bid1_qty INTEGER,
    bid2        REAL,   bid2_qty INTEGER,
    bid3        REAL,   bid3_qty INTEGER,
    bid4        REAL,   bid4_qty INTEGER,
    bid5        REAL,   bid5_qty INTEGER,
    ask1        REAL,   ask1_qty INTEGER,
    ask2        REAL,   ask2_qty INTEGER,
    ask3        REAL,   ask3_qty INTEGER,
    ask4        REAL,   ask4_qty INTEGER,
    ask5        REAL,   ask5_qty INTEGER,
    PRIMARY KEY (symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_realtime_ticks_symbol    ON realtime_ticks (symbol);
CREATE INDEX IF NOT EXISTS idx_realtime_ticks_timestamp ON realtime_ticks (timestamp);

-- ── 체결 틱 원본 (HDFFF020 WebSocket) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_ticks (
    symbol      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    price       REAL    NOT NULL,
    quantity    INTEGER,            -- 체결수량
    cum_volume  INTEGER,            -- 누적거래량
    direction   TEXT,               -- "BUY" / "SELL" (quotsign 기반)
    open_price  REAL,               -- 당일 시가
    high_price  REAL,               -- 당일 고가
    low_price   REAL,               -- 당일 저가
    recv_date   TEXT,               -- 수신일자 YYYYMMDD
    recv_time   TEXT,               -- 수신시각 HHMMSS
    PRIMARY KEY (symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_trade_ticks_symbol    ON trade_ticks (symbol);
CREATE INDEX IF NOT EXISTS idx_trade_ticks_timestamp  ON trade_ticks (timestamp);
CREATE INDEX IF NOT EXISTS idx_trade_ticks_recv_date  ON trade_ticks (recv_date);

-- ── Paper Trading 거래 기록 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    datetime     TEXT    NOT NULL,
    side         TEXT    NOT NULL,   -- "BUY" / "SELL"
    qty          INTEGER NOT NULL,
    price        REAL    NOT NULL,
    order_type   TEXT,               -- "MARKET" / "LIMIT"
    strategy     TEXT,
    event_type   TEXT,
    pnl          REAL,
    pnl_currency TEXT,
    commission   REAL DEFAULT 0,
    note         TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol   ON paper_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_paper_trades_datetime ON paper_trades (datetime);
CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades (strategy);

-- ── Paper 포지션 스냅샷 (상태 복원용) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol         TEXT PRIMARY KEY,
    side           TEXT NOT NULL,    -- "LONG" / "SHORT"
    qty            INTEGER NOT NULL,
    avg_price      REAL    NOT NULL,
    margin_used    REAL,
    unrealized_pnl REAL,
    fsm_state      TEXT,
    updated_at     TEXT DEFAULT (datetime('now'))
);
