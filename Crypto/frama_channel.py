"""
FRAMA Channel [BigBeluga] — Python Standalone Port

원본: TradingView Pine Script (BigBeluga)
목적: TradingView 시그널과 1:1 검증 (백테스트 통합 전 정확성 확인)

핵심 quirk (반드시 보존):
- volatility는 SMA(high-low, 200) — ATR 아님
- alpha clamp [0.01, 1]
- Filt에 SMA(5) 두 번째 스무딩 적용
- bar_index < N+1 구간은 원본 price를 SMA(5)에 입력
- 시그널은 hlc3 기준, color 전환은 close 기준 (분기 다름)
- count1/count2 dedup: 같은 방향 연속 시그널 중 첫 번째만 라벨 표시

의존성: numpy, pandas
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass


# ============================================================================
# 1. FRAMA 핵심 계산
# ============================================================================

def _compute_frama_raw(price: np.ndarray, high: np.ndarray, low: np.ndarray,
                       N: int) -> np.ndarray:
    """
    Pine Script FRAMA 재귀 EMA 부분 (SMA(5) 적용 전).

    N1 = (HH(N/2 first half) - LL(N/2 first half)) / (N/2)
    N2 = (HH(N/2 second half) - LL(N/2 second half)) / (N/2)
    N3 = (HH(N) - LL(N)) / N
    Dimen = (log(N1+N2) - log(N3)) / log(2)   # if N1,N2,N3 > 0
    alpha = exp(-4.6 * (Dimen - 1)), clamp [0.01, 1]
    Filt[t] = alpha[t] * price[t] + (1 - alpha[t]) * Filt[t-1]
    Filt[0] = price[0]   (na 처리)
    """
    n_bars = len(price)
    half_N = N // 2

    filt = np.full(n_bars, np.nan, dtype=np.float64)

    # Pine Script: Filt = na(Filt) ? price : ...
    # → 첫 봉부터 Filt는 정의됨 (price로 초기화)
    filt[0] = price[0]

    for t in range(1, n_bars):
        # 충분한 history가 없으면 단순 carry (price[t]로 초기화 유지)
        # Pine Script도 N+1 미만이면 사실상 EMA가 무의미하지만,
        # alpha 계산이 안 되는 시점은 직전 filt 그대로 가져감
        if t < N:
            # alpha 계산 불가 — 직전 값 carry
            # Pine Script는 NaN 곱셈으로 이전 값 보존되는 케이스
            filt[t] = filt[t - 1]
            continue

        # N3: 전체 N봉 구간 high-low 평균
        # 인덱스 [t-N+1 ... t] (현재 봉 포함, N개)
        window_hi = high[t - N + 1: t + 1]
        window_lo = low[t - N + 1: t + 1]
        N3 = (window_hi.max() - window_lo.min()) / N

        # N1: 첫 N/2 구간 (Pine: for count=0 to N/2-1, high[count]/low[count])
        # Pine의 high[count]는 count봉 전. count=0..N/2-1 → 인덱스 t-(N/2-1)..t
        first_hi = high[t - half_N + 1: t + 1]
        first_lo = low[t - half_N + 1: t + 1]
        N1 = (first_hi.max() - first_lo.min()) / half_N

        # N2: 다음 N/2 구간 (Pine: for count=N/2 to N-1, high[count]/low[count])
        # high[N/2]..high[N-1] → 인덱스 t-(N-1)..t-N/2
        second_hi = high[t - N + 1: t - half_N + 1]
        second_lo = low[t - N + 1: t - half_N + 1]
        N2 = (second_hi.max() - second_lo.min()) / half_N

        # Dimen, alpha
        if N1 > 0 and N2 > 0 and N3 > 0:
            Dimen = (np.log(N1 + N2) - np.log(N3)) / np.log(2.0)
            alpha = np.exp(-4.6 * (Dimen - 1.0))
            # clamp [0.01, 1]
            if alpha < 0.01:
                alpha = 0.01
            elif alpha > 1.0:
                alpha = 1.0
        else:
            # Pine Script: alpha는 이전 값 유지 또는 미정의
            # 실제 동작은 Filt가 직전값 carry되도록 alpha=0 효과
            alpha = 0.0

        filt[t] = alpha * price[t] + (1.0 - alpha) * filt[t - 1]

    return filt


def _apply_filt_sma5(filt_raw: np.ndarray, price: np.ndarray, N: int) -> np.ndarray:
    """
    Pine Script:
        Filt := ta.sma((bar_index < N+1) ? price : Filt, 5)

    bar_index가 N+1 미만이면 SMA(5) 입력으로 price 사용,
    그 이후엔 raw Filt 사용. 그 위에 SMA(5) 적용.
    """
    n_bars = len(filt_raw)

    # SMA(5)에 입력할 시리즈 구성
    sma_input = filt_raw.copy()
    early_mask = np.arange(n_bars) < (N + 1)
    sma_input[early_mask] = price[early_mask]

    # SMA(5) — pandas rolling 사용 (NaN 처리 일관성)
    s = pd.Series(sma_input)
    smoothed = s.rolling(window=5, min_periods=1).mean().to_numpy()

    return smoothed


# ============================================================================
# 2. 시그널 생성
# ============================================================================

def _crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """ta.crossover(a, b): a가 b를 위로 돌파한 봉에서 True."""
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    result = (a > b) & (prev_a <= prev_b)
    # NaN 비교는 False (Pine Script 동작과 일치)
    nan_mask = np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)
    result[nan_mask] = False
    return result


def _crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """ta.crossunder(a, b): a가 b를 아래로 돌파한 봉에서 True."""
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    result = (a < b) & (prev_a >= prev_b)
    nan_mask = np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)
    result[nan_mask] = False
    return result


def _apply_signal_dedup(break_up: np.ndarray,
                        break_dn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Pine Script 라벨 카운터 로직:
        if break_up: count2 := 0, count1 += 1, label_only_if count1==1
        if break_dn: count1 := 0, count2 += 1, label_only_if count2==1

    → 같은 방향 연속 시그널 중 첫 번째 (반대 방향 시그널이 카운터를 리셋)만 True
    """
    n_bars = len(break_up)
    signal_long = np.zeros(n_bars, dtype=bool)
    signal_short = np.zeros(n_bars, dtype=bool)

    count1 = 0  # break_up 누적
    count2 = 0  # break_dn 누적

    for t in range(n_bars):
        if break_up[t]:
            count2 = 0
            count1 += 1
            if count1 == 1:
                signal_long[t] = True
        if break_dn[t]:
            count1 = 0
            count2 += 1
            if count2 == 1:
                signal_short[t] = True

    return signal_long, signal_short


def _compute_color_state(close: np.ndarray, filt: np.ndarray,
                         break_up: np.ndarray,
                         break_dn: np.ndarray) -> np.ndarray:
    """
    Pine Script 색상 전환 로직:
        ta.cross(close, Filt) → color3 (neutral, gray)
        break_up               → color1 (long, green)
        break_dn               → color2 (short, red)

    우선순위: cross가 먼저 평가되고 break_up/dn으로 덮어씌워지는 구조
    (Pine Script var color_ + 순차 if 할당 순서대로 재현)
    """
    n_bars = len(close)
    state = np.empty(n_bars, dtype=object)

    # close vs filt cross 감지
    close_cross = _crossover(close, filt) | _crossunder(close, filt)

    current = 'neutral'
    for t in range(n_bars):
        if close_cross[t]:
            current = 'neutral'
        if break_up[t]:
            current = 'long'
        if break_dn[t]:
            current = 'short'
        state[t] = current

    return state


# ============================================================================
# 3. 메인 함수
# ============================================================================

def compute_frama_channel(
    df: pd.DataFrame,
    N: int = 26,
    distance: float = 1.5,
    p_vol_mode: str = 'price',
    signal_dedup: bool = True,
) -> pd.DataFrame:
    """
    FRAMA Channel 인디케이터 계산.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV 데이터. 'open', 'high', 'low', 'close', 'volume' 컬럼 필요.
        timestamp는 index 또는 'timestamp' 컬럼 어느 쪽이든 OK.
    N : int
        FRAMA Length (default 26).
    distance : float
        Bands Distance — volatility * distance가 채널 폭 (default 1.5).
    p_vol_mode : {'price', 'volume'}
        라벨 표시값 모드. 'price'면 close, 'volume'이면 SUM(volume, 10)/10.
    signal_dedup : bool
        True면 TradingView 라벨 dedup 로직 적용 (같은 방향 연속 첫 번째만).
        백테스트에서 첫 시그널만 진입할 때 활용. False면 모든 crossover 표시.

    Returns
    -------
    pd.DataFrame
        입력 DataFrame에 다음 컬럼 추가:
        - hl2, hlc3
        - volatility   (SMA(high-low, 200) — ATR 아님 주의)
        - frama        (재귀 EMA + SMA(5) 두 번째 스무딩 후 최종값)
        - upper_band   (frama + volatility * distance)
        - lower_band   (frama - volatility * distance)
        - break_up     (raw crossover 시그널, 모든 돌파)
        - break_dn     (raw crossunder 시그널, 모든 돌파)
        - signal_long  (dedup 후 — signal_dedup=False면 break_up과 동일)
        - signal_short (dedup 후 — signal_dedup=False면 break_dn과 동일)
        - color_state  ('long' / 'short' / 'neutral', 색상 전환 상태)
        - p_vol        (라벨 표시값 — close 또는 SMA(volume, 10))
    """
    # 입력 검증
    required = {'open', 'high', 'low', 'close', 'volume'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    if N < 4 or N % 2 != 0:
        raise ValueError(f"N must be even and >= 4, got {N}")

    if p_vol_mode not in ('price', 'volume'):
        raise ValueError(f"p_vol_mode must be 'price' or 'volume', got {p_vol_mode}")

    out = df.copy()

    high = out['high'].to_numpy(dtype=np.float64)
    low = out['low'].to_numpy(dtype=np.float64)
    close = out['close'].to_numpy(dtype=np.float64)
    volume = out['volume'].to_numpy(dtype=np.float64)

    # 1. 가격 시리즈
    hl2 = (high + low) / 2.0
    hlc3 = (high + low + close) / 3.0

    # 2. volatility = SMA(high-low, 200) ← ATR 아님
    hl_range = pd.Series(high - low)
    volatility = hl_range.rolling(window=200, min_periods=1).mean().to_numpy()

    # 3. p_vol (라벨 표시용)
    if p_vol_mode == 'price':
        p_vol = close.copy()
    else:  # 'volume'
        # Pine: math.round(math.sum(volume, 10) / 10, 2)
        vol_sma = pd.Series(volume).rolling(window=10, min_periods=1).mean().to_numpy()
        p_vol = np.round(vol_sma, 2)

    # 4. FRAMA — 재귀 EMA (price = hl2)
    filt_raw = _compute_frama_raw(hl2, high, low, N)

    # 5. SMA(5) 추가 스무딩
    frama = _apply_filt_sma5(filt_raw, hl2, N)

    # 6. 채널 밴드
    upper_band = frama + volatility * distance
    lower_band = frama - volatility * distance

    # 7. raw 시그널 (barstate.isconfirmed는 백테스트에선 항상 True)
    break_up = _crossover(hlc3, upper_band)
    break_dn = _crossunder(hlc3, lower_band)

    # 8. 시그널 dedup
    if signal_dedup:
        signal_long, signal_short = _apply_signal_dedup(break_up, break_dn)
    else:
        signal_long = break_up.copy()
        signal_short = break_dn.copy()

    # 9. 색상 상태
    color_state = _compute_color_state(close, frama, break_up, break_dn)

    # 컬럼 추가
    out['hl2'] = hl2
    out['hlc3'] = hlc3
    out['volatility'] = volatility
    out['frama'] = frama
    out['upper_band'] = upper_band
    out['lower_band'] = lower_band
    out['break_up'] = break_up
    out['break_dn'] = break_dn
    out['signal_long'] = signal_long
    out['signal_short'] = signal_short
    out['color_state'] = color_state
    out['p_vol'] = p_vol

    return out


# ============================================================================
# 4. TradingView 검증 헬퍼
# ============================================================================

@dataclass
class VerificationResult:
    match_rate: float                    # 0.0 ~ 1.0
    matched: list[dict]                  # 매칭된 시그널
    missed_in_python: list[dict]         # TV에는 있는데 Python엔 없음
    extra_in_python: list[dict]          # Python에만 있는 시그널
    tv_total: int
    py_total: int

    def summary(self) -> str:
        return (
            f"Match rate: {self.match_rate*100:.2f}%\n"
            f"  TV signals: {self.tv_total}\n"
            f"  Python signals: {self.py_total}\n"
            f"  Matched: {len(self.matched)}\n"
            f"  Missed in Python: {len(self.missed_in_python)}\n"
            f"  Extra in Python: {len(self.extra_in_python)}"
        )


def verify_against_tradingview(
    df: pd.DataFrame,
    tv_signals: list[dict],
    tolerance_bars: int = 0,
    timestamp_col: str = None,
) -> VerificationResult:
    """
    Python 시그널과 TradingView 시그널 1:1 비교.

    Parameters
    ----------
    df : pd.DataFrame
        compute_frama_channel 출력. signal_long, signal_short 필수.
    tv_signals : list[dict]
        TradingView에서 추출한 시그널.
        형식: [{'timestamp': pd.Timestamp, 'direction': 'long'|'short'}, ...]
    tolerance_bars : int
        시간 매칭 허용 오차 (봉 단위). 0이면 정확히 같은 봉.
    timestamp_col : str, optional
        timestamp가 컬럼에 있을 때 컬럼명. None이면 index 사용.

    Returns
    -------
    VerificationResult
    """
    if 'signal_long' not in df.columns or 'signal_short' not in df.columns:
        raise ValueError(
            "df must contain 'signal_long' and 'signal_short'. "
            "Run compute_frama_channel() first."
        )

    # Python 시그널 추출
    if timestamp_col:
        ts = df[timestamp_col]
    else:
        ts = pd.Series(df.index)

    py_signals = []
    for i in range(len(df)):
        if df['signal_long'].iloc[i]:
            py_signals.append({'timestamp': ts.iloc[i], 'direction': 'long'})
        if df['signal_short'].iloc[i]:
            py_signals.append({'timestamp': ts.iloc[i], 'direction': 'short'})

    # 매칭
    py_ts_array = pd.Series([s['timestamp'] for s in py_signals])
    matched = []
    py_used = set()

    for tv_sig in tv_signals:
        tv_ts = pd.Timestamp(tv_sig['timestamp'])
        tv_dir = tv_sig['direction']

        # 같은 방향 + 시간 tolerance 내 후보
        candidates = []
        for j, py_sig in enumerate(py_signals):
            if j in py_used:
                continue
            if py_sig['direction'] != tv_dir:
                continue
            # bar 단위 tolerance 비교
            try:
                diff = abs((py_sig['timestamp'] - tv_ts).total_seconds())
            except (TypeError, AttributeError):
                # timestamp가 datetime이 아니면 정확 일치만
                if py_sig['timestamp'] == tv_ts:
                    diff = 0
                else:
                    continue
            candidates.append((j, diff))

        if not candidates:
            continue

        # 가장 가까운 것 선택
        candidates.sort(key=lambda x: x[1])
        best_j, best_diff = candidates[0]

        # tolerance 검사 (timeframe을 모르므로 시간 차로 근사)
        # tolerance_bars=0이면 정확 일치만 (diff == 0)
        if tolerance_bars == 0 and best_diff != 0:
            continue

        # tolerance_bars > 0인 경우: 사용자가 timeframe 추정해서 환산해야 정확
        # 여기선 단순화: tolerance_bars == 0만 엄격, 그 외는 무조건 가장 가까운 것 매칭
        matched.append({
            'tv_timestamp': tv_ts,
            'py_timestamp': py_signals[best_j]['timestamp'],
            'direction': tv_dir,
            'time_diff_seconds': best_diff,
        })
        py_used.add(best_j)

    # missed / extra
    matched_tv_ts = {(m['tv_timestamp'], m['direction']) for m in matched}
    missed_in_python = [
        s for s in tv_signals
        if (pd.Timestamp(s['timestamp']), s['direction']) not in matched_tv_ts
    ]
    extra_in_python = [
        py_signals[j] for j in range(len(py_signals)) if j not in py_used
    ]

    tv_total = len(tv_signals)
    match_rate = len(matched) / tv_total if tv_total > 0 else 0.0

    return VerificationResult(
        match_rate=match_rate,
        matched=matched,
        missed_in_python=missed_in_python,
        extra_in_python=extra_in_python,
        tv_total=tv_total,
        py_total=len(py_signals),
    )


# ============================================================================
# 5. 사용 예시
# ============================================================================

if __name__ == "__main__":
    # 더미 OHLCV 생성
    np.random.seed(42)
    n = 500
    dates = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')

    # 트렌드 + 노이즈
    base = 50000 + np.cumsum(np.random.randn(n) * 100)
    high = base + np.abs(np.random.randn(n)) * 50
    low = base - np.abs(np.random.randn(n)) * 50
    close = base + np.random.randn(n) * 30
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.abs(np.random.randn(n)) * 1000 + 500

    df = pd.DataFrame({
        'open': open_, 'high': high, 'low': low,
        'close': close, 'volume': volume,
    }, index=dates)

    # 인디케이터 계산
    result = compute_frama_channel(df, N=26, distance=1.5, p_vol_mode='price')

    # 시그널 출력
    long_signals = result[result['signal_long']]
    short_signals = result[result['signal_short']]

    print(f"=== FRAMA Channel 결과 ({n} bars) ===")
    print(f"Long signals (dedup 후): {len(long_signals)}")
    print(f"Short signals (dedup 후): {len(short_signals)}")
    print(f"Raw break_up: {result['break_up'].sum()}")
    print(f"Raw break_dn: {result['break_dn'].sum()}")
    print()

    if len(long_signals) > 0:
        print("첫 long 시그널:")
        first_long = long_signals.iloc[0]
        print(f"  timestamp: {long_signals.index[0]}")
        print(f"  hlc3: {first_long['hlc3']:.2f}")
        print(f"  upper_band: {first_long['upper_band']:.2f}")
        print(f"  frama: {first_long['frama']:.2f}")
        print()

    # 검증 헬퍼 사용 예시 (TV 시그널을 직접 입력)
    print("=== 검증 헬퍼 예시 ===")
    # 실제 사용 시: TradingView에서 추출한 시그널 리스트
    if len(long_signals) >= 2 and len(short_signals) >= 1:
        # 더미 TV 시그널: Python 결과 기반으로 일부러 같게 만들어서 100% match 확인
        dummy_tv_signals = [
            {'timestamp': long_signals.index[0], 'direction': 'long'},
            {'timestamp': short_signals.index[0], 'direction': 'short'},
            {'timestamp': long_signals.index[1], 'direction': 'long'},
        ]

        verification = verify_against_tradingview(
            result, dummy_tv_signals, tolerance_bars=0
        )
        print(verification.summary())
