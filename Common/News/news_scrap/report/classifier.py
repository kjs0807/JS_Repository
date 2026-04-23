"""
뉴스 분류기
==========
카테고리별 키워드 매칭으로 기사를 분류.
복수 카테고리 매칭 허용.

v3: 한국어는 substring 매칭 유지 + exclusion 필터,
    영어는 word boundary 정규식 매칭.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _is_korean(text: str) -> bool:
    """문자열에 한국어가 포함되어 있는지 확인."""
    return bool(re.search(r"[가-힣]", text))


def _build_keyword_matcher(kw: str) -> tuple[str, re.Pattern[str] | None]:
    """키워드에 맞는 매칭 전략 결정.

    한국어 키워드: substring 매칭 (pattern=None, kw.lower() 반환)
    영어 키워드: word boundary 정규식

    Returns:
        (lowercase_keyword, compiled_pattern_or_None)
    """
    kw_lower = kw.lower()

    if _is_korean(kw):
        # 한국어: substring 매칭 (교착어 특성상 \b 사용 불가)
        return (kw_lower, None)
    else:
        # 영어: word boundary 정규식
        pattern = re.compile(rf"\b{re.escape(kw_lower)}\b", re.IGNORECASE)
        return (kw_lower, pattern)


class NewsClassifier:
    """키워드 기반 뉴스 카테고리 분류기.

    v3: 한국어 substring + 영어 word boundary + exclusion_keywords.
    """

    def __init__(self, categories: dict[str, dict[str, Any]]) -> None:
        """카테고리 설정 초기화.

        Args:
            categories: config['categories']에서 로드된 dict.
                구조: {"통화정책": {"keywords": [...], "icon": "...",
                       "related_assets": [...],
                       "exclusion_keywords": [...]  # 선택
                       }, ...}
        """
        self.categories = categories or {}

        # 키워드별 매칭 전략 (kw_lower, pattern_or_None)
        self._matchers: dict[str, list[tuple[str, str, re.Pattern[str] | None]]] = {}
        # exclusion: 항상 substring 매칭 (한/영 모두)
        self._exclusions: dict[str, list[str]] = {}

        for cat_name, cat_config in self.categories.items():
            keywords = cat_config.get("keywords", [])
            self._matchers[cat_name] = [
                (kw, *_build_keyword_matcher(kw)) for kw in keywords
            ]

            exclusions = cat_config.get("exclusion_keywords", [])
            self._exclusions[cat_name] = [ex.lower() for ex in exclusions]

    def _match_keyword(
        self, kw_lower: str, pattern: re.Pattern[str] | None, text: str
    ) -> bool:
        """키워드 매칭 수행.

        Args:
            kw_lower: 소문자 키워드.
            pattern: 영어용 정규식 패턴 (한국어는 None).
            text: 검색 대상 텍스트 (소문자).

        Returns:
            매칭 여부.
        """
        if pattern is not None:
            return bool(pattern.search(text))
        else:
            return kw_lower in text

    def _has_exclusion(self, cat_name: str, text_lower: str) -> bool:
        """텍스트에 해당 카테고리의 exclusion 키워드가 포함되었는지 확인."""
        for ex in self._exclusions.get(cat_name, []):
            if ex in text_lower:
                return True
        return False

    def classify(self, title: str, description: str = "") -> list[str]:
        """제목 + description에 대해 모든 카테고리의 키워드 매칭.

        매칭 전략:
            1차: 제목(title)에서 키워드 매칭
            2차: 제목 미매칭 시 description에서 추가 검색
            3차: exclusion_keywords 체크 → 해당 시 카테고리 제외

        Args:
            title: 기사 제목.
            description: 기사 본문 요약 (선택).

        Returns:
            매칭된 카테고리명 리스트. 미매칭 시 빈 리스트 [].
        """
        matched_cats: list[str] = []
        title_lower = (title or "").lower()
        desc_lower = (description or "").lower()
        combined_lower = title_lower + " " + desc_lower

        for cat_name, matchers in self._matchers.items():
            found = False

            # 1차: 제목에서 매칭
            for _kw_orig, kw_lower, pattern in matchers:
                if self._match_keyword(kw_lower, pattern, title_lower):
                    found = True
                    break

            # 2차: 제목 미매칭 시 description에서 매칭
            if not found and desc_lower:
                for _kw_orig, kw_lower, pattern in matchers:
                    if self._match_keyword(kw_lower, pattern, desc_lower):
                        found = True
                        break

            # 3차: exclusion 체크
            if found and self._has_exclusion(cat_name, combined_lower):
                found = False

            if found:
                matched_cats.append(cat_name)

        return matched_cats

    def classify_with_details(
        self, title: str, description: str = ""
    ) -> dict[str, list[str]]:
        """분류 + 각 카테고리별 매칭된 키워드 상세 반환.

        Args:
            title: 기사 제목.
            description: 기사 본문 요약.

        Returns:
            {"통화정책": ["FOMC", "파월"], "금리_채권": ["국고채"]}
        """
        result: dict[str, list[str]] = {}
        title_lower = (title or "").lower()
        desc_lower = (description or "").lower()
        combined_lower = title_lower + " " + desc_lower

        for cat_name, matchers in self._matchers.items():
            matched_kw: list[str] = []
            for kw_orig, kw_lower, pattern in matchers:
                if self._match_keyword(kw_lower, pattern, combined_lower):
                    matched_kw.append(kw_orig)

            # exclusion 체크
            if matched_kw and self._has_exclusion(cat_name, combined_lower):
                continue

            if matched_kw:
                result[cat_name] = matched_kw

        return result

    def get_primary_category(
        self, categories_matched: dict[str, list[str]]
    ) -> str | None:
        """복수 카테고리 중 primary 결정.

        규칙: 매칭 키워드 수 최다 카테고리. 동률 시 카테고리 정의 순서.

        Args:
            categories_matched: classify_with_details() 반환값.

        Returns:
            primary 카테고리명 또는 미매칭 시 None.
        """
        if not categories_matched:
            return None

        cat_order = list(self.categories.keys())
        best_cat: str | None = None
        best_count = 0

        for cat_name in cat_order:
            if cat_name in categories_matched:
                count = len(categories_matched[cat_name])
                if count > best_count:
                    best_count = count
                    best_cat = cat_name

        return best_cat
