"""통합 테스트: Optimizer + WalkForward + Overfit + Analyzer."""
import sys, logging
sys.path.insert(0, '.')
logging.disable(logging.WARNING)

import numpy as np
from src.core.config import BacktestConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.indicators.momentum import bollinger, keltner, atr
from src.strategies.indicators.oscillator import rsi
from src.backtester.engine import BacktestEngine
from src.backtester.optimizer import GridSearchOptimizer
from src.backtester.walk_forward import WalkForwardAnalyzer
from src.backtester.config import WalkForwardConfig
from src.backtester.overfit import OverfitDetector
from src.backtester.analyzer import PerformanceAnalyzer

class BBKCSqueeze:
    name='BBKCSqueeze'; timeframe='1h'
    def __init__(s, bb_std=1.5, kc_mult=1.0, tp_pct=0.06, sl_pct=0.07):
        s.bb_std=bb_std; s.kc_mult=kc_mult; s.tp_pct=tp_pct; s.sl_pct=sl_pct
    def on_bar(s, bar, series, broker):
        if len(series)<s.warmup_bars or broker.get_position(bar.symbol): return
        bb=bollinger(series,period=20,std=s.bb_std)
        kc=keltner(series,ema_period=20,atr_period=14,atr_mult=s.kc_mult)
        rsi_r=rsi(series,period=14); atr_r=atr(series,period=14)
        if any(np.isnan(x[-1]) for x in [bb.upper,kc.upper,rsi_r.values,atr_r.values]): return
        if len(bb.upper)<2 or np.isnan(bb.upper[-2]) or np.isnan(kc.upper[-2]): return
        prev_sq=(bb.upper[-2]<kc.upper[-2])and(bb.lower[-2]>kc.lower[-2])
        cur_sq=(bb.upper[-1]<kc.upper[-1])and(bb.lower[-1]>kc.lower[-1])
        if not(prev_sq and not cur_sq): return
        cl,bm,rv=bar.close,bb.mid[-1],rsi_r.values[-1]
        ptp,psl=s.tp_pct/3,s.sl_pct/3
        if cl>bm and rv<=70:
            sl=cl*(1-psl); q=broker.calc_qty(bar.symbol,0.02,cl-sl)
            if q>0: broker.buy(bar.symbol,q,sl,cl*(1+ptp),reason='SQ L')
        elif cl<bm and rv>=30:
            sl=cl*(1+psl); q=broker.calc_qty(bar.symbol,0.02,sl-cl)
            if q>0: broker.sell(bar.symbol,q,sl,cl*(1-ptp),reason='SQ S')
    def on_fill(s,f): pass
    def get_params(s): return {'bb_std':s.bb_std,'kc_mult':s.kc_mult,'tp_pct':s.tp_pct,'sl_pct':s.sl_pct}
    def set_params(s,p):
        for k,v in p.items():
            if hasattr(s,k): setattr(s,k,v)
    @property
    def warmup_bars(s): return 35

db=DBManager(db_path='db/bybit_data.db')
config=BacktestConfig(initial_capital=50000.0,taker_fee_pct=0.00055,slippage_pct=0.0003)

# 2. OPTIMIZER
print('='*60); print('2. OPTIMIZER (GridSearch) - BTCUSDT'); print('='*60)
feed=HistoricalDataFeed(db=db,symbols=['BTCUSDT'],timeframe='1h')
opt=GridSearchOptimizer(BacktestEngine())
opt_r=opt.run(BBKCSqueeze,{'bb_std':[1.5,2.0],'kc_mult':[1.0,1.5]},feed,config,'BTCUSDT')
print(f'best_params: {opt_r.best_params}')
print(f'best_score (Sharpe): {opt_r.best_score:.3f}')
for p,s in opt_r.all_results: print(f'  {p} -> Sharpe={s:.3f}')
sys.stdout.flush()

# 3. WALK-FORWARD
print(); print('='*60); print('3. WALK-FORWARD - BTCUSDT'); print('='*60)
wf_config=WalkForwardConfig(is_months=4,oos_months=2,min_windows=2)
wf=WalkForwardAnalyzer(wf_config)
feed2=HistoricalDataFeed(db=db,symbols=['BTCUSDT'],timeframe='1h')
wf_r=wf.run(BBKCSqueeze,{'bb_std':[1.5,2.0],'kc_mult':[1.0,1.5]},feed2,config,'BTCUSDT')
print(f'windows: {len(wf_r.windows)}')
print(f'avg OOS retention: {wf_r.avg_oos_retention:.1%}')
print(f'avg OOS Sharpe: {wf_r.avg_oos_sharpe:.3f}')
print(f'OOS positive pct: {wf_r.oos_positive_pct:.1%}')
for w in wf_r.windows:
    is_s=w.is_result.sharpe_ratio if w.is_result else 0
    oos_s=w.oos_result.sharpe_ratio if w.oos_result else 0
    print(f'  W{w.window_idx}: IS={is_s:.3f} OOS={oos_s:.3f} ret={w.oos_retention:.1%} params={w.best_params}')
sys.stdout.flush()

# 4. OVERFIT
print(); print('='*60); print('4. OVERFIT DETECTOR'); print('='*60)
feed3=HistoricalDataFeed(db=db,symbols=['BTCUSDT'],timeframe='1h')
bs=BBKCSqueeze(); bs.set_params(opt_r.best_params)
br=BacktestEngine().run(bs,feed3,config,'BTCUSDT')
pnl_list=[t.pnl for t in br.trades]
scores={str(p):s for p,s in opt_r.all_results}
v=OverfitDetector().detect(pnl_list,scores,n_shuffles=500)
print(f'verdict: {v.verdict}')
print(f'p_value: {v.p_value:.4f}')
print(f'sensitivity: {v.sensitivity:.3f}')
print(f'reason: {v.reason}')
sys.stdout.flush()

# 5. ANALYZER
print(); print('='*60); print('5. PERFORMANCE ANALYZER'); print('='*60)
ana=PerformanceAnalyzer()
feed_e=HistoricalDataFeed(db=db,symbols=['ETHUSDT'],timeframe='1h')
es=BBKCSqueeze(); es.set_params(opt_r.best_params)
er=BacktestEngine().run(es,feed_e,config,'ETHUSDT')
table=ana.compare([br,er])
for row in table:
    print(f'{row["strategy_name"]}({row["symbol"]}): PnL={row["total_pnl"]:+,.0f} Sharpe={row["sharpe_ratio"]:.3f} MDD={row["max_drawdown"]:.2%}')
print()
print(ana.generate_report(br))
alloc=ana.suggest_allocation([br,er])
print(f'Allocation: {alloc}')

print('\n=== ALL INTEGRATION TESTS PASSED ===')
