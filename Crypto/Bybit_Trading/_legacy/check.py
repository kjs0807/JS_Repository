"""포지션/잔고 간단 조회 스크립트. 사용: python check.py"""
import sys
sys.path.insert(0, ".")
from api.rest_client import BybitRestClient
from config.settings import AppSettings

settings = AppSettings()
c = BybitRestClient(base_url=settings.base_url)

# 포지션
positions = c.get_positions()
if positions:
    print("=== 포지션 ===")
    for p in positions:
        print(f"  {p['symbol']} {p['side']} qty={p['size']} entry={p['avgPrice']} mark={p['markPrice']} uPnL={p['unrealisedPnl']} TP={p.get('takeProfit','-')} SL={p.get('stopLoss','-')}")
else:
    print("포지션 없음")

# 잔고
bal = c.get_wallet_balance()
if bal:
    for coin in bal.get("coin", []):
        if coin.get("coin") == "USDT":
            print(f"\n=== 잔고 ===")
            print(f"  지갑: {coin['walletBalance']} USDT")
            print(f"  미실현PnL: {coin.get('unrealisedPnl', '-')}")
            print(f"  오늘실현: {coin.get('cumRealisedPnl', '-')}")
