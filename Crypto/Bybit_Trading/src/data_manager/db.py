"""DBManager — SQLite CRUD. OHLCV, 상품 마스터, 거래/시그널 로그."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

TIMEFRAME_TABLE: Dict[str, str] = {
    "5": "ohlcv_5m", "5m": "ohlcv_5m",
    "15": "ohlcv_15m", "15m": "ohlcv_15m",
    "30": "ohlcv_30m", "30m": "ohlcv_30m",
    "60": "ohlcv_1h", "1h": "ohlcv_1h",
    "240": "ohlcv_4h", "4h": "ohlcv_4h",
    "D": "ohlcv_daily", "1d": "ohlcv_daily", "daily": "ohlcv_daily",
}


class DBManager:
    def __init__(self, db_path: str, schema_path: Optional[str] = None) -> None:
        self.db_path = db_path
        if schema_path is None:
            schema_path = str(Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql")
        self.schema_path = schema_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        schema_file = Path(self.schema_path)
        if not schema_file.exists():
            raise FileNotFoundError(f"schema.sql 없음: {self.schema_path}")
        schema_sql = schema_file.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(schema_sql)
            conn.commit()

    def _resolve_table(self, timeframe: str) -> str:
        table = TIMEFRAME_TABLE.get(timeframe)
        if table is None:
            raise ValueError(f"지원하지 않는 타임프레임: {timeframe}. 가능: {list(TIMEFRAME_TABLE.keys())}")
        return table

    def upsert_bars(self, symbol: str, timeframe: str, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        table = self._resolve_table(timeframe)
        for row in rows:
            row.setdefault("symbol", symbol)
            row.setdefault("turnover", None)
        sql = f"""
            INSERT INTO {table} (symbol, open_time, open, high, low, close, volume, turnover)
            VALUES (:symbol, :open_time, :open, :high, :low, :close, :volume, :turnover)
            ON CONFLICT (symbol, open_time) DO UPDATE SET
                close = excluded.close,
                high = MAX(excluded.high, {table}.high),
                low = MIN(excluded.low, {table}.low),
                volume = excluded.volume,
                turnover = excluded.turnover
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            conn.commit()
        return cursor.rowcount

    def get_bars(self, symbol: str, timeframe: str, start_time: Optional[int] = None,
                 end_time: Optional[int] = None, limit: Optional[int] = None) -> pd.DataFrame:
        table = self._resolve_table(timeframe)
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol]
        if start_time is not None:
            conditions.append("open_time >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("open_time <= ?")
            params.append(end_time)
        where = " AND ".join(conditions)
        if limit:
            sql = f"""SELECT * FROM (
                SELECT open_time, open, high, low, close, volume, turnover
                FROM {table} WHERE {where} ORDER BY open_time DESC LIMIT {limit}
            ) ORDER BY open_time ASC"""
        else:
            sql = f"""SELECT open_time, open, high, low, close, volume, turnover
                FROM {table} WHERE {where} ORDER BY open_time ASC"""
        with self._connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_bar_count(self, symbol: str, timeframe: str) -> int:
        table = self._resolve_table(timeframe)
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else 0

    def get_bar_range(self, symbol: str, timeframe: str) -> Tuple[Optional[int], Optional[int]]:
        table = self._resolve_table(timeframe)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT MIN(open_time), MAX(open_time) FROM {table} WHERE symbol = ?", (symbol,)
            ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def upsert_ohlcv(self, symbol: str, timeframe: str, rows: List[Dict[str, Any]]) -> int:
        """Alias for upsert_bars — accepts (symbol, timeframe, rows) kwargs."""
        return self.upsert_bars(symbol=symbol, timeframe=timeframe, rows=rows)

    def upsert_products(self, products: List[Dict[str, Any]]) -> int:
        if not products:
            return 0
        sql = """INSERT OR REPLACE INTO products_master
            (symbol, base_coin, quote_coin, min_qty, qty_step,
             tick_size, min_notional, max_leverage, contract_type, updated_at)
            VALUES (:symbol, :base_coin, :quote_coin, :min_qty, :qty_step,
                    :tick_size, :min_notional, :max_leverage, :contract_type, :updated_at)"""
        with self._connect() as conn:
            conn.executemany(sql, products)
            conn.commit()
        return len(products)

    def get_product(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM products_master WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None

    def insert_signal(self, data: Dict[str, Any]) -> int:
        sql = """INSERT INTO signal_log
            (timestamp, strategy, symbol, direction, signal_strength, entry_price,
             stop_loss, take_profit, atr, reason, regime, indicators_snapshot)
            VALUES (:timestamp, :strategy, :symbol, :direction, :signal_strength,
                    :entry_price, :stop_loss, :take_profit, :atr, :reason,
                    :regime, :indicators_snapshot)"""
        defaults = {
            "signal_strength": None, "entry_price": None, "stop_loss": None,
            "take_profit": None, "atr": None, "reason": None,
            "regime": None, "indicators_snapshot": None,
        }
        row = {**defaults, **data}
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            conn.commit()
            return cursor.lastrowid

    def insert_trade_log(self, data: Dict[str, Any]) -> int:
        sql = """INSERT INTO trade_log
            (signal_id, strategy, symbol, direction, entry_time, exit_time,
             entry_price, exit_price, quantity, leverage, margin_used,
             gross_pnl, fee, slippage, net_pnl, exit_reason, holding_bars,
             max_favorable, max_adverse, notes, source)
            VALUES (:signal_id, :strategy, :symbol, :direction, :entry_time,
                    :exit_time, :entry_price, :exit_price, :quantity, :leverage,
                    :margin_used, :gross_pnl, :fee, :slippage, :net_pnl,
                    :exit_reason, :holding_bars, :max_favorable, :max_adverse,
                    :notes, :source)"""
        defaults = {
            "signal_id": None, "exit_time": None, "exit_price": None,
            "leverage": 3, "margin_used": None, "gross_pnl": None,
            "fee": None, "slippage": None, "net_pnl": None,
            "exit_reason": None, "holding_bars": None,
            "max_favorable": None, "max_adverse": None, "notes": None,
            "source": "STRATEGY",
        }
        row = {**defaults, **data}
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            conn.commit()
            return cursor.lastrowid

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trade_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_recent_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM signal_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── Funding Rate ────────────────────────────────────

    def get_funding_rates(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> pd.DataFrame:
        """펀딩비 데이터 조회.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            start_time: Unix ms 시작 시각
            end_time: Unix ms 종료 시각

        Returns:
            DataFrame with columns [funding_rate, funding_time], indexed by datetime
        """
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol]
        if start_time is not None:
            conditions.append("funding_time >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("funding_time <= ?")
            params.append(end_time)
        where = " AND ".join(conditions)
        sql = f"""
            SELECT funding_time, funding_rate
            FROM funding_rate
            WHERE {where}
            ORDER BY funding_time ASC
        """
        with self._connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
        return df

    def upsert_funding_rates(self, rows: List[Dict[str, Any]]) -> int:
        """펀딩비 데이터 저장.

        Args:
            rows: [{symbol, funding_rate, funding_time}, ...]

        Returns:
            저장된 행 수
        """
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO funding_rate (symbol, funding_rate, funding_time)
            VALUES (:symbol, :funding_rate, :funding_time)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            conn.commit()
        return cursor.rowcount

    # ── Open Interest ───────────────────────────────────

    def get_open_interest(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> pd.DataFrame:
        """OI 데이터 조회.

        Returns:
            DataFrame with columns [open_interest, open_interest_value, timestamp]
        """
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol]
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        where = " AND ".join(conditions)
        sql = f"""
            SELECT timestamp, open_interest, open_interest_value
            FROM open_interest
            WHERE {where}
            ORDER BY timestamp ASC
        """
        with self._connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce")
        return df

    def upsert_open_interest(self, rows: List[Dict[str, Any]]) -> int:
        """OI 데이터 저장.

        Args:
            rows: [{symbol, open_interest, open_interest_value, timestamp}, ...]

        Returns:
            저장된 행 수
        """
        if not rows:
            return 0
        sql = """
            INSERT OR IGNORE INTO open_interest
                (symbol, open_interest, open_interest_value, timestamp)
            VALUES
                (:symbol, :open_interest, :open_interest_value, :timestamp)
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            conn.commit()
        return cursor.rowcount


__all__ = ["DBManager", "TIMEFRAME_TABLE"]
