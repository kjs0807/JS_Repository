"""
뉴스 요약기
==========
RSS description에서 extractive 요약 (첫 N문장 추출).
한국어/영문 문장 분리 로직 포함.
"""

import re
from report import clean_html


class NewsSummarizer:
    """RSS description 기반 extractive 요약기."""

    def summarize(
        self, description: str, lang: str = "ko", max_sentences: int = 2
    ) -> str:
        """description에서 첫 max_sentences개 문장 추출.

        Args:
            description: RSS description/summary 원문.
            lang: 언어 코드 ('ko' 또는 'en').
            max_sentences: 추출할 최대 문장 수 (기본 2).

        Returns:
            추출된 요약 문자열. 빈 입력이면 빈 문자열.
        """
        if not description:
            return ""

        # HTML 클리닝
        text = clean_html(description)
        if not text:
            return ""

        # 언어에 따라 문장 분리
        if lang == "en":
            sentences = self._split_sentences_en(text)
        else:
            sentences = self._split_sentences_ko(text)

        # 첫 N문장 결합
        selected = sentences[:max_sentences]
        return " ".join(s.strip() for s in selected if s.strip())

    def _split_sentences_ko(self, text: str) -> list[str]:
        """한국어 문장 분리 (정규식 기반).

        숫자 뒤 마침표(3.5) 예외 처리.
        종결어미(.!?다요함됨음) 뒤 공백 기준 분리.
        """
        # 숫자+마침표+숫자 패턴을 임시 치환하여 보호
        placeholder = "\x00NUM_DOT\x00"
        protected = re.sub(r"(\d)\.(\d)", rf"\1{placeholder}\2", text)

        # 종결어미 뒤 공백 기준 분리
        sentences = re.split(r"(?<=[.!?\u3002다요함됨음])\s+", protected)

        # 플레이스홀더 복원
        sentences = [s.replace(placeholder, ".") for s in sentences]

        # 빈 문장 제거
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return [text]

        return sentences

    def _split_sentences_en(self, text: str) -> list[str]:
        """영문 문장 분리.

        약어(Mr., Dr., U.S., etc.) 예외 처리.
        """
        # 약어 보호: Mr. Mrs. Dr. U.S. etc. → 임시 치환
        abbreviations = {
            "Mr.": "Mr\x00",
            "Mrs.": "Mrs\x00",
            "Ms.": "Ms\x00",
            "Dr.": "Dr\x00",
            "U.S.": "U\x00S\x00",
            "U.K.": "U\x00K\x00",
            "vs.": "vs\x00",
            "etc.": "etc\x00",
            "Inc.": "Inc\x00",
            "Corp.": "Corp\x00",
            "Ltd.": "Ltd\x00",
            "Sr.": "Sr\x00",
            "Jr.": "Jr\x00",
            "Prof.": "Prof\x00",
            "St.": "St\x00",
        }
        protected = text
        for abbr, replacement in abbreviations.items():
            protected = protected.replace(abbr, replacement)

        # 문장 부호 뒤 공백으로 분리
        sentences = re.split(r"(?<=[.!?])\s+", protected)

        # 약어 복원
        restored: list[str] = []
        for s in sentences:
            for abbr, replacement in abbreviations.items():
                s = s.replace(replacement, abbr)
            s = s.strip()
            if s:
                restored.append(s)

        if not restored:
            return [text]

        return restored
