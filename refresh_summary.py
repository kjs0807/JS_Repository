"""
Refresh Bond_Auto.xlsx Summary (Daily) values from RAW data.

Single self-contained script that replaces the old Excel formula chain
(EWM_Levels, EWM_Spreads, KTB_Calculation, IRS_Calculation, BackData
formulas, Summary formulas) with a Python computation. After Bond_Auto
was stripped (intermediate sheets deleted), this is the only way to push
new daily numbers into the Summary (Daily) tab.

Reads (Bond_Auto.xlsx):
  데이터               KTB / KDB / 특수채(JN..) / IRS(KD..) / BOK / MSB
  선물내재수익률       3Y / 10Y futures implied yields with own date col

Computes:
  KTB     R6-12   level/vol/carry/total/adj
  Curve   R16-26  spread level/vol, DV01 hedge, steepener total/adj
  Futures R30-32  3Y/10Y MTM total, 3Y-10Y curve
  IRS     R36-43  level/vol/carry/roll/total/adj
  Swap    R47-54  KTB-IRS spread + paired total
  KDB     R58-60  KDB-IRS spread + paired total
  특수채   R63-68  특수채-IRS spread + paired total
  MSB     R73-77  KTB-MSB spread level/vol only (no carry per user spec)

Writes ONLY value cells in Summary (Daily) and BackData. All user
formatting (font/fill/border/CF/sparkline) is preserved.

Usage: just `python refresh_summary.py`. Designed to be wrapped in a .bat.
"""
import os, sys, time, shutil
import datetime as _dt
import numpy as np
import pandas as pd
import win32com.client as win32

# Force UTF-8 console output (Korean Windows defaults to cp949 which can't
# encode em-dash, ✓, etc.)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

BOND = r"C:\Python\Summary_Daily\Bond_Auto.xlsx"
WORK = r"C:\tmp\bond_refresh.xlsx"

# ---- Lookback offsets (business days) ----
LOOKBACK_LVL = [("Last", 0), ("2d", 2), ("5d", 5), ("10d", 10),
                ("20d", 20), ("3m", 63), ("6m", 126), ("12m", 252)]
LOOKBACK_VOL = [("Last", 0), ("5d", 5), ("10d", 10), ("20d", 20),
                ("3m", 63), ("6m", 126), ("12m", 252)]
SPARK_BD = 63
EWM_SPAN = 21
ALPHA = 2 / (EWM_SPAN + 1)

# ---- Cell layout (Summary (Daily) / BackData share same column layout) ----
LEVEL_COLS = list(range(2, 10))    # B..I  (Last, 2d, 5d, 10d, 20d, 3m, 6m, 12m)
VOL_COLS   = list(range(11, 18))   # K..Q  (Last, 5d, 10d, 20d, 3m, 6m, 12m)
HEDGE_COL  = 19   # S
TOTAL_COL  = 21   # U
ADJ_COL    = 22   # V
SPARK_LVL_FROM = 25   # Y..  (BackData only)
SPARK_VOL_FROM = 90   # CL.. (BackData only)


# ============================================================
#                       READ
# ============================================================

def col_to_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def read_cols(ws, last_row, cols_dict):
    """Bulk-read named columns by letter from row 4 to last_row."""
    out = {}
    for name, letter in cols_dict.items():
        c = col_to_num(letter)
        rng = ws.Range(ws.Cells(4, c), ws.Cells(last_row, c)).Value
        if isinstance(rng, tuple):
            out[name] = [r[0] if isinstance(r, tuple) else r for r in rng]
        else:
            out[name] = [rng]
    return out


def to_dt(v):
    if v is None: return pd.NaT
    if hasattr(v, "date"):
        return pd.Timestamp(v.date())
    if isinstance(v, str):
        try: return pd.Timestamp(v)
        except Exception: return pd.NaT
    return pd.NaT


def to_num(v):
    if v is None: return np.nan
    try: return float(v)
    except (TypeError, ValueError): return np.nan


# ============================================================
#                       COMPUTE
# ============================================================

def ewm_std(series_pct_or_bp, alpha=ALPHA):
    """EWM (zero-mean) std of first differences. Pass series in same unit
    as desired output unit (typically bp = pct * 100)."""
    diffs = series_pct_or_bp.diff()
    var = diffs.ewm(alpha=alpha, adjust=False).var(bias=True)
    return np.sqrt(var)


def lookback_value(series, off):
    s = series.dropna()
    idx = len(s) - 1 - off
    if idx < 0: return np.nan
    return float(s.iloc[idx])


def md_par(y_pct, T):
    """Modified duration of par bond, semi-annual coupon."""
    if pd.isna(y_pct) or y_pct <= 0: return np.nan
    return (1 - (1 + y_pct/200) ** (-2*T)) / (y_pct/100)


# ============================================================
#                       WRITE
# ============================================================

def safe_set(ws, r, c, v):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        ws.Cells(r, c).Value = None
    else:
        ws.Cells(r, c).Value = float(v)


def write_lookback_row(ws, row, level_series, vol_series):
    for i, (_, off) in enumerate(LOOKBACK_LVL):
        safe_set(ws, row, LEVEL_COLS[i], lookback_value(level_series, off))
    for i, (_, off) in enumerate(LOOKBACK_VOL):
        safe_set(ws, row, VOL_COLS[i], lookback_value(vol_series, off))


def write_sparkline_row(ws, row, level_series, vol_series):
    """Last 63 values to BackData sparkline data blocks."""
    lv = list(level_series.dropna().iloc[-SPARK_BD:].values)
    vv = list(vol_series.dropna().iloc[-SPARK_BD:].values)
    if len(lv) < SPARK_BD: lv = [None]*(SPARK_BD-len(lv)) + lv
    if len(vv) < SPARK_BD: vv = [None]*(SPARK_BD-len(vv)) + vv
    rng = ws.Range(ws.Cells(row, SPARK_LVL_FROM),
                    ws.Cells(row, SPARK_LVL_FROM + SPARK_BD - 1))
    rng.Value = tuple(None if v is None or (isinstance(v,float) and np.isnan(v))
                       else float(v) for v in lv)
    rng = ws.Range(ws.Cells(row, SPARK_VOL_FROM),
                    ws.Cells(row, SPARK_VOL_FROM + SPARK_BD - 1))
    rng.Value = tuple(None if v is None or (isinstance(v,float) and np.isnan(v))
                       else float(v) for v in vv)


# ============================================================
#                       MAIN
# ============================================================

def main():
    print("=" * 70)
    print("REFRESH SUMMARY — Bond_Auto.xlsx daily update")
    print("=" * 70)
    print("Killing leftover Excel...")
    os.system("taskkill /F /IM EXCEL.EXE >nul 2>&1")
    time.sleep(8)

    if os.path.exists(WORK):
        os.remove(WORK)
    print(f"Copy {BOND} → {WORK}")
    shutil.copy(BOND, WORK)

    excel = win32.DispatchEx("Excel.Application")
    excel.Visible = True
    excel.DisplayAlerts = False
    try: excel.AskToUpdateLinks = False
    except Exception: pass
    time.sleep(3)

    print("Opening...")
    wb = excel.Workbooks.Open(WORK, UpdateLinks=0,
                               IgnoreReadOnlyRecommended=True)
    if wb.ReadOnly:
        wb.SaveAs(WORK, FileFormat=51)
    time.sleep(5)

    data_ws = wb.Worksheets("데이터")
    fut_ws  = wb.Worksheets("선물내재수익률")
    sm = wb.Worksheets("Summary (Daily)")
    bd = wb.Worksheets("BackData")

    # Find last data rows
    last_data = 0
    for r in range(4, 5000):
        if data_ws.Cells(r, 1).Value is None: break
        last_data = r
    print(f"  데이터 last row: {last_data} (date={data_ws.Cells(last_data,1).Value})")

    last_fut = 0
    for r in range(4, 5000):
        if fut_ws.Cells(r, 1).Value is None: break
        last_fut = r
    print(f"  선물 last row: {last_fut} (date={fut_ws.Cells(last_fut,1).Value})")

    # ---- Load all RAW data ----
    print("Reading RAW columns...")
    raw = read_cols(data_ws, last_data, {
        # KTB — col A=date, B..P = 15 maturities
        "ktb_date":"A", "ktb_3m":"B", "ktb_6m":"C", "ktb_9m":"D",
        "ktb_1y":"E",  "ktb_18m":"F", "ktb_2y":"G", "ktb_30m":"H",
        "ktb_3y":"I",  "ktb_4y":"J",  "ktb_5y":"K", "ktb_7y":"L",
        "ktb_10y":"M", "ktb_15y":"N", "ktb_20y":"O","ktb_30y":"P",
        # MSB — no date col, cols BY..CF
        "msb_3m":"BY", "msb_6m":"BZ", "msb_9m":"CA", "msb_1y":"CB",
        "msb_18m":"CC","msb_2y":"CD", "msb_3y":"CF",
        # KDB — no date col, cols CN..CU
        "kdb_3m":"CN", "kdb_6m":"CO", "kdb_9m":"CP", "kdb_1y":"CQ",
        "kdb_18m":"CR","kdb_2y":"CS", "kdb_30m":"CT","kdb_3y":"CU",
        # 특수채(정부보증) — col JN=date
        "agc_date":"JN","agc_3m":"JO","agc_6m":"JP","agc_9m":"JQ",
        "agc_1y":"JR","agc_18m":"JS","agc_2y":"JT","agc_30m":"JU",
        "agc_3y":"JV",
        # IRS — col KD=date (offset from KTB), KE..KL
        "irs_date":"KD","irs_6m":"KE","irs_9m":"KF","irs_1y":"KG",
        "irs_18m":"KH","irs_2y":"KI","irs_3y":"KJ","irs_4y":"KK",
        "irs_5y":"KL",
        # BOK base
        "bok":"KS",
    })
    raw_fut = read_cols(fut_ws, last_fut, {
        "fut_date":"A", "impl_3y":"B", "impl_10y":"D",
    })

    # ---- Build pandas Series indexed by date (master = KTB date col) ----
    def to_series(date_key, val_key, raw_dict=None):
        rd = raw_dict or raw
        dates = [to_dt(d) for d in rd[date_key]]
        vals = [to_num(v) for v in rd[val_key]]
        s = pd.Series(vals, index=pd.DatetimeIndex(dates))
        s = s[~s.index.isna()]
        s = s[~s.index.duplicated(keep='last')]
        return s.sort_index()

    master_dates = pd.DatetimeIndex([to_dt(x) for x in raw["ktb_date"]])
    master_dates = master_dates.dropna().unique().sort_values()
    last_d = master_dates[-1]
    print(f"  Master date: {last_d.date()}")

    ktb_keys = ["ktb_3m","ktb_6m","ktb_9m","ktb_1y","ktb_18m","ktb_2y",
                 "ktb_30m","ktb_3y","ktb_4y","ktb_5y","ktb_7y","ktb_10y",
                 "ktb_15y","ktb_20y","ktb_30y"]
    msb_keys = ["msb_3m","msb_6m","msb_9m","msb_1y","msb_18m","msb_2y","msb_3y"]
    kdb_keys = ["kdb_3m","kdb_6m","kdb_9m","kdb_1y","kdb_18m","kdb_2y","kdb_30m","kdb_3y"]
    agc_keys = ["agc_3m","agc_6m","agc_9m","agc_1y","agc_18m","agc_2y","agc_30m","agc_3y"]
    irs_keys = ["irs_6m","irs_9m","irs_1y","irs_18m","irs_2y","irs_3y","irs_4y","irs_5y"]

    series = {}
    for k in ktb_keys + msb_keys + kdb_keys:
        # row-aligned with KTB date col
        series[k] = to_series("ktb_date", k).reindex(master_dates).ffill()
    for k in agc_keys:
        series[k] = to_series("agc_date", k).reindex(master_dates).ffill()
    for k in irs_keys:
        series[k] = to_series("irs_date", k).reindex(master_dates).ffill()
    # Futures
    series["impl_3y"]  = to_series("fut_date", "impl_3y", raw_fut).reindex(master_dates).ffill()
    series["impl_10y"] = to_series("fut_date", "impl_10y", raw_fut).reindex(master_dates).ffill()

    # Latest snapshot
    snap = {k: float(series[k].iloc[-1]) if not pd.isna(series[k].iloc[-1]) else np.nan
            for k in series}
    repo = to_num(raw["bok"][-1])
    cd = to_num(raw["msb_3m"][-1])
    if pd.isna(cd): cd = repo + 0.3
    print(f"  REPO={repo:.3f}, CD={cd:.3f}")

    # =========================================================
    #                  KTB R6-12  (BackData R4-10)
    # =========================================================
    print("\n[KTB] R6-12 ...")
    # (label, key, T_yrs, summary_row, backdata_row, target_low, target_high, weight)
    # Target = KTB at (T - 0.25)Y, interpolated between two adjacent tenors.
    KTB_ROWS = [
        ("1Y",  "ktb_1y", 1,  6, 4,  "ktb_9m",  None,       0),
        ("2Y",  "ktb_2y", 2,  7, 5,  "ktb_18m", "ktb_2y",   0.5),
        ("3Y",  "ktb_3y", 3,  8, 6,  "ktb_30m", "ktb_3y",   0.5),
        ("5Y",  "ktb_5y", 5,  9, 7,  "ktb_4y",  "ktb_5y",   0.75),
        ("10Y", "ktb_10y",10, 10, 8, "ktb_7y",  "ktb_10y",  33/36),
        ("20Y", "ktb_20y",20, 11, 9, "ktb_15y", "ktb_20y",  57/60),
        ("30Y", "ktb_30y",30, 12, 10,"ktb_20y", "ktb_30y",  117/120),
    ]
    ktb_total_by_T = {}
    for tlbl, key, T, sm_r, bd_r, low, high, w in KTB_ROWS:
        s_lvl = series[key]
        s_vol = ewm_std(s_lvl * 100)
        write_lookback_row(sm, sm_r, s_lvl, s_vol)
        write_lookback_row(bd, bd_r, s_lvl, s_vol)
        write_sparkline_row(bd, bd_r, s_lvl, s_vol)
        # carry & roll
        y = snap[key]
        if high is None: y_minus = snap[low]
        else: y_minus = snap[low] * (1-w) + snap[high] * w
        md = md_par(y, T)
        carry = (y - repo) * 0.25 * 100 if not pd.isna(y) else np.nan
        roll = -md * (y_minus - y) * 100 if not pd.isna(md) else 0.0
        total = carry + roll
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        # Hedge for outright KTB = 1 (no hedge concept)
        safe_set(sm, sm_r, HEDGE_COL, 1.0)
        safe_set(sm, sm_r, TOTAL_COL, total)
        safe_set(sm, sm_r, ADJ_COL,   adj)
        safe_set(bd, bd_r, HEDGE_COL, 1.0)
        safe_set(bd, bd_r, TOTAL_COL, total)
        safe_set(bd, bd_r, ADJ_COL,   adj)
        ktb_total_by_T[T] = total
        print(f"  {tlbl:>4}: y={y:.3f}, MD={md:.3f}, total={total:+.2f}, adj={adj:+.2f}")

    # =========================================================
    #                  Curve R16-26  (BackData R12-22)
    # =========================================================
    print("\n[Curve] R16-26 ...")
    # Steepener: hedge × short_total - long_total, hedge = MD_long / MD_short
    CURVE_PAIRS = [
        ("1-2",   1,  2,  16, 12),
        ("1-3",   1,  3,  17, 13),
        ("2-3",   2,  3,  18, 14),
        ("2-5",   2,  5,  19, 15),
        ("3-5",   3,  5,  20, 16),
        ("3-10",  3, 10,  21, 17),
        ("5-10",  5, 10,  22, 18),
        ("5-30",  5, 30,  23, 19),
        ("10-20",10, 20,  24, 20),
        ("10-30",10, 30,  25, 21),
        ("20-30",20, 30,  26, 22),
    ]
    KTB_TENOR_KEY = {1:"ktb_1y",2:"ktb_2y",3:"ktb_3y",5:"ktb_5y",
                      10:"ktb_10y",20:"ktb_20y",30:"ktb_30y"}
    for label, ts, tl, sm_r, bd_r in CURVE_PAIRS:
        s_lvl = (series[KTB_TENOR_KEY[tl]] - series[KTB_TENOR_KEY[ts]]) * 100
        s_vol = ewm_std(s_lvl)
        write_lookback_row(sm, sm_r, s_lvl, s_vol)
        write_lookback_row(bd, bd_r, s_lvl, s_vol)
        write_sparkline_row(bd, bd_r, s_lvl, s_vol)
        md_s = md_par(snap[KTB_TENOR_KEY[ts]], ts)
        md_l = md_par(snap[KTB_TENOR_KEY[tl]], tl)
        hedge = md_l / md_s if md_s else 1.0
        total = hedge * ktb_total_by_T[ts] - ktb_total_by_T[tl]
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        safe_set(sm, sm_r, HEDGE_COL, hedge)
        safe_set(sm, sm_r, TOTAL_COL, total)
        safe_set(sm, sm_r, ADJ_COL,   adj)
        safe_set(bd, bd_r, HEDGE_COL, hedge)
        safe_set(bd, bd_r, TOTAL_COL, total)
        safe_set(bd, bd_r, ADJ_COL,   adj)
        # Restore curve label string (col A) — Excel sometimes auto-converts
        sm.Cells(sm_r, 1).NumberFormat = "@"
        sm.Cells(sm_r, 1).Value = label
        print(f"  {label:>5}: hedge={hedge:.3f}, total={total:+.2f}, adj={adj:+.2f}")

    # =========================================================
    #                Futures R30-32  (BackData R24-26)
    # =========================================================
    print("\n[Futures] R30-32 ...")
    impl_3y_now = snap["impl_3y"]
    impl_10y_now = snap["impl_10y"]
    md_3y_fut  = md_par(impl_3y_now, 3)
    md_10y_fut = md_par(impl_10y_now, 10)
    # 3Y target = KTB at 33m (interp 30m & 36m, w=0.5)
    y_minus_3y = (snap["ktb_30m"] + snap["ktb_3y"]) / 2
    total_3y_fut = -md_3y_fut * (y_minus_3y - impl_3y_now) * 100
    # 10Y target = KTB at 117m (interp 7Y & 10Y, w=33/36)
    w = 33/36
    y_minus_10y = snap["ktb_7y"] * (1-w) + snap["ktb_10y"] * w
    total_10y_fut = -md_10y_fut * (y_minus_10y - impl_10y_now) * 100
    hedge_curve_fut = md_10y_fut / md_3y_fut
    total_curve_fut = hedge_curve_fut * total_3y_fut - total_10y_fut

    # Futures level series for sparklines (use implied yield)
    for label, key, sm_r, bd_r, total_v, hedge_v in [
        ("3Y",  "impl_3y",  30, 24, total_3y_fut,  1.0),
        ("10Y", "impl_10y", 31, 25, total_10y_fut, 1.0),
    ]:
        s_lvl = series[key]
        s_vol = ewm_std(s_lvl * 100)
        write_lookback_row(sm, sm_r, s_lvl, s_vol)
        write_lookback_row(bd, bd_r, s_lvl, s_vol)
        write_sparkline_row(bd, bd_r, s_lvl, s_vol)
        std_now = lookback_value(s_vol, 0)
        adj = total_v / std_now if std_now and std_now > 0 else np.nan
        safe_set(sm, sm_r, HEDGE_COL, hedge_v)
        safe_set(sm, sm_r, TOTAL_COL, total_v)
        safe_set(sm, sm_r, ADJ_COL,   adj)
        safe_set(bd, bd_r, HEDGE_COL, hedge_v)
        safe_set(bd, bd_r, TOTAL_COL, total_v)
        safe_set(bd, bd_r, ADJ_COL,   adj)
    # 3Y-10Y curve (R32 / BD R26)
    s_curve = (series["impl_10y"] - series["impl_3y"]) * 100
    s_vol_c = ewm_std(s_curve)
    write_lookback_row(sm, 32, s_curve, s_vol_c)
    write_lookback_row(bd, 26, s_curve, s_vol_c)
    write_sparkline_row(bd, 26, s_curve, s_vol_c)
    std_now_c = lookback_value(s_vol_c, 0)
    adj_c = total_curve_fut / std_now_c if std_now_c and std_now_c > 0 else np.nan
    safe_set(sm, 32, HEDGE_COL, hedge_curve_fut)
    safe_set(sm, 32, TOTAL_COL, total_curve_fut)
    safe_set(sm, 32, ADJ_COL,   adj_c)
    safe_set(bd, 26, HEDGE_COL, hedge_curve_fut)
    safe_set(bd, 26, TOTAL_COL, total_curve_fut)
    safe_set(bd, 26, ADJ_COL,   adj_c)
    print(f"  3Y :  total={total_3y_fut:+.2f}")
    print(f"  10Y:  total={total_10y_fut:+.2f}")
    print(f"  3-10: hedge={hedge_curve_fut:.3f}, total={total_curve_fut:+.2f}")

    # =========================================================
    #                  IRS R36-43  (BackData R28-35)
    # =========================================================
    print("\n[IRS] R36-43 ...")
    irs_T_yrs = {"6m":0.5,"9m":0.75,"1Y":1,"1.5Y":1.5,"2Y":2,
                  "3Y":3,"4Y":4,"5Y":5}
    irs_tenor_to_key = {"6m":"irs_6m","9m":"irs_9m","1Y":"irs_1y",
                         "1.5Y":"irs_18m","2Y":"irs_2y","3Y":"irs_3y",
                         "4Y":"irs_4y","5Y":"irs_5y"}
    irs_roll_targets = {
        "6m":  ("cd",   None,        0),
        "9m":  ("irs_6m",None,       0),
        "1Y":  ("irs_9m",None,       0),
        "1.5Y":("irs_1y","irs_18m",  0.5),
        "2Y":  ("irs_18m","irs_2y",  0.5),
        "3Y":  ("irs_2y","irs_3y",   0.75),
        "4Y":  ("irs_3y","irs_4y",   0.75),
        "5Y":  ("irs_4y","irs_5y",   0.75),
    }
    irs_totals = {}
    for i, tlbl in enumerate(["6m","9m","1Y","1.5Y","2Y","3Y","4Y","5Y"]):
        bd_r = 28 + i
        sm_r = 36 + i
        s_lvl = series[irs_tenor_to_key[tlbl]]
        s_vol = ewm_std(s_lvl * 100)
        write_lookback_row(sm, sm_r, s_lvl, s_vol)
        write_lookback_row(bd, bd_r, s_lvl, s_vol)
        write_sparkline_row(bd, bd_r, s_lvl, s_vol)
        y = snap[irs_tenor_to_key[tlbl]]
        T = irs_T_yrs[tlbl]
        low, high, w = irs_roll_targets[tlbl]
        if low == "cd":      y_minus = cd
        elif high is None:    y_minus = snap[low]
        else:                 y_minus = snap[low]*(1-w) + snap[high]*w
        md = md_par(y, T)
        carry = (y - cd) * 0.25 * 100 if not pd.isna(y) else np.nan
        roll = -md * (y_minus - y) * 100 if not pd.isna(md) else 0
        total = carry + roll
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        irs_totals[tlbl] = total
        safe_set(sm, sm_r, TOTAL_COL, total); safe_set(sm, sm_r, ADJ_COL, adj)
        safe_set(bd, bd_r, TOTAL_COL, total); safe_set(bd, bd_r, ADJ_COL, adj)

    # =========================================================
    #                  Swap R47-54  (BackData R37-44)
    # =========================================================
    print("\n[Swap] R47-54 ...")
    swap_pairs = [("6m","ktb_6m"),("9m","ktb_9m"),("1Y","ktb_1y"),
                   ("1.5Y","ktb_18m"),("2Y","ktb_2y"),("3Y","ktb_3y"),
                   ("4Y","ktb_4y"),("5Y","ktb_5y")]
    ktb_roll_map = {"6m":("ktb_3m",None,0),"9m":("ktb_6m",None,0),
                    "1Y":("ktb_9m",None,0),"1.5Y":("ktb_1y","ktb_18m",0.5),
                    "2Y":("ktb_18m","ktb_2y",0.5),"3Y":("ktb_30m","ktb_3y",0.5),
                    "4Y":("ktb_3y","ktb_4y",0.75),"5Y":("ktb_4y","ktb_5y",0.75)}
    for i, (tlbl, ktb_k) in enumerate(swap_pairs):
        bd_r = 37 + i
        sm_r = 47 + i
        irs_k = irs_tenor_to_key[tlbl]
        spread = (series[ktb_k] - series[irs_k]) * 100
        s_vol = ewm_std(spread)
        write_lookback_row(sm, sm_r, spread, s_vol)
        write_lookback_row(bd, bd_r, spread, s_vol)
        write_sparkline_row(bd, bd_r, spread, s_vol)
        T = irs_T_yrs[tlbl]
        ktb_y = snap[ktb_k]; ktb_md = md_par(ktb_y, T)
        irs_md = md_par(snap[irs_k], T)
        hedge = 1.0 if T < 3 else (ktb_md / irs_md if irs_md else 1.0)
        low, high, w = ktb_roll_map[tlbl]
        if high is None: y_minus = snap[low]
        else:            y_minus = snap[low]*(1-w) + snap[high]*w
        ktb_carry = (ktb_y - repo) * 0.25 * 100
        ktb_roll = -ktb_md * (y_minus - ktb_y) * 100 if not pd.isna(ktb_md) else 0
        ktb_tot = ktb_carry + ktb_roll
        total = ktb_tot - hedge * irs_totals[tlbl]
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        if i == 0:
            safe_set(sm, sm_r, HEDGE_COL, hedge)
            safe_set(bd, bd_r, HEDGE_COL, hedge)
        safe_set(sm, sm_r, TOTAL_COL, total); safe_set(sm, sm_r, ADJ_COL, adj)
        safe_set(bd, bd_r, TOTAL_COL, total); safe_set(bd, bd_r, ADJ_COL, adj)

    # =========================================================
    #                  KDB-IRS R58-60  (BackData R46-48)
    # =========================================================
    print("\n[KDB-IRS] R58-60 ...")
    kdb_rows = [("1Y","kdb_1y","irs_1y", 1.0, 46, 58),
                 ("2Y","kdb_2y","irs_2y", 2.0, 47, 59),
                 ("3Y","kdb_3y","irs_3y", 3.0, 48, 60)]
    kdb_roll_map = {1.0:("kdb_9m",None,0), 2.0:("kdb_18m","kdb_2y",0.5),
                     3.0:("kdb_30m","kdb_3y",0.5)}
    for tlbl, kk, ik, T, bd_r, sm_r in kdb_rows:
        spread = (series[kk] - series[ik]) * 100
        s_vol = ewm_std(spread)
        write_lookback_row(sm, sm_r, spread, s_vol)
        write_lookback_row(bd, bd_r, spread, s_vol)
        write_sparkline_row(bd, bd_r, spread, s_vol)
        ag_y = snap[kk]; ag_md = md_par(ag_y, T)
        low, high, w = kdb_roll_map[T]
        if high is None: y_minus = snap[low]
        else:            y_minus = snap[low]*(1-w) + snap[high]*w
        ag_carry = (ag_y - repo) * 0.25 * 100
        ag_roll = -ag_md * (y_minus - ag_y) * 100 if not pd.isna(ag_md) else 0
        ag_total = ag_carry + ag_roll
        irs_md = md_par(snap[ik], T)
        hedge = 1.0 if T < 3 else (ag_md / irs_md if irs_md else 1.0)
        irs_lbl = "1Y" if T == 1.0 else ("2Y" if T == 2.0 else "3Y")
        total = ag_total - hedge * irs_totals[irs_lbl]
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        safe_set(sm, sm_r, HEDGE_COL, hedge)
        safe_set(sm, sm_r, TOTAL_COL, total); safe_set(sm, sm_r, ADJ_COL, adj)
        safe_set(bd, bd_r, HEDGE_COL, hedge)
        safe_set(bd, bd_r, TOTAL_COL, total); safe_set(bd, bd_r, ADJ_COL, adj)

    # =========================================================
    #                특수채-IRS R63-68  (BackData R52-57)
    # =========================================================
    print("\n[특수채-IRS] R63-68 ...")
    spc_rows = [("6m","agc_6m","irs_6m", 0.5, 52, 63),
                 ("9m","agc_9m","irs_9m", 0.75, 53, 64),
                 ("1Y","agc_1y","irs_1y", 1.0, 54, 65),
                 ("1.5Y","agc_18m","irs_18m", 1.5, 55, 66),
                 ("2Y","agc_2y","irs_2y", 2.0, 56, 67),
                 ("3Y","agc_3y","irs_3y", 3.0, 57, 68)]
    spc_roll_map = {0.5:("ktb_3m",None,0), 0.75:("agc_6m",None,0),
                     1.0:("agc_9m",None,0), 1.5:("agc_1y","agc_18m",0.5),
                     2.0:("agc_18m","agc_2y",0.5), 3.0:("agc_2y","agc_3y",0.75)}
    for tlbl, ak, ik, T, bd_r, sm_r in spc_rows:
        spread = (series[ak] - series[ik]) * 100
        s_vol = ewm_std(spread)
        write_lookback_row(sm, sm_r, spread, s_vol)
        write_lookback_row(bd, bd_r, spread, s_vol)
        write_sparkline_row(bd, bd_r, spread, s_vol)
        ag_y = snap[ak]; ag_md = md_par(ag_y, T)
        low, high, w = spc_roll_map[T]
        if high is None: y_minus = snap[low]
        else:            y_minus = snap[low]*(1-w) + snap[high]*w
        ag_carry = (ag_y - repo) * 0.25 * 100
        ag_roll = -ag_md * (y_minus - ag_y) * 100 if not pd.isna(ag_md) else 0
        ag_total = ag_carry + ag_roll
        irs_md = md_par(snap[ik], T)
        hedge = 1.0 if T < 3 else (ag_md / irs_md if irs_md else 1.0)
        irs_tlbl = tlbl
        total = ag_total - hedge * irs_totals[irs_tlbl]
        std_now = lookback_value(s_vol, 0)
        adj = total / std_now if std_now and std_now > 0 else np.nan
        safe_set(sm, sm_r, HEDGE_COL, hedge)
        safe_set(sm, sm_r, TOTAL_COL, total); safe_set(sm, sm_r, ADJ_COL, adj)
        safe_set(bd, bd_r, HEDGE_COL, hedge)
        safe_set(bd, bd_r, TOTAL_COL, total); safe_set(bd, bd_r, ADJ_COL, adj)

    # =========================================================
    #                  MSB R73-77  (BackData R61-65)  level/vol only
    # =========================================================
    print("\n[MSB] R73-77 ...")
    msb_rows = [("6m","ktb_6m","msb_6m", 73, 61),
                ("9m","ktb_9m","msb_9m", 74, 62),
                ("1Y","ktb_1y","msb_1y", 75, 63),
                ("1.5Y","ktb_18m","msb_18m", 76, 64),
                ("2Y","ktb_2y","msb_2y", 77, 65)]
    for tlbl, kk, mk, sm_r, bd_r in msb_rows:
        spread = (series[kk] - series[mk]) * 100
        s_vol = ewm_std(spread)
        write_lookback_row(sm, sm_r, spread, s_vol)
        write_lookback_row(bd, bd_r, spread, s_vol)
        write_sparkline_row(bd, bd_r, spread, s_vol)
        # No carry per user spec — clear carry/adj cells
        safe_set(sm, sm_r, TOTAL_COL, None); safe_set(sm, sm_r, ADJ_COL, None)
        safe_set(bd, bd_r, TOTAL_COL, None); safe_set(bd, bd_r, ADJ_COL, None)

    # ---- Save ----
    print("\nSaving...")
    wb.Save()
    wb.Close(False)
    excel.Quit()
    time.sleep(3)

    print(f"Copy {WORK} → {BOND}")
    shutil.copy(WORK, BOND)

    new_size = os.path.getsize(BOND) / 1024 / 1024
    print(f"\nDone. Bond_Auto.xlsx size: {new_size:.1f} MB")


if __name__ == "__main__":
    main()
