"""
HTS 자동 로그아웃 방지 스크립트 v2
- 5분마다 마우스 움직임 + 클릭 + 키 입력
- 종료: Ctrl+C
- 비상정지: 마우스를 화면 왼쪽 상단 모서리로
"""

import pyautogui
import time
import random
from datetime import datetime

pyautogui.FAILSAFE = True  # 왼쪽 상단 모서리로 마우스 이동 시 즉시 중지

# ===== 설정 =====
INTERVAL = 5          # 간격 (초). 기본 5분
DO_CLICK = True         # True: 현재 위치에서 클릭도 함
DO_KEY = True           # True: Scroll Lock 키 토글 (화면 영향 없음)
# ================

print("=" * 50)
print("  HTS 자동 로그아웃 방지 v2")
print(f"  간격: {INTERVAL // 60}분")
print(f"  클릭: {'ON' if DO_CLICK else 'OFF'}")
print(f"  키입력: {'ON' if DO_KEY else 'OFF'}")
print("  종료: Ctrl+C")
print("  비상정지: 마우스를 화면 왼쪽 상단 모서리로")
print("=" * 50)
print()
print("※ HTS 창을 포커스된 상태로 두세요.")
print("※ 다른 작업하려면 별도 모니터나 가상데스크톱 활용")
print()

try:
    count = 0
    while True:
        time.sleep(INTERVAL)
        count += 1
        now = datetime.now().strftime("%H:%M:%S")

        # 1) 마우스 미세 움직임 후 복귀
        x, y = pyautogui.position()
        dx = random.choice([-3, -2, -1, 1, 2, 3])
        dy = random.choice([-3, -2, -1, 1, 2, 3])
        pyautogui.moveTo(x + dx, y + dy, duration=0.1)
        time.sleep(0.2)
        pyautogui.moveTo(x, y, duration=0.1)
        actions = ["move"]

        # 2) 클릭 (현재 위치에서)
        if DO_CLICK:
            time.sleep(0.1)
            pyautogui.click(x, y)
            actions.append("click")

        # 3) 키 입력 (Scroll Lock - 화면에 영향 없음)
        if DO_KEY:
            time.sleep(0.1)
            pyautogui.press("scrolllock")
            time.sleep(0.05)
            pyautogui.press("scrolllock")  # 두 번 눌러서 원래 상태 복귀
            actions.append("key")

        print(f"[{now}] #{count} 완료 ({'+'.join(actions)}) 위치:({x},{y})")

except KeyboardInterrupt:
    print("\n종료되었습니다.")
