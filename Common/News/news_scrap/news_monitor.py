"""
📡 경제뉴스 모니터 (Economic News Monitor)
- RSS 피드 + 네이버 Open API 기반 실시간 경제뉴스 키워드 모니터링
- 빌드: PyInstaller로 단일 .exe 생성
- 의존성: feedparser, requests, pystray, Pillow
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import time
import json
import os
import hashlib
import webbrowser
import winsound
from datetime import datetime, timedelta
from collections import OrderedDict
import feedparser
import requests
import pystray
from PIL import Image, ImageDraw, ImageFont
import sys
import re
import html
import logging
from typing import Any

try:
    from report import clean_html
except ImportError:
    def clean_html(text):  # type: ignore[misc]
        """HTML 태그 및 엔티티 제거 (fallback)."""
        if not text:
            return ""
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        return text.strip()

# ============================================================
# 설정
# ============================================================

APP_NAME = "📡 경제뉴스 모니터"
APP_VERSION = "1.0.0"
CONFIG_FILE = "news_monitor_config.json"
LOG_FILE = "news_monitor.log"
MAX_ARTICLES = 500          # 화면에 보관할 최대 기사 수
CACHE_TTL_HOURS = 24        # 중복 체크 캐시 유지 시간
MAX_CACHE_SIZE = 5000       # 최대 캐시 엔트리

# ── 기본 키워드 (배포자가 수정 후 재빌드) ──
DEFAULT_KEYWORDS = [
    "금통위", "기준금리", "FOMC", "국고채", "통안채",
    "한은", "BOK", "기재부", "국채", "금리인하",
    "금리인상", "통화정책", "양적긴축", "QT",
    "CPI", "고용", "GDP", "환율", "달러",
    "연준", "Fed", "파월", "Powell",
]

# ── 뉴스 소스 정의 ──
# group: "fast"(2분), "medium"(3분), "slow"(5분)
RSS_SOURCES = [
    # === 국내 ===
    {"name": "한국경제 금융",   "url": "https://www.hankyung.com/feed/finance",     "group": "medium", "lang": "ko"},
    {"name": "한국경제 경제",   "url": "https://www.hankyung.com/feed/economy",     "group": "medium", "lang": "ko"},
    {"name": "한국경제 국제",   "url": "https://www.hankyung.com/feed/international","group": "medium", "lang": "ko"},
    {"name": "연합뉴스",       "url": "https://www.yna.co.kr/RSS/economy.xml",      "group": "medium", "lang": "ko"},
    {"name": "연합뉴스 국제",   "url": "https://www.yna.co.kr/RSS/international.xml","group": "medium", "lang": "ko"},
    {"name": "매일경제",       "url": "https://www.mk.co.kr/rss/30100041/",         "group": "medium", "lang": "ko"},
    {"name": "뉴시스 속보",    "url": "https://newsis.com/RSS/sokbo.xml",           "group": "medium", "lang": "ko"},
    # Google News 한국 비즈니스
    {"name": "Google 한국경제", "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko", "group": "fast", "lang": "ko"},

    # === 해외 ===
    {"name": "CNBC Top",       "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "group": "slow", "lang": "en"},
    {"name": "CNBC Finance",   "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",  "group": "slow", "lang": "en"},
    {"name": "Investing.com",  "url": "https://www.investing.com/rss/news.rss",     "group": "slow", "lang": "en"},
    {"name": "Yahoo Finance",  "url": "https://finance.yahoo.com/news/rssindex",    "group": "slow", "lang": "en"},
    {"name": "MarketWatch",    "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "group": "slow", "lang": "en"},
    {"name": "Seeking Alpha",  "url": "https://seekingalpha.com/market_currents.xml","group": "slow", "lang": "en"},
    # Reuters (Google News 우회)
    {"name": "Reuters via Google", "url": "https://news.google.com/rss/search?q=finance+site:reuters.com&ceid=US:en&hl=en&gl=US", "group": "slow", "lang": "en"},
]

POLL_INTERVALS = {
    "fast": 120,    # 2분
    "medium": 180,  # 3분
    "slow": 300,    # 5분
}

# ============================================================
# 로깅 설정
# ============================================================

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)


# ============================================================
# 설정 관리
# ============================================================

class ConfigManager:
    """사용자 설정을 JSON 파일로 관리"""

    DEFAULTS = {
        "user_keywords": [],
        "naver_client_id": "",
        "naver_client_secret": "",
        "enable_toast": True,
        "enable_sound": True,
        "google_news_custom_queries": [],  # 사용자 정의 Google News 검색어
        "poll_intervals": POLL_INTERVALS.copy(),
        "window_geometry": "900x650",
        "date_range": "today",  # today, 3days, 7days, all
        # ── 보고서 관련 설정 ──
        "categories": {
            "통화정책": {
                "keywords": ["금통위", "기준금리", "FOMC", "한은", "BOK", "Fed", "연준",
                             "파월", "Powell", "양적완화", "QE", "양적긴축", "QT",
                             "통화정책", "BOJ", "ECB", "금리동결", "피벗", "pivot"],
                "exclusion_keywords": ["FedEx", "feedback", "federal register",
                                       "federation", "fed up"],
                "icon": "\U0001f3e6",
                "related_assets": ["^TNX", "^FVX", "^IRX"]
            },
            "재정정책": {
                "keywords": ["기재부", "추경", "세제", "국채발행", "재정적자", "정부지출",
                             "stimulus", "재정정책", "세수", "국가채무"],
                "exclusion_keywords": [],
                "icon": "\U0001f3db\ufe0f",
                "related_assets": ["^KS11"]
            },
            "금리_채권": {
                "keywords": ["국고채", "통안채", "금리인상", "금리인하", "수익률곡선",
                             "금리", "채권", "스프레드", "장단기",
                             "Treasury", "yield", "bond"],
                "exclusion_keywords": ["채권추심", "채권자", "채권압류", "채권단",
                                       "James Bond", "bonding", "bondage",
                                       "semiconductor yield", "crop yield"],
                "icon": "\U0001f4ca",
                "related_assets": ["^TNX", "^FVX", "^TYX"]
            },
            "환율": {
                "keywords": ["환율", "달러", "원화", "USD/KRW", "강달러", "약달러",
                             "FX", "외환", "원달러", "엔화", "유로", "위안"],
                "exclusion_keywords": ["달러구트", "달러샵", "달러스토어"],
                "icon": "\U0001f4b1",
                "related_assets": ["USDKRW=X", "EURUSD=X", "USDJPY=X", "DX-Y.NYB"]
            },
            "원자재": {
                "keywords": ["유가", "WTI", "브렌트", "금값", "금시세", "국제유가",
                             "원유", "구리", "천연가스", "농산물",
                             "gold", "oil", "copper", "OPEC"],
                "exclusion_keywords": ["Goldman", "gold medal", "golden", "golden state",
                                       "spoil", "foil", "soil", "toil",
                                       "Cooper Kupp", "cooper union",
                                       "oil painting", "essential oil"],
                "icon": "\U0001f6e2\ufe0f",
                "related_assets": ["GC=F", "CL=F", "BZ=F"]
            },
            "거시경제": {
                "keywords": ["CPI", "GDP", "고용", "실업률", "PMI", "ISM", "inflation",
                             "경기침체", "소비자물가", "생산자물가", "NFP", "비농업"],
                "exclusion_keywords": ["PMI자격증", "Project Management Institute"],
                "icon": "\U0001f4c8",
                "related_assets": ["^GSPC", "^KS11", "^VIX"]
            },
        },
        "z_score_thresholds": {"high": 2.0, "medium": 1.0, "window": 60},
        "report_settings": {"max_articles_per_category": 20, "chart_dpi": 100},
    }

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.data = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"설정 파일 로드 실패: {e}")
                self.data = {}
        # 기본값 채우기
        for key, default in self.DEFAULTS.items():
            if key not in self.data:
                self.data[key] = default

    def save(self) -> None:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"설정 저장 실패: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()


# ============================================================
# 중복 캐시
# ============================================================

class DeduplicationCache:
    """URL 기반 중복 기사 필터링 (TTL 적용)"""

    def __init__(self, ttl_hours: int = CACHE_TTL_HOURS, max_size: int = MAX_CACHE_SIZE) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        self.max_size = max_size
        self.cache = OrderedDict()  # url_hash -> timestamp

    def _hash(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def is_seen(self, url: str) -> bool:
        h = self._hash(url)
        if h in self.cache:
            return True
        return False

    def add(self, url: str) -> None:
        h = self._hash(url)
        self.cache[h] = datetime.now()
        self._cleanup()

    def _cleanup(self) -> None:
        now = datetime.now()
        # TTL 만료 제거
        expired = [k for k, v in self.cache.items() if now - v > self.ttl]
        for k in expired:
            del self.cache[k]
        # 크기 제한
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


# ============================================================
# 뉴스 엔진
# ============================================================

class NewsEngine:
    """RSS 피드 및 네이버 API에서 뉴스 수집"""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.cache = DeduplicationCache()
        self._db = None  # NewsDB 인스턴스 (선택적)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def set_db(self, db):
        """NewsDB 인스턴스 주입. DB가 설정되면 poll_group()에서 자동 DB 저장."""
        self._db = db

    def get_all_keywords(self):
        """기본 키워드 + 사용자 키워드 반환"""
        user_kw = self.config.get("user_keywords", [])
        return list(set(DEFAULT_KEYWORDS + user_kw))

    def check_keyword_match(self, title, keywords):
        """제목에 키워드 매칭 여부 확인, 매칭된 키워드 리스트 반환"""
        title_lower = title.lower()
        matched = []
        for kw in keywords:
            if kw.lower() in title_lower:
                matched.append(kw)
        return matched

    def fetch_rss(self, source):
        """단일 RSS 소스에서 기사 수집"""
        articles = []
        try:
            feed = feedparser.parse(
                source["url"],
                agent=self.session.headers["User-Agent"]
            )
            keywords = self.get_all_keywords()

            for entry in feed.entries:
                url = getattr(entry, "link", "")
                if not url or self.cache.is_seen(url):
                    continue

                title = clean_html(getattr(entry, "title", "제목 없음"))

                # description 추출 (3단계 fallback)
                desc_raw = getattr(entry, "summary", "") or \
                           getattr(entry, "description", "")
                if not desc_raw and hasattr(entry, "content") and entry.content:
                    desc_raw = entry.content[0].get("value", "")
                description = clean_html(desc_raw)

                published = getattr(entry, "published", "")
                # 발행일 파싱 시도
                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        # feedparser는 published_parsed를 UTC로 정규화함.
                        # +9시간으로 KST 변환하여 datetime.now() (로컬 KST)와 일치시킴.
                        # 전제: 이 머신의 로컬 시간대가 KST (UTC+9)임.
                        # 주의: 피드가 시간대 정보를 생략하면 feedparser가 시간을 그대로
                        #   전달할 수 있으며(이미 로컬 시간), 이 경우 +9는 잘못된 결과를 줌.
                        #   현재 모든 RSS_SOURCES는 pubDate에 시간대를 포함함.
                        pub_date = datetime(*entry.published_parsed[:6]) + timedelta(hours=9)
                    except (ValueError, TypeError, OverflowError) as e:
                        logger.debug(f"발행일 파싱 실패: {e}")

                matched = self.check_keyword_match(title, keywords)

                self.cache.add(url)
                articles.append({
                    "title": title,
                    "description": description,
                    "url": url,
                    "source": source["name"],
                    "lang": source.get("lang", ""),
                    "published": pub_date or datetime.now(),
                    "matched_keywords": matched,
                    "fetched_at": datetime.now(),
                })

        except Exception as e:
            logger.warning(f"RSS 수집 실패 [{source['name']}]: {e}")

        return articles

    def fetch_naver_api(self, query, display=20):
        """네이버 뉴스 검색 API 호출"""
        articles = []
        client_id = self.config.get("naver_client_id", "")
        client_secret = self.config.get("naver_client_secret", "")

        if not client_id or not client_secret:
            return articles

        try:
            resp = self.session.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": display, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"네이버 API 오류: {resp.status_code}")
                return articles

            data = resp.json()
            keywords = self.get_all_keywords()

            for item in data.get("items", []):
                url = item.get("originallink") or item.get("link", "")
                if not url or self.cache.is_seen(url):
                    continue

                title = clean_html(item.get("title", ""))
                description = clean_html(item.get("description", ""))
                pub_str = item.get("pubDate", "")
                pub_date = None
                if pub_str:
                    try:
                        # 네이버 API는 KST (+0900)를 반환; tzinfo 제거 후에도 KST 값 유지.
                        # 전제: 이 머신의 로컬 시간대가 KST (UTC+9)임.
                        pub_date = datetime.strptime(pub_str, "%a, %d %b %Y %H:%M:%S %z")
                        pub_date = pub_date.replace(tzinfo=None)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"네이버 pubDate 파싱 실패: {e}")

                matched = self.check_keyword_match(title, keywords)

                self.cache.add(url)
                articles.append({
                    "title": title,
                    "description": description,
                    "url": url,
                    "source": f"네이버({query})",
                    "lang": "ko",
                    "published": pub_date or datetime.now(),
                    "matched_keywords": matched,
                    "fetched_at": datetime.now(),
                })

        except Exception as e:
            logger.warning(f"네이버 API 호출 실패 [{query}]: {e}")

        return articles

    def fetch_google_news_keyword(self, keyword, lang="ko"):
        """Google News RSS로 특정 키워드 검색"""
        if lang == "ko":
            url = f"https://news.google.com/rss/search?q={keyword}&ceid=KR:ko&hl=ko&gl=KR"
        else:
            url = f"https://news.google.com/rss/search?q={keyword}&ceid=US:en&hl=en&gl=US"

        source = {"name": f"Google({keyword})", "url": url, "group": "fast", "lang": lang}
        return self.fetch_rss(source)

    def poll_group(self, group_name):
        """특정 그룹의 모든 RSS 소스 폴링"""
        all_articles = []
        sources = [s for s in RSS_SOURCES if s["group"] == group_name]

        for source in sources:
            articles = self.fetch_rss(source)
            all_articles.extend(articles)

        # Google News 사용자 정의 검색어
        if group_name == "fast":
            custom_queries = self.config.get("google_news_custom_queries", [])
            for query in custom_queries:
                articles = self.fetch_google_news_keyword(query)
                all_articles.extend(articles)

        # 네이버 API (fast 그룹에서만)
        if group_name == "fast":
            naver_keywords = self.get_all_keywords()
            # 키워드를 5개씩 묶어서 OR 검색 (API 호출 수 절약)
            for i in range(0, len(naver_keywords), 5):
                batch = naver_keywords[i:i+5]
                query = " | ".join(batch)
                articles = self.fetch_naver_api(query, display=30)
                all_articles.extend(articles)
                time.sleep(0.2)  # API 호출 간 딜레이

        # DB 저장 훅 (NewsDB가 주입된 경우에만)
        if all_articles and self._db:
            try:
                self._db.insert_articles(all_articles)
            except Exception as e:
                logger.warning(f"DB 저장 실패: {e}")

        return all_articles


# ============================================================
# 시스템 트레이 아이콘 생성
# ============================================================

def create_tray_icon_image(size=64, has_alert=False):
    """프로그램 아이콘을 코드로 생성"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 배경 원
    bg_color = (41, 98, 255) if not has_alert else (255, 59, 48)
    draw.ellipse([2, 2, size-2, size-2], fill=bg_color)

    # 📡 안테나 모양
    cx, cy = size // 2, size // 2
    # 신호 원호
    for r in [12, 20, 28]:
        draw.arc(
            [cx - r, cy - r - 5, cx + r, cy + r - 5],
            200, 340,
            fill="white", width=2
        )
    # 안테나 기둥
    draw.line([cx, cy - 5, cx, cy + 18], fill="white", width=3)
    draw.ellipse([cx-3, cy+15, cx+3, cy+21], fill="white")

    return img


# ============================================================
# 메인 앱 (GUI)
# ============================================================

class NewsMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("920x680")
        self.root.minsize(700, 500)

        # 아이콘 설정
        self.icon_image = create_tray_icon_image()
        self._set_window_icon()

        # 컴포넌트 초기화
        self.config = ConfigManager(CONFIG_FILE)
        self.engine = NewsEngine(self.config)
        self.articles = []        # 전체 기사 리스트
        self.polling_active = False
        self.tray_icon = None
        self.is_minimized_to_tray = False
        self._poll_threads = {}   # group_name -> thread
        self.last_poll_times = {}  # group_name -> datetime
        self.poll_error_counts = {}  # group_name -> consecutive error count
        self._report_generating = False  # 보고서 생성 중 플래그

        # DB + 분류기 초기화
        try:
            from report.db_manager import NewsDB
            from report.classifier import NewsClassifier
            self.news_db = NewsDB()
            self.engine.set_db(self.news_db)
            self.classifier = NewsClassifier(self.config.get("categories", {}))
        except Exception as e:
            logger.warning(f"보고서 모듈 초기화 실패 (보고서 기능 비활성): {e}")
            self.news_db = None
            self.classifier = None

        # GUI 구성
        self._build_gui()

        # 이벤트 바인딩
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_minimize)

        # 폴링 시작
        self._start_polling()

        # 상태바 업데이트 루프
        self._update_status_loop()

        logger.info("앱 시작됨")

    def _set_window_icon(self):
        """tkinter 윈도우 아이콘 설정"""
        try:
            icon_photo = self.icon_image.resize((32, 32))
            self._icon_photo = tk.PhotoImage(data=self._pil_to_png_bytes(icon_photo))
            self.root.iconphoto(True, self._icon_photo)
        except Exception as e:
            logger.debug(f"윈도우 아이콘 설정 실패: {e}")

    @staticmethod
    def _pil_to_png_bytes(pil_img):
        """PIL Image를 PNG bytes로 변환 (tkinter PhotoImage용)"""
        import io
        buffer = io.BytesIO()
        pil_img.save(buffer, format="PNG")
        return buffer.getvalue()

    # ── GUI 구성 ──

    def _build_gui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # 탭 컨트롤
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 탭 1: 뉴스 피드
        self._build_feed_tab()

        # 탭 2: 보고서 생성
        self._build_report_tab()

        # 탭 3: 카테고리 관리
        self._build_category_tab()

        # 탭 4: 설정
        self._build_settings_tab()

        # 상태바
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        self.status_label = ttk.Label(self.status_frame, text="준비 중...", foreground="gray")
        self.status_label.pack(side=tk.LEFT)
        self.count_label = ttk.Label(self.status_frame, text="", foreground="gray")
        self.count_label.pack(side=tk.RIGHT)

    def _build_feed_tab(self):
        """뉴스 피드 탭"""
        feed_frame = ttk.Frame(self.notebook)
        self.notebook.add(feed_frame, text="  📰 뉴스 피드  ")

        # 필터 바
        filter_frame = ttk.Frame(feed_frame)
        filter_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(filter_frame, text="필터:").pack(side=tk.LEFT, padx=(0, 5))
        self.filter_var = tk.StringVar(value="all")
        ttk.Radiobutton(filter_frame, text="전체", variable=self.filter_var,
                        value="all", command=self._apply_filter).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(filter_frame, text="🔴 키워드 매칭만", variable=self.filter_var,
                        value="matched", command=self._apply_filter).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(filter_frame, text="🇰🇷 국내만", variable=self.filter_var,
                        value="ko", command=self._apply_filter).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(filter_frame, text="🌐 해외만", variable=self.filter_var,
                        value="en", command=self._apply_filter).pack(side=tk.LEFT, padx=3)

        # 검색
        ttk.Label(filter_frame, text="  검색:").pack(side=tk.LEFT, padx=(10, 5))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=20)
        search_entry.pack(side=tk.LEFT, padx=3)

        # 기간 필터
        ttk.Label(filter_frame, text="  기간:").pack(side=tk.LEFT, padx=(10, 5))
        self.date_range_var = tk.StringVar(value=self.config.get("date_range", "today"))
        date_combo = ttk.Combobox(filter_frame, textvariable=self.date_range_var,
                                   values=["today", "3days", "7days", "all"],
                                   state="readonly", width=8)
        date_combo.pack(side=tk.LEFT, padx=3)
        date_combo.bind("<<ComboboxSelected>>", lambda *_: self._on_date_range_change())

        # 새로고침 버튼
        ttk.Button(filter_frame, text="🔄 새로고침", command=self._manual_refresh,
                   width=10).pack(side=tk.RIGHT, padx=5)

        # 기사 리스트 (Treeview)
        columns = ("time", "source", "title", "keywords", "url")
        list_frame = ttk.Frame(feed_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings",
                                 selectmode="browse")
        # "url"을 화면에서 제외 -- displaycolumns가 width=0/minwidth=0보다 견고함
        self.tree["displaycolumns"] = ("time", "source", "title", "keywords")
        self.tree.heading("time", text="시간")
        self.tree.heading("source", text="출처")
        self.tree.heading("title", text="제목")
        self.tree.heading("keywords", text="매칭 키워드")

        self.tree.column("time", width=95, minwidth=80, stretch=False)
        self.tree.column("source", width=110, minwidth=80, stretch=False)
        self.tree.column("title", width=500, minwidth=200)
        self.tree.column("keywords", width=150, minwidth=100)

        # 스크롤바
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 더블클릭으로 기사 열기
        self.tree.bind("<Double-1>", self._on_article_click)
        # 우클릭 메뉴
        self.tree.bind("<Button-3>", self._on_right_click)

        # 태그 스타일
        self.tree.tag_configure("matched", foreground="#D32F2F", font=("", 10, "bold"))
        self.tree.tag_configure("normal", foreground="#333333")
        self.tree.tag_configure("en", foreground="#555555")

    # ── 보고서 탭 ──

    def _build_report_tab(self):
        """보고서 생성 탭 GUI 구성"""
        report_frame = ttk.Frame(self.notebook)
        self.notebook.add(report_frame, text="  \U0001f4c4 보고서  ")

        # 기간 선택
        period_frame = ttk.LabelFrame(report_frame, text="기간 선택", padding=10)
        period_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.report_period_var = tk.StringVar(value="daily")
        for text, val in [("일간", "daily"), ("주간", "weekly"),
                          ("월간", "monthly"), ("사용자 지정", "custom")]:
            ttk.Radiobutton(period_frame, text=text, variable=self.report_period_var,
                            value=val, command=self._on_report_period_change
                            ).pack(side=tk.LEFT, padx=8)

        # 사용자 지정 날짜 입력
        custom_frame = ttk.Frame(period_frame)
        custom_frame.pack(side=tk.LEFT, padx=(20, 0))

        ttk.Label(custom_frame, text="시작:").pack(side=tk.LEFT, padx=(0, 3))
        self.report_start_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d"))
        self.report_start_entry = ttk.Entry(
            custom_frame, textvariable=self.report_start_var, width=12,
            state="disabled")
        self.report_start_entry.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(custom_frame, text="종료:").pack(side=tk.LEFT, padx=(0, 3))
        self.report_end_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d"))
        self.report_end_entry = ttk.Entry(
            custom_frame, textvariable=self.report_end_var, width=12,
            state="disabled")
        self.report_end_entry.pack(side=tk.LEFT)

        # 생성 버튼 + 진행률
        action_frame = ttk.Frame(report_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=10)

        self.generate_btn = ttk.Button(
            action_frame, text="\U0001f4ca 보고서 생성",
            command=self._generate_report, width=20)
        self.generate_btn.pack(side=tk.LEFT, padx=(0, 15))

        progress_frame = ttk.Frame(action_frame)
        progress_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.report_progress_var = tk.DoubleVar(value=0)
        self.report_progress_bar = ttk.Progressbar(
            progress_frame, variable=self.report_progress_var,
            maximum=100, mode="determinate")
        self.report_progress_bar.pack(fill=tk.X)

        self.report_progress_label = ttk.Label(
            progress_frame, text="", foreground="gray")
        self.report_progress_label.pack(anchor="w")

        # 최근 보고서 목록
        list_frame = ttk.LabelFrame(report_frame, text="최근 생성 보고서", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        self.report_listbox = tk.Listbox(list_frame, height=8)
        self.report_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        report_scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.report_listbox.yview)
        self.report_listbox.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side=tk.LEFT, fill=tk.Y)

        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(side=tk.LEFT, padx=10)

        ttk.Button(btn_frame, text="\U0001f310 열기",
                   command=self._open_selected_report, width=12).pack(pady=3)
        ttk.Button(btn_frame, text="\U0001f4c1 폴더 열기",
                   command=self._open_report_folder, width=12).pack(pady=3)

        self._refresh_report_list()

    def _on_report_period_change(self):
        """보고서 기간 유형 변경 시 사용자 지정 입력 활성화/비활성화"""
        if self.report_period_var.get() == "custom":
            self.report_start_entry.config(state="normal")
            self.report_end_entry.config(state="normal")
        else:
            self.report_start_entry.config(state="disabled")
            self.report_end_entry.config(state="disabled")

    def _generate_report(self):
        """보고서 생성 버튼 핸들러 (별도 스레드에서 실행)"""
        if self._report_generating:
            messagebox.showwarning("진행 중", "이미 보고서 생성이 진행 중입니다.")
            return

        if not self.news_db:
            messagebox.showerror("오류", "보고서 모듈이 초기화되지 않았습니다.")
            return

        period_type = self.report_period_var.get()

        # 사용자 지정 기간 파싱
        from datetime import date as _date
        start_date = None
        end_date = None
        if period_type == "custom":
            try:
                start_date = datetime.strptime(
                    self.report_start_var.get(), "%Y-%m-%d").date()
                end_date = datetime.strptime(
                    self.report_end_var.get(), "%Y-%m-%d").date()
                if start_date > end_date:
                    messagebox.showerror("오류", "시작일이 종료일보다 늦습니다.")
                    return
            except ValueError:
                messagebox.showerror("오류", "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).")
                return

        self._report_generating = True
        self.generate_btn.config(state="disabled")
        self.report_progress_var.set(0)
        self.report_progress_label.config(text="보고서 생성 시작...")

        def progress_callback(message, percent):
            """GUI 진행률 업데이트 (메인 스레드에서 실행)"""
            self.root.after(0, lambda: self.report_progress_var.set(percent))
            self.root.after(0, lambda: self.report_progress_label.config(
                text=f"{percent}% — {message}"))

        def do_generate():
            try:
                from report.generator import ReportGenerator
                generator = ReportGenerator(self.news_db, self.config)
                output_path = generator.generate(
                    period_type,
                    start_date=start_date,
                    end_date=end_date,
                    progress_callback=progress_callback,
                )

                def on_complete():
                    self.report_progress_var.set(100)
                    self.report_progress_label.config(
                        text=f"완료! {os.path.basename(output_path)}")
                    self.generate_btn.config(state="normal")
                    self._report_generating = False
                    self._refresh_report_list()
                    # 브라우저 자동 오픈
                    try:
                        webbrowser.open(os.path.abspath(output_path))
                    except Exception as e:
                        logger.warning(f"브라우저 열기 실패: {e}")

                self.root.after(0, on_complete)

            except Exception as e:
                logger.error(f"보고서 생성 실패: {e}")

                def on_error():
                    self.report_progress_label.config(
                        text=f"오류: {str(e)[:60]}", foreground="red")
                    self.generate_btn.config(state="normal")
                    self._report_generating = False

                self.root.after(0, on_error)

        threading.Thread(target=do_generate, daemon=True).start()

    def _open_selected_report(self):
        """선택된 보고서를 브라우저에서 열기"""
        selection = self.report_listbox.curselection()
        if not selection:
            return
        filename = self.report_listbox.get(selection[0])
        path = os.path.join("reports", filename)
        if os.path.exists(path):
            webbrowser.open(os.path.abspath(path))

    def _open_report_folder(self):
        """보고서 폴더 열기"""
        reports_dir = os.path.abspath("reports")
        os.makedirs(reports_dir, exist_ok=True)
        try:
            os.startfile(reports_dir)
        except Exception:
            webbrowser.open(reports_dir)

    def _refresh_report_list(self):
        """reports/ 디렉토리 스캔 → 최근 보고서 목록 갱신"""
        self.report_listbox.delete(0, tk.END)
        reports_dir = "reports"
        if not os.path.exists(reports_dir):
            return
        files = [f for f in os.listdir(reports_dir) if f.endswith(".html")]
        files.sort(reverse=True)  # 최신 순
        for f in files[:20]:
            self.report_listbox.insert(tk.END, f)

    # ── 카테고리 관리 탭 ──

    def _build_category_tab(self):
        """카테고리 관리 탭 GUI 구성"""
        cat_frame = ttk.Frame(self.notebook)
        self.notebook.add(cat_frame, text="  \U0001f3f7\ufe0f 카테고리  ")

        # 좌측: 카테고리 목록
        left_frame = ttk.LabelFrame(cat_frame, text="카테고리 목록", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 5), pady=10)

        self.cat_listbox = tk.Listbox(left_frame, width=25, height=15)
        self.cat_listbox.pack(fill=tk.BOTH, expand=True)
        self.cat_listbox.bind("<<ListboxSelect>>", self._on_category_select)

        cat_btn_frame = ttk.Frame(left_frame)
        cat_btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(cat_btn_frame, text="\u2795 추가",
                   command=self._add_category).pack(side=tk.LEFT, padx=2)
        ttk.Button(cat_btn_frame, text="\u2796 삭제",
                   command=self._delete_category).pack(side=tk.LEFT, padx=2)

        # 우측: 편집 영역
        right_frame = ttk.LabelFrame(cat_frame, text="카테고리 편집", padding=10)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                         padx=(5, 10), pady=10)

        row = 0
        ttk.Label(right_frame, text="이름:").grid(
            row=row, column=0, sticky="w", pady=3)
        self.cat_name_var = tk.StringVar()
        ttk.Entry(right_frame, textvariable=self.cat_name_var, width=25).grid(
            row=row, column=1, sticky="w", padx=5, pady=3)
        row += 1

        ttk.Label(right_frame, text="아이콘:").grid(
            row=row, column=0, sticky="w", pady=3)
        self.cat_icon_var = tk.StringVar()
        ttk.Entry(right_frame, textvariable=self.cat_icon_var, width=5).grid(
            row=row, column=1, sticky="w", padx=5, pady=3)
        row += 1

        ttk.Label(right_frame, text="키워드:").grid(
            row=row, column=0, sticky="nw", pady=3)
        kw_edit_frame = ttk.Frame(right_frame)
        kw_edit_frame.grid(row=row, column=1, sticky="ew", padx=5, pady=3)

        self.cat_kw_listbox = tk.Listbox(kw_edit_frame, height=8, width=30)
        self.cat_kw_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        kw_btn_col = ttk.Frame(kw_edit_frame)
        kw_btn_col.pack(side=tk.LEFT, padx=5)
        self.cat_new_kw_var = tk.StringVar()
        ttk.Entry(kw_btn_col, textvariable=self.cat_new_kw_var,
                  width=15).pack(pady=2)
        ttk.Button(kw_btn_col, text="\u2795",
                   command=self._add_cat_keyword, width=5).pack(pady=2)
        ttk.Button(kw_btn_col, text="\u2796",
                   command=self._remove_cat_keyword, width=5).pack(pady=2)
        row += 1

        ttk.Label(right_frame, text="관련 자산:").grid(
            row=row, column=0, sticky="w", pady=3)
        self.cat_assets_var = tk.StringVar()
        ttk.Entry(right_frame, textvariable=self.cat_assets_var, width=40).grid(
            row=row, column=1, sticky="w", padx=5, pady=3)
        ttk.Label(right_frame, text="(쉼표 구분, 예: ^TNX, FEDFUNDS)",
                  foreground="gray").grid(
            row=row + 1, column=1, sticky="w", padx=5)
        row += 2

        save_frame = ttk.Frame(right_frame)
        save_frame.grid(row=row, column=0, columnspan=2, pady=15)
        ttk.Button(save_frame, text="\U0001f4be 저장",
                   command=self._save_category_config, width=12).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(save_frame, text="\u21a9\ufe0f 기본값 복원",
                   command=self._reset_categories_to_default, width=14).pack(
            side=tk.LEFT, padx=5)

        self._refresh_category_list()

    def _refresh_category_list(self):
        """카테고리 목록 갱신"""
        self.cat_listbox.delete(0, tk.END)
        categories = self.config.get("categories", {})
        for name, cfg in categories.items():
            icon = cfg.get("icon", "")
            self.cat_listbox.insert(tk.END, f"{icon} {name}")

    def _on_category_select(self, event=None):
        """카테고리 선택 시 편집 영역 갱신"""
        selection = self.cat_listbox.curselection()
        if not selection:
            return
        categories = self.config.get("categories", {})
        cat_names = list(categories.keys())
        idx = selection[0]
        if idx >= len(cat_names):
            return

        name = cat_names[idx]
        cfg = categories[name]

        self.cat_name_var.set(name)
        self.cat_icon_var.set(cfg.get("icon", ""))
        self.cat_assets_var.set(", ".join(cfg.get("related_assets", [])))

        self.cat_kw_listbox.delete(0, tk.END)
        for kw in cfg.get("keywords", []):
            self.cat_kw_listbox.insert(tk.END, kw)

        # 편집 중인 원래 이름 저장
        self._editing_cat_name = name

    def _add_cat_keyword(self):
        """카테고리 키워드 추가"""
        kw = self.cat_new_kw_var.get().strip()
        if kw:
            self.cat_kw_listbox.insert(tk.END, kw)
            self.cat_new_kw_var.set("")

    def _remove_cat_keyword(self):
        """카테고리 키워드 삭제"""
        selected = self.cat_kw_listbox.curselection()
        for idx in reversed(selected):
            self.cat_kw_listbox.delete(idx)

    def _add_category(self):
        """새 카테고리 추가"""
        name = simpledialog.askstring("카테고리 추가", "카테고리 이름:")
        if not name:
            return
        categories = self.config.get("categories", {})
        if name in categories:
            messagebox.showwarning("중복", f"'{name}' 카테고리가 이미 존재합니다.")
            return
        categories[name] = {"keywords": [], "icon": "\U0001f4cc", "related_assets": []}
        self.config.set("categories", categories)
        self._refresh_category_list()
        # 분류기 갱신
        if self.classifier:
            from report.classifier import NewsClassifier
            self.classifier = NewsClassifier(categories)

    def _delete_category(self):
        """선택된 카테고리 삭제"""
        selection = self.cat_listbox.curselection()
        if not selection:
            return
        categories = self.config.get("categories", {})
        cat_names = list(categories.keys())
        idx = selection[0]
        if idx >= len(cat_names):
            return
        name = cat_names[idx]
        if messagebox.askyesno("삭제 확인", f"'{name}' 카테고리를 삭제하시겠습니까?"):
            del categories[name]
            self.config.set("categories", categories)
            self._refresh_category_list()
            if self.classifier:
                from report.classifier import NewsClassifier
                self.classifier = NewsClassifier(categories)

    def _save_category_config(self):
        """편집된 카테고리 설정을 config에 저장"""
        if not hasattr(self, "_editing_cat_name"):
            messagebox.showinfo("안내", "먼저 카테고리를 선택하세요.")
            return

        categories = self.config.get("categories", {})
        old_name = self._editing_cat_name
        new_name = self.cat_name_var.get().strip()

        if not new_name:
            messagebox.showerror("오류", "카테고리 이름을 입력하세요.")
            return

        # 이름 변경 시 처리
        if old_name != new_name and old_name in categories:
            categories[new_name] = categories.pop(old_name)
        elif new_name not in categories:
            categories[new_name] = {}

        # 키워드 리스트 수집
        keywords = list(self.cat_kw_listbox.get(0, tk.END))

        # 관련 자산 파싱
        assets_str = self.cat_assets_var.get().strip()
        related_assets = [a.strip() for a in assets_str.split(",") if a.strip()] \
            if assets_str else []

        categories[new_name] = {
            "keywords": keywords,
            "icon": self.cat_icon_var.get().strip() or "\U0001f4cc",
            "related_assets": related_assets,
        }

        self.config.set("categories", categories)
        self._editing_cat_name = new_name
        self._refresh_category_list()

        # 분류기 갱신
        if self.classifier:
            from report.classifier import NewsClassifier
            self.classifier = NewsClassifier(categories)

        messagebox.showinfo("저장 완료", f"'{new_name}' 카테고리가 저장되었습니다.")

    def _reset_categories_to_default(self):
        """카테고리를 기본값으로 복원"""
        if messagebox.askyesno("기본값 복원",
                               "모든 카테고리를 기본 설정으로 복원하시겠습니까?"):
            default_cats = ConfigManager.DEFAULTS["categories"]
            self.config.set("categories", default_cats.copy())
            self._refresh_category_list()
            if self.classifier:
                from report.classifier import NewsClassifier
                self.classifier = NewsClassifier(default_cats)
            messagebox.showinfo("완료", "카테고리가 기본값으로 복원되었습니다.")

    def _build_settings_tab(self):
        """설정 탭"""
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="  ⚙️ 설정  ")

        canvas = tk.Canvas(settings_frame)
        scrollbar = ttk.Scrollbar(settings_frame, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        row = 0

        # ── 기본 키워드 (읽기 전용) ──
        ttk.Label(inner, text="📌 기본 키워드 (배포자가 관리)", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 5), padx=5)
        row += 1

        default_kw_text = ", ".join(DEFAULT_KEYWORDS)
        ttk.Label(inner, text=default_kw_text, wraplength=700, foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 10))
        row += 1

        # ── 내 키워드 ──
        ttk.Label(inner, text="🏷️ 내 키워드", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 5), padx=5)
        row += 1

        kw_frame = ttk.Frame(inner)
        kw_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 5))

        self.user_kw_listbox = tk.Listbox(kw_frame, height=6, width=50,
                                           selectmode=tk.EXTENDED)
        self.user_kw_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        kw_btn_frame = ttk.Frame(kw_frame)
        kw_btn_frame.pack(side=tk.LEFT, padx=10)

        self.new_kw_var = tk.StringVar()
        ttk.Entry(kw_btn_frame, textvariable=self.new_kw_var, width=20).pack(pady=2)
        ttk.Button(kw_btn_frame, text="➕ 추가", command=self._add_user_keyword).pack(fill=tk.X, pady=2)
        ttk.Button(kw_btn_frame, text="➖ 삭제", command=self._remove_user_keyword).pack(fill=tk.X, pady=2)

        self._refresh_user_keyword_list()
        row += 1

        # ── Google News 사용자 검색어 ──
        ttk.Label(inner, text="🔍 Google News 추가 검색어", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(15, 5), padx=5)
        row += 1

        ttk.Label(inner, text="특정 검색어로 Google News RSS를 추가 폴링합니다 (예: 삼성전자, Tesla)",
                  foreground="gray").grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
        row += 1

        gn_frame = ttk.Frame(inner)
        gn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=20, pady=(5, 5))

        self.gn_listbox = tk.Listbox(gn_frame, height=4, width=50, selectmode=tk.EXTENDED)
        self.gn_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        gn_btn_frame = ttk.Frame(gn_frame)
        gn_btn_frame.pack(side=tk.LEFT, padx=10)

        self.new_gn_var = tk.StringVar()
        ttk.Entry(gn_btn_frame, textvariable=self.new_gn_var, width=20).pack(pady=2)
        ttk.Button(gn_btn_frame, text="➕ 추가", command=self._add_gn_query).pack(fill=tk.X, pady=2)
        ttk.Button(gn_btn_frame, text="➖ 삭제", command=self._remove_gn_query).pack(fill=tk.X, pady=2)

        self._refresh_gn_list()
        row += 1

        # ── 네이버 API ──
        ttk.Label(inner, text="🔗 네이버 Open API (선택사항)", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(15, 5), padx=5)
        row += 1

        ttk.Label(inner, text="developers.naver.com에서 무료 발급 → 일 25,000건",
                  foreground="gray").grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
        row += 1

        naver_frame = ttk.Frame(inner)
        naver_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=20, pady=5)

        ttk.Label(naver_frame, text="Client ID:").grid(row=0, column=0, sticky="w", pady=2)
        self.naver_id_var = tk.StringVar(value=self.config.get("naver_client_id", ""))
        ttk.Entry(naver_frame, textvariable=self.naver_id_var, width=40).grid(
            row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(naver_frame, text="Client Secret:").grid(row=1, column=0, sticky="w", pady=2)
        self.naver_secret_var = tk.StringVar(value=self.config.get("naver_client_secret", ""))
        ttk.Entry(naver_frame, textvariable=self.naver_secret_var, width=40, show="*").grid(
            row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Button(naver_frame, text="💾 저장", command=self._save_naver_config).grid(
            row=0, column=2, rowspan=2, padx=10)
        row += 1

        # ── 알림 설정 ──
        ttk.Label(inner, text="🔔 알림 설정", font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(15, 5), padx=5)
        row += 1

        alert_frame = ttk.Frame(inner)
        alert_frame.grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=5)

        self.toast_var = tk.BooleanVar(value=self.config.get("enable_toast", True))
        ttk.Checkbutton(alert_frame, text="시스템 트레이 알림 (트레이에 최소화 시)",
                        variable=self.toast_var, command=self._save_alert_config).pack(anchor="w")

        self.sound_var = tk.BooleanVar(value=self.config.get("enable_sound", True))
        ttk.Checkbutton(alert_frame, text="알림 소리 🔊",
                        variable=self.sound_var, command=self._save_alert_config).pack(anchor="w")
        row += 1

        # ── 정보 ──
        ttk.Separator(inner, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=15, padx=5)
        row += 1

        info_text = (
            f"📡 경제뉴스 모니터 v{APP_VERSION}\n"
            f"폴링 주기: 국내속보 2분 / 국내일반 3분 / 해외 5분\n"
            f"RSS 소스: {len(RSS_SOURCES)}개 | 중복제거 캐시: {CACHE_TTL_HOURS}시간"
        )
        ttk.Label(inner, text=info_text, foreground="gray", justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=5, pady=5)

    # ── 설정 탭 동작 ──

    def _refresh_user_keyword_list(self):
        self.user_kw_listbox.delete(0, tk.END)
        for kw in self.config.get("user_keywords", []):
            self.user_kw_listbox.insert(tk.END, kw)

    def _add_user_keyword(self):
        kw = self.new_kw_var.get().strip()
        if not kw:
            return
        keywords = self.config.get("user_keywords", [])
        if kw not in keywords:
            keywords.append(kw)
            self.config.set("user_keywords", keywords)
            self._refresh_user_keyword_list()
        self.new_kw_var.set("")

    def _remove_user_keyword(self):
        selected = self.user_kw_listbox.curselection()
        if not selected:
            return
        keywords = self.config.get("user_keywords", [])
        for idx in reversed(selected):
            if idx < len(keywords):
                keywords.pop(idx)
        self.config.set("user_keywords", keywords)
        self._refresh_user_keyword_list()

    def _refresh_gn_list(self):
        self.gn_listbox.delete(0, tk.END)
        for q in self.config.get("google_news_custom_queries", []):
            self.gn_listbox.insert(tk.END, q)

    def _add_gn_query(self):
        q = self.new_gn_var.get().strip()
        if not q:
            return
        queries = self.config.get("google_news_custom_queries", [])
        if q not in queries:
            queries.append(q)
            self.config.set("google_news_custom_queries", queries)
            self._refresh_gn_list()
        self.new_gn_var.set("")

    def _remove_gn_query(self):
        selected = self.gn_listbox.curselection()
        if not selected:
            return
        queries = self.config.get("google_news_custom_queries", [])
        for idx in reversed(selected):
            if idx < len(queries):
                queries.pop(idx)
        self.config.set("google_news_custom_queries", queries)
        self._refresh_gn_list()

    def _save_naver_config(self):
        self.config.set("naver_client_id", self.naver_id_var.get().strip())
        self.config.set("naver_client_secret", self.naver_secret_var.get().strip())
        messagebox.showinfo("저장 완료", "네이버 API 설정이 저장되었습니다.")

    def _save_alert_config(self):
        self.config.set("enable_toast", self.toast_var.get())
        self.config.set("enable_sound", self.sound_var.get())

    # ── 뉴스 피드 동작 ──

    def _add_articles_to_ui(self, new_articles):
        """새 기사를 UI에 추가 (메인 스레드에서 호출)"""
        if not new_articles:
            return

        date_cutoff = self._get_date_cutoff()
        matched_count = 0

        for article in new_articles:
            # 카테고리 분류 적용 (classifier가 있는 경우에만)
            if self.classifier and ("category" not in article or not article.get("category")):
                cats = self.classifier.classify(
                    article.get("title", ""), article.get("description", ""))
                article["category"] = cats

            # 기간 필터 적용
            if date_cutoff and article["published"] < date_cutoff:
                continue
            self.articles.append(article)
            if article["matched_keywords"]:
                matched_count += 1

        # 전역 정렬: 최신순
        self.articles.sort(key=lambda a: a["published"], reverse=True)

        # 기사 수 제한
        if len(self.articles) > MAX_ARTICLES:
            self.articles = self.articles[:MAX_ARTICLES]

        # Treeview 재구성 (정렬 + 필터 반영)
        self._apply_filter()

        # 키워드 매치 알림 -- _notify는 (count, articles) 2개 인자 필요 (토스트 본문용)
        if matched_count > 0:
            self._notify(matched_count, new_articles)

        # 카운트 업데이트 (인라인 -- 별도 메서드 없음)
        total = len(self.articles)
        matched_total = sum(1 for a in self.articles if a["matched_keywords"])
        self.count_label.config(text=f"전체 {total}건 | 키워드 매칭 {matched_total}건")

    def _notify(self, count, articles):
        """키워드 매칭 기사 알림"""
        # 소리
        if self.config.get("enable_sound", True):
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception as e:
                logger.debug(f"알림음 재생 실패: {e}")

        # 트레이 알림
        if self.is_minimized_to_tray and self.config.get("enable_toast", True):
            matched = [a for a in articles if a["matched_keywords"]]
            if matched:
                title = f"🔴 키워드 매칭 {count}건"
                body = "\n".join(
                    f"[{a['source']}] {a['title'][:50]}"
                    for a in matched[:3]
                )
                if count > 3:
                    body += f"\n...외 {count - 3}건"
                try:
                    if self.tray_icon:
                        self.tray_icon.notify(title, body)
                except Exception as e:
                    logger.warning(f"트레이 알림 실패: {e}")

    def _on_article_click(self, event):
        """기사 더블클릭 → 브라우저 열기"""
        selection = self.tree.selection()
        if not selection:
            return
        item = self.tree.item(selection[0])
        url = item["values"][4]  # 5번째 컬럼: 숨겨진 URL
        if url:
            webbrowser.open(url)

    def _on_right_click(self, event):
        """우클릭 컨텍스트 메뉴"""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        self.tree.selection_set(item_id)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🔗 브라우저에서 열기", command=lambda: self._on_article_click(None))
        menu.add_command(label="📋 링크 복사", command=lambda: self._copy_link(item_id))
        menu.add_separator()
        menu.add_command(label="🗑️ 이 기사 숨기기", command=lambda: self._hide_article(item_id))
        menu.post(event.x_root, event.y_root)

    def _copy_link(self, item_id):
        item = self.tree.item(item_id)
        url = item["values"][4]
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)

    def _hide_article(self, item_id):
        item = self.tree.item(item_id)
        url = item["values"][4]
        # URL 매칭으로 기사 리스트에서 제거 (필터 상태에서도 안전)
        self.articles = [a for a in self.articles if a["url"] != url]
        self.tree.delete(item_id)

    def _on_date_range_change(self):
        """기간 변경 시 설정 저장 + 필터 적용"""
        self.config.set("date_range", self.date_range_var.get())
        self._apply_filter()

    def _get_date_cutoff(self):
        """선택된 기간에 따른 cutoff datetime 반환"""
        range_val = self.date_range_var.get()
        now = datetime.now()
        if range_val == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif range_val == "3days":
            return (now - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif range_val == "7days":
            return (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # all
            return None

    def _apply_filter(self):
        """필터 적용 → Treeview 재구성"""
        filter_mode = self.filter_var.get()
        search_text = self.search_var.get().strip().lower()
        date_cutoff = self._get_date_cutoff()

        # 전체 삭제 후 재삽입
        for item in self.tree.get_children():
            self.tree.delete(item)

        for article in self.articles:
            # 기간 필터
            if date_cutoff and article["published"] < date_cutoff:
                continue
            # 카테고리 필터
            if filter_mode == "matched" and not article["matched_keywords"]:
                continue
            if filter_mode == "ko" and article["lang"] != "ko":
                continue
            if filter_mode == "en" and article["lang"] not in ("en", ""):
                continue
            if search_text and search_text not in article["title"].lower():
                continue

            tag = "normal"
            kw_text = ""
            if article["matched_keywords"]:
                tag = "matched"
                kw_text = ", ".join(article["matched_keywords"])
            elif article["lang"] == "en":
                tag = "en"

            time_str = article["published"].strftime("%m/%d %H:%M")
            self.tree.insert("", tk.END, values=(
                time_str,
                article["source"],
                article["title"],
                kw_text,
                article["url"],  # 클릭 매핑용 숨겨진 URL 컬럼
            ), tags=(tag,))

    # ── 폴링 엔진 ──

    def _manual_refresh(self):
        """수동 새로고침 — 모든 그룹 즉시 폴링"""
        self.status_label.config(text="🔄 새로고침 중...", foreground="orange")

        def do_refresh():
            all_articles = []
            for group_name in POLL_INTERVALS:
                try:
                    articles = self.engine.poll_group(group_name)
                    all_articles.extend(articles)
                    self.last_poll_times[group_name] = datetime.now()
                    self.poll_error_counts[group_name] = 0
                except Exception as e:
                    logger.error(f"수동 새로고침 오류 [{group_name}]: {e}")
            if all_articles:
                self.root.after(0, self._add_articles_to_ui, all_articles)
            self.root.after(0, lambda: self.status_label.config(
                text=f"🟢 새로고침 완료 — {len(all_articles)}건 수집", foreground="green"))

        threading.Thread(target=do_refresh, daemon=True).start()

    def _start_polling(self):
        """각 그룹별 폴링 스레드 시작"""
        self.polling_active = True

        for group_name in POLL_INTERVALS:
            self._launch_poll_thread(group_name)

        self.next_fast_poll = datetime.now()
        self.next_medium_poll = datetime.now()
        self.next_slow_poll = datetime.now()

        logger.info("폴링 시작됨")

    def _launch_poll_thread(self, group_name):
        """특정 그룹의 폴링 스레드 (재)시작"""
        interval = POLL_INTERVALS[group_name]
        t = threading.Thread(
            target=self._poll_loop,
            args=(group_name, interval),
            daemon=True,
            name=f"poll-{group_name}",
        )
        t.start()
        self._poll_threads[group_name] = t
        logger.info(f"폴링 스레드 시작: {group_name}")

    def _poll_loop(self, group_name, interval):
        """특정 그룹 폴링 루프 (백그라운드 스레드)"""
        # 시작 시 약간의 지연 (그룹별 분산)
        delay_map = {"fast": 0, "medium": 5, "slow": 10}
        time.sleep(delay_map.get(group_name, 0))

        while self.polling_active:
            try:
                articles = self.engine.poll_group(group_name)
                self.last_poll_times[group_name] = datetime.now()
                self.poll_error_counts[group_name] = 0

                if articles:
                    # 메인 스레드에서 UI 업데이트
                    self.root.after(0, self._add_articles_to_ui, articles)
                    logger.info(f"[{group_name}] {len(articles)}건 수집")
                else:
                    logger.debug(f"[{group_name}] 새 기사 없음")

                # 다음 폴링 시간 업데이트
                setattr(self, f"next_{group_name}_poll",
                        datetime.now() + timedelta(seconds=interval))

            except Exception as e:
                error_count = self.poll_error_counts.get(group_name, 0) + 1
                self.poll_error_counts[group_name] = error_count
                logger.error(f"폴링 오류 [{group_name}] (연속 {error_count}회): {e}")

                # 연속 에러 시 대기 시간 증가 (최대 10분)
                wait_time = min(interval * error_count, 600)
                for _ in range(wait_time):
                    if not self.polling_active:
                        return
                    time.sleep(1)
                continue

            # 인터벌 대기 (1초 단위로 체크하여 종료 감지)
            for _ in range(interval):
                if not self.polling_active:
                    return
                time.sleep(1)

        logger.info(f"폴링 스레드 종료: {group_name}")

    def _check_thread_health(self):
        """죽은 폴링 스레드 감지 및 재시작"""
        if not self.polling_active:
            return

        for group_name in POLL_INTERVALS:
            thread = self._poll_threads.get(group_name)
            if thread is None or not thread.is_alive():
                logger.warning(f"폴링 스레드 죽음 감지 → 재시작: {group_name}")
                self._launch_poll_thread(group_name)

    def _update_status_loop(self):
        """상태바 업데이트 (1초마다) + 스레드 건강 체크"""
        if not self.polling_active:
            return

        now = datetime.now()

        # 10초마다 스레드 건강 체크
        if not hasattr(self, "_health_check_counter"):
            self._health_check_counter = 0
        self._health_check_counter += 1
        if self._health_check_counter >= 10:
            self._health_check_counter = 0
            self._check_thread_health()

        # 상태바: 마지막 수집 시각 + 다음 갱신까지
        parts = []
        for group_name in ["fast", "medium", "slow"]:
            label = {"fast": "속보", "medium": "일반", "slow": "해외"}[group_name]
            last = self.last_poll_times.get(group_name)
            thread = self._poll_threads.get(group_name)
            alive = thread and thread.is_alive()

            if not alive:
                parts.append(f"{label}: ⚠️ 중단")
            elif last:
                ago = int((now - last).total_seconds())
                if ago < 60:
                    parts.append(f"{label}: {ago}초 전")
                else:
                    parts.append(f"{label}: {ago // 60}분 전")
            else:
                parts.append(f"{label}: 대기중")

        errors = sum(self.poll_error_counts.get(g, 0) for g in POLL_INTERVALS)
        error_text = f" | ⚠️ 에러 {errors}" if errors > 0 else ""

        self.status_label.config(
            text=f"🟢 모니터링 중 | 마지막 수집 — {' | '.join(parts)}{error_text}",
            foreground="green" if errors == 0 else "orange"
        )

        self.root.after(1000, self._update_status_loop)

    # ── 시스템 트레이 ──

    def _setup_tray(self):
        """시스템 트레이 아이콘 설정"""
        menu = pystray.Menu(
            pystray.MenuItem("열기", self._restore_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._quit_app),
        )
        self.tray_icon = pystray.Icon(
            "news_monitor",
            self.icon_image,
            APP_NAME,
            menu,
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _on_minimize(self, event):
        """최소화 시 트레이로 보내기"""
        if self.root.state() == "iconic":
            self.root.withdraw()
            self.is_minimized_to_tray = True
            if not self.tray_icon:
                self._setup_tray()

    def _restore_from_tray(self, icon=None, item=None):
        """트레이에서 복원"""
        self.is_minimized_to_tray = False
        self.root.after(0, self._do_restore)

    def _do_restore(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self):
        """창 닫기 → 트레이로 최소화"""
        result = messagebox.askyesnocancel(
            "종료 확인",
            "트레이로 최소화하시겠습니까?\n\n"
            "예 = 트레이로 최소화 (백그라운드 실행)\n"
            "아니오 = 완전 종료"
        )
        if result is True:
            # 트레이로
            self.root.withdraw()
            self.is_minimized_to_tray = True
            if not self.tray_icon:
                self._setup_tray()
        elif result is False:
            self._quit_app()
        # Cancel = 아무것도 안 함

    def _quit_app(self, icon=None, item=None):
        """앱 완전 종료"""
        self.polling_active = False
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception as e:
                logger.debug(f"트레이 아이콘 종료 오류: {e}")
        # DB 종료
        if hasattr(self, "news_db") and self.news_db:
            try:
                self.news_db.close()
            except Exception as e:
                logger.debug(f"DB 종료 오류: {e}")
        self.config.save()
        logger.info("앱 종료됨")
        self.root.after(0, self.root.destroy)


# ============================================================
# 진입점
# ============================================================

def main():
    root = tk.Tk()
    app = NewsMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
