"""
보고서 생성기
============
모든 데이터를 조합하여 HTML 보고서를 생성.
Jinja2 템플릿 렌더링 + matplotlib 차트 생성.
"""

import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Callable

import matplotlib
matplotlib.use("Agg")  # GUI 없이 렌더링
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from report import clean_html, fig_to_base64
from report.classifier import NewsClassifier
from report.db_manager import NewsDB
from report.market_data import MarketDataFetcher, TICKER_DISPLAY_NAMES
from report.summarizer import NewsSummarizer
from report.volatility import VolatilityAnalyzer

logger = logging.getLogger(__name__)

# 한글 폰트 설정
try:
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
except Exception as e:
    logger.warning(f"한글 폰트 설정 실패: {e}")


class ReportGenerator:
    """HTML 보고서 생성 오케스트레이터."""

    def __init__(self, db: NewsDB, config: Any) -> None:
        """NewsDB, ConfigManager 주입.

        Args:
            db: NewsDB 인스턴스.
            config: ConfigManager 인스턴스.
        """
        self.db = db
        self.config = config

        # 내부 컴포넌트 초기화
        self.market_fetcher = MarketDataFetcher(db)
        categories = config.get("categories", {})
        self.classifier = NewsClassifier(categories)
        self.summarizer = NewsSummarizer()

        z_cfg = config.get("z_score_thresholds", {})
        self.volatility = VolatilityAnalyzer(
            high_threshold=z_cfg.get("high", 2.0),
            medium_threshold=z_cfg.get("medium", 1.0),
            rolling_window=z_cfg.get("window", 60),
        )

        # Jinja2 환경
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html"]),
        )

        self.report_settings = config.get("report_settings", {})
        self.chart_dpi = self.report_settings.get("chart_dpi", 100)
        self.max_per_cat = self.report_settings.get(
            "max_articles_per_category", 20
        )

    def generate(
        self,
        period_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> str:
        """보고서 생성 메인 메서드.

        Args:
            period_type: "daily" | "weekly" | "monthly" | "custom".
            start_date: 시작 날짜 (custom용).
            end_date: 종료 날짜 (custom용).
            progress_callback: GUI 진행 상태 업데이트용 (message, percent).

        Returns:
            생성된 HTML 파일 경로.
        """

        def _progress(msg: str, pct: int) -> None:
            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception as e:
                    logger.debug(f"progress_callback 오류: {e}")

        # 1. 기간 결정
        _progress("기간 계산 중...", 5)
        if period_type != "custom" or start_date is None or end_date is None:
            start_date, end_date = self._calculate_period(period_type)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time().replace(microsecond=0))

        # 2. DB에서 기사 로드
        _progress("기사 로드 중...", 10)
        articles = self.db.get_articles(start_dt, end_dt)
        logger.info(f"DB에서 {len(articles)}건 기사 로드")

        # 보충 수집 판단
        _progress("기사 보충 수집 확인 중...", 15)
        self._check_and_supplement(start_date, end_date)

        # 보충 후 다시 로드
        articles = self.db.get_articles(start_dt, end_dt)
        logger.info(f"보충 후 총 {len(articles)}건")

        # 3. 카테고리 분류 (아직 미분류된 기사)
        _progress("카테고리 분류 중...", 25)
        categories_config = self.config.get("categories", {})
        for article in articles:
            if not article.get("category") or article["category"] == "[]":
                cats = self.classifier.classify(
                    article.get("title", ""),
                    article.get("description", ""),
                )
                article["category"] = cats

            # 요약 생성
            desc = article.get("description", "")
            lang = article.get("lang", "ko")
            article["summary"] = self.summarizer.summarize(desc, lang=lang)

        # 4. 시장 데이터 수집
        _progress("시장 데이터 수집 중...", 35)
        try:
            market_data = self.market_fetcher.fetch_all_for_report(
                start_date, end_date
            )
        except Exception as e:
            logger.error(f"시장 데이터 수집 실패: {e}")
            market_data = {"prices": {}, "ecos": {}, "fred": {}}

        prices_dict = market_data.get("prices", {})

        # 5. 임팩트 스코어링
        _progress("Z-score 임팩트 스코어링 중...", 55)
        try:
            articles = self.volatility.score_articles(
                articles, categories_config, prices_dict
            )
        except Exception as e:
            logger.error(f"임팩트 스코어링 실패: {e}")

        # 6. Z-score 요약
        _progress("Z-score 요약 생성 중...", 65)
        try:
            zscore_summary = self.volatility.get_period_zscore_summary(
                start_date, end_date, prices_dict
            )
        except Exception as e:
            logger.error(f"Z-score 요약 실패: {e}")
            zscore_summary = pd.DataFrame()

        # 7. 차트 생성
        _progress("차트 생성 중...", 75)
        try:
            charts = self._generate_charts(
                articles, prices_dict, zscore_summary, categories_config, start_date
            )
        except Exception as e:
            logger.error(f"차트 생성 실패: {e}")
            charts = {}

        # 8. 템플릿 렌더링
        _progress("HTML 보고서 렌더링 중...", 90)
        context = self._build_template_context(
            articles, charts, market_data, period_type, start_date, end_date
        )

        template = self.jinja_env.get_template("report.html")
        html_content = template.render(**context)

        # 9. 파일 저장
        os.makedirs("reports", exist_ok=True)
        filename = f"{end_date.strftime('%Y%m%d')}_{period_type}_report.html"
        filepath = os.path.join("reports", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        _progress("완료!", 100)
        logger.info(f"보고서 생성 완료: {filepath}")
        return filepath

    def _calculate_period(self, period_type: str) -> tuple[date, date]:
        """period_type에 따른 start/end 자동 계산.

        Args:
            period_type: "daily", "weekly", "monthly", "custom".

        Returns:
            (start_date, end_date) 튜플.
        """
        today = date.today()
        if period_type == "daily":
            return today, today
        elif period_type == "weekly":
            return today - timedelta(days=7), today
        elif period_type == "monthly":
            return today - timedelta(days=30), today
        else:  # custom -- 호출자가 직접 지정
            return today, today

    def _check_and_supplement(
        self, start_date: date, end_date: date
    ) -> None:
        """DB 기사 수 확인 → 부족 시 Naver API 보충 검색.

        Args:
            start_date: 시작 날짜.
            end_date: 종료 날짜.
        """
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time().replace(microsecond=0))
        counts = self.db.get_article_count_by_date(start_dt, end_dt)

        # 일평균 10건 미만인 날짜 확인
        current = start_date
        low_dates: list[date] = []
        while current <= end_date:
            date_str = current.strftime("%Y-%m-%d")
            if counts.get(date_str, 0) < 10:
                low_dates.append(current)
            current += timedelta(days=1)

        if not low_dates:
            return

        logger.info(f"기사 부족 날짜 {len(low_dates)}일 → 네이버 보충 수집 시도")

        # 네이버 API 키 확인
        client_id = self.config.get("naver_client_id", "")
        client_secret = self.config.get("naver_client_secret", "")
        if not client_id or not client_secret:
            logger.info("네이버 API 키 미설정 → 보충 수집 건너뜀")
            return

        # 카테고리 키워드로 보충 검색
        import requests
        import time

        session = requests.Session()
        categories = self.config.get("categories", {})

        for cat_name, cat_cfg in categories.items():
            keywords = cat_cfg.get("keywords", [])[:3]  # 상위 3개만
            if not keywords:
                continue

            query = " | ".join(keywords)
            try:
                resp = session.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    params={"query": query, "display": 50, "sort": "date"},
                    headers={
                        "X-Naver-Client-Id": client_id,
                        "X-Naver-Client-Secret": client_secret,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                for item in data.get("items", []):
                    url = item.get("originallink") or item.get("link", "")
                    if not url:
                        continue

                    title = clean_html(item.get("title", ""))
                    description = clean_html(item.get("description", ""))
                    pub_str = item.get("pubDate", "")
                    pub_date = None
                    if pub_str:
                        try:
                            pub_date = datetime.strptime(
                                pub_str, "%a, %d %b %Y %H:%M:%S %z"
                            )
                            pub_date = pub_date.replace(tzinfo=None)
                        except Exception:
                            pass

                    cats = self.classifier.classify(title, description)

                    self.db.insert_article(
                        {
                            "url": url,
                            "title": title,
                            "description": description,
                            "source": f"네이버(보충:{cat_name})",
                            "lang": "ko",
                            "published": pub_date or datetime.now(),
                            "category": cats,
                            "matched_keywords": [],
                        }
                    )

                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"보충 수집 실패 [{cat_name}]: {e}")

    def _generate_charts(
        self,
        articles: list[dict[str, Any]],
        prices_dict: dict[str, pd.DataFrame],
        zscore_summary: pd.DataFrame,
        categories_config: dict[str, dict[str, Any]],
        start_date: date | None = None,
    ) -> dict[str, str]:
        """모든 차트를 생성하여 base64 문자열 dict로 반환.

        Args:
            articles: 기사 리스트.
            prices_dict: {ticker: DataFrame}.
            zscore_summary: Z-score 요약 DataFrame.
            categories_config: 카테고리 설정.

        Returns:
            {"market_overview": "data:image/png;base64,...", ...}
        """
        charts: dict[str, str] = {}

        # 1. 시장 개요: 주요 자산 수익률 바 차트 (보고서 기간 기준)
        try:
            charts["market_overview"] = self._chart_market_overview(
                prices_dict, start_date
            )
        except Exception as e:
            logger.warning(f"시장 개요 차트 실패: {e}")

        # 2. Z-score 히트맵
        try:
            if not zscore_summary.empty:
                charts["zscore_heatmap"] = self._chart_zscore_heatmap(
                    zscore_summary
                )
        except Exception as e:
            logger.warning(f"Z-score 히트맵 차트 실패: {e}")

        # 3. Z-score 바 차트
        try:
            if not zscore_summary.empty:
                charts["zscore_bars"] = self._chart_zscore_bars(zscore_summary)
        except Exception as e:
            logger.warning(f"Z-score 바 차트 실패: {e}")

        # 4. 카테고리별 기사 건수 도넛 차트
        try:
            charts["category_distribution"] = self._chart_category_donut(
                articles, categories_config
            )
        except Exception as e:
            logger.warning(f"카테고리 분포 차트 실패: {e}")

        return charts

    def _chart_market_overview(
        self, prices_dict: dict[str, pd.DataFrame],
        report_start: date | None = None,
    ) -> str:
        """주요 자산 수익률 바 차트 (보고서 기간 기준)."""
        tickers = []
        returns = []

        for ticker, df in prices_dict.items():
            if df is None or df.empty:
                continue
            close_col = None
            for c in ["close", "Close"]:
                if c in df.columns:
                    close_col = c
                    break
            if close_col is None or len(df) < 2:
                continue

            # 보고서 시작일 기준으로 슬라이싱 (lookback 데이터 제외)
            if report_start is not None:
                start_ts = pd.Timestamp(report_start)
                # timezone-aware index 대응
                if df.index.tz is not None:
                    start_ts = start_ts.tz_localize(df.index.tz)
                period_df = df[df.index >= start_ts]
                if period_df.empty or len(period_df) < 2:
                    period_df = df  # fallback
            else:
                period_df = df

            first = period_df[close_col].iloc[0]
            last = period_df[close_col].iloc[-1]
            if first and first != 0:
                ret = (last - first) / first * 100
                tickers.append(ticker)
                returns.append(ret)

        if not tickers:
            return ""

        fig, ax = plt.subplots(figsize=(10, 5))
        colors = ["#2196F3" if r >= 0 else "#F44336" for r in returns]
        bars = ax.barh(tickers, returns, color=colors)
        ax.set_xlabel("수익률 (%)")
        ax.set_title("기간 내 주요 자산 수익률")
        ax.axvline(x=0, color="gray", linewidth=0.8)

        for bar, ret in zip(bars, returns):
            ax.text(
                bar.get_width() + (0.1 if ret >= 0 else -0.1),
                bar.get_y() + bar.get_height() / 2,
                f"{ret:.2f}%",
                va="center",
                ha="left" if ret >= 0 else "right",
                fontsize=9,
            )

        plt.tight_layout()
        result = fig_to_base64(fig, dpi=self.chart_dpi)
        plt.close(fig)
        return result

    def _chart_zscore_heatmap(self, zscore_summary: pd.DataFrame) -> str:
        """Z-score 히트맵."""
        if zscore_summary.empty:
            return ""

        fig, ax = plt.subplots(figsize=(12, 5))
        data = zscore_summary.fillna(0).T.values
        tickers = list(zscore_summary.columns)
        dates = [d.strftime("%m/%d") for d in zscore_summary.index]

        im = ax.imshow(data, aspect="auto", cmap="RdBu_r", vmin=-3, vmax=3)
        ax.set_yticks(range(len(tickers)))
        ax.set_yticklabels(tickers, fontsize=8)

        # X축 날짜 라벨 (간격 조절)
        step = max(1, len(dates) // 10)
        ax.set_xticks(range(0, len(dates), step))
        ax.set_xticklabels(dates[::step], fontsize=8, rotation=45)

        ax.set_title("자산별 일간 Z-score 히트맵")
        fig.colorbar(im, ax=ax, label="Z-score", shrink=0.8)
        plt.tight_layout()
        result = fig_to_base64(fig, dpi=self.chart_dpi)
        plt.close(fig)
        return result

    def _chart_zscore_bars(self, zscore_summary: pd.DataFrame) -> str:
        """일별 최대 Z-score 바 차트."""
        if zscore_summary.empty:
            return ""

        max_z = zscore_summary.abs().max(axis=1)

        fig, ax = plt.subplots(figsize=(10, 4))
        colors = [
            "#F44336" if z > 2.0 else "#FF9800" if z > 1.0 else "#4CAF50"
            for z in max_z
        ]
        ax.bar(range(len(max_z)), max_z.values, color=colors, width=0.8)

        ax.axhline(y=2.0, color="#F44336", linestyle="--", alpha=0.5, label="HIGH (2.0)")
        ax.axhline(y=1.0, color="#FF9800", linestyle="--", alpha=0.5, label="MEDIUM (1.0)")

        step = max(1, len(max_z) // 10)
        labels = [d.strftime("%m/%d") for d in max_z.index]
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(labels[::step], fontsize=8, rotation=45)

        ax.set_ylabel("|Z-score| max")
        ax.set_title("일별 최대 변동성 Z-score")
        ax.legend(fontsize=8)
        plt.tight_layout()
        result = fig_to_base64(fig, dpi=self.chart_dpi)
        plt.close(fig)
        return result

    def _chart_category_donut(
        self,
        articles: list[dict[str, Any]],
        categories_config: dict[str, dict[str, Any]],
    ) -> str:
        """카테고리별 기사 건수 도넛 차트."""
        counter: Counter = Counter()
        for article in articles:
            cats = article.get("category", [])
            if isinstance(cats, str):
                cats = [cats] if cats else []
            if cats:
                # primary 카테고리만 집계
                details = self.classifier.classify_with_details(
                    article.get("title", ""),
                    article.get("description", ""),
                )
                primary = self.classifier.get_primary_category(details)
                if primary:
                    counter[primary] += 1
                else:
                    counter[cats[0]] += 1
            else:
                counter["기타"] += 1

        if not counter:
            return ""

        labels = list(counter.keys())
        sizes = list(counter.values())
        colors_list = [
            "#1976D2", "#388E3C", "#F57C00", "#D32F2F",
            "#7B1FA2", "#00796B", "#455A64", "#C2185B",
        ]

        fig, ax = plt.subplots(figsize=(7, 5))
        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            autopct="%1.0f%%",
            startangle=90,
            colors=colors_list[: len(labels)],
            pctdistance=0.8,
            wedgeprops=dict(width=0.4),
        )
        for text in autotexts:
            text.set_fontsize(9)
        ax.set_title("카테고리별 기사 분포")
        plt.tight_layout()
        result = fig_to_base64(fig, dpi=self.chart_dpi)
        plt.close(fig)
        return result

    def _build_template_context(
        self,
        articles: list[dict[str, Any]],
        charts: dict[str, str],
        market_data: dict[str, Any],
        period_type: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Jinja2 템플릿에 전달할 context dict 조립.

        Args:
            articles: 기사 리스트.
            charts: base64 이미지 dict.
            market_data: 시장 데이터.
            period_type: 기간 유형.
            start_date: 시작 날짜.
            end_date: 종료 날짜.

        Returns:
            템플릿 context dict.
        """
        categories_config = self.config.get("categories", {})

        # HIGH IMPACT 기사
        high_impact = [
            a for a in articles if a.get("importance") == "HIGH"
        ]
        high_impact.sort(
            key=lambda a: abs(a.get("z_score", 0)), reverse=True
        )

        # 카테고리별 기사 분류
        categorized: dict[str, list[dict]] = {}
        uncategorized: list[dict] = []

        for article in articles:
            cats = article.get("category", [])
            if isinstance(cats, str):
                cats = [cats] if cats else []

            if cats:
                for cat in cats:
                    if cat in categories_config:
                        if cat not in categorized:
                            categorized[cat] = []
                        categorized[cat].append(article)
            else:
                uncategorized.append(article)

        # 각 카테고리 내 중요도 순 정렬 + 최대 건수 제한
        importance_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNSCORED": 3}
        for cat_name in categorized:
            categorized[cat_name].sort(
                key=lambda a: importance_order.get(
                    a.get("importance", "UNSCORED"), 3
                )
            )
            categorized[cat_name] = categorized[cat_name][: self.max_per_cat]

        # 시장 요약 테이블 데이터
        market_summary: list[dict[str, Any]] = []
        prices_dict = market_data.get("prices", {})
        for ticker, df in prices_dict.items():
            if df is None or df.empty:
                continue
            close_col = None
            for c in ["close", "Close"]:
                if c in df.columns:
                    close_col = c
                    break
            if close_col is None or len(df) < 2:
                continue
            first_val = df[close_col].iloc[0]
            last_val = df[close_col].iloc[-1]
            ret = ((last_val - first_val) / first_val * 100) if first_val else 0
            display_name = TICKER_DISPLAY_NAMES.get(ticker, ticker)
            market_summary.append(
                {
                    "ticker": ticker,
                    "display_name": display_name,
                    "last_price": round(float(last_val), 2),
                    "return_pct": round(float(ret), 2),
                    "direction": "up" if ret >= 0 else "down",
                }
            )

        # 경제지표
        ecos_indicators: list[dict[str, Any]] = []
        for code, series in market_data.get("ecos", {}).items():
            if series is not None and not series.empty:
                ecos_indicators.append(
                    {
                        "code": code,
                        "name": code,
                        "latest_value": round(float(series.iloc[-1]), 4),
                        "latest_date": str(series.index[-1].date())
                        if hasattr(series.index[-1], "date")
                        else str(series.index[-1]),
                    }
                )

        fred_indicators: list[dict[str, Any]] = []
        for series_id, series in market_data.get("fred", {}).items():
            if series is not None and not series.empty:
                fred_indicators.append(
                    {
                        "series_id": series_id,
                        "latest_value": round(float(series.iloc[-1]), 4),
                        "latest_date": str(series.index[-1].date())
                        if hasattr(series.index[-1], "date")
                        else str(series.index[-1]),
                    }
                )

        # 통계
        source_counter: Counter = Counter()
        for a in articles:
            source_counter[a.get("source", "unknown")] += 1

        period_label = {
            "daily": "일간", "weekly": "주간",
            "monthly": "월간", "custom": "사용자 지정",
        }.get(period_type, period_type)

        uncategorized_total = len(uncategorized)
        categorized_total = len(articles) - uncategorized_total
        categorization_rate = round(
            categorized_total / max(len(articles), 1) * 100, 1
        )

        stats = {
            "total": len(articles),
            "high_count": len(high_impact),
            "sources": len(source_counter),
            "source_breakdown": dict(source_counter.most_common(10)),
            "categories_count": {
                cat: len(arts) for cat, arts in categorized.items()
            },
            "uncategorized_total": uncategorized_total,
            "categorization_rate": categorization_rate,
        }

        return {
            "ticker_names": TICKER_DISPLAY_NAMES,
            "report_title": f"Financial News Report ({period_label})",
            "period_type": period_type,
            "period_label": period_label,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "charts": charts,
            "high_impact_articles": high_impact[:15],
            "categorized_articles": categorized,
            "uncategorized_articles": uncategorized[:30],
            "categories": categories_config,
            "market_summary": market_summary,
            "ecos_indicators": ecos_indicators,
            "fred_indicators": fred_indicators,
            "stats": stats,
        }
