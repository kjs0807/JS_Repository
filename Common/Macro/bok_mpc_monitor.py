"""
한국은행 금통위 통화정책방향 결정문 자동 모니터링
- 목록 페이지를 주기적으로 확인하여 새 결정문 감지
- PDF 다운로드 후 핵심 내용 파싱
- 데스크톱 알림 + 콘솔 출력
"""

import requests
from bs4 import BeautifulSoup
import time
import re
import os
from datetime import datetime
import tempfile
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# PDF 파싱용
try:
    import fitz  # PyMuPDF
    PDF_LIBRARY = "pymupdf"
except ImportError:
    try:
        import pdfplumber
        PDF_LIBRARY = "pdfplumber"
    except ImportError:
        PDF_LIBRARY = None

# 알림용
try:
    from plyer import notification
    NOTIFICATION_AVAILABLE = True
except ImportError:
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        NOTIFICATION_AVAILABLE = True
    except ImportError:
        NOTIFICATION_AVAILABLE = False


# ============ 설정 ============
LIST_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755"
CHECK_INTERVAL = 10  # 체크 간격 (초)
TARGET_DATE = "1월 21일"  # 오늘 날짜 (페이지에 표시되는 형식)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def show_notification(title: str, message: str):
    """데스크톱 알림 표시"""
    print(f"\n{'='*60}")
    print(f"🔔 알림: {title}")
    print(f"{'='*60}")
    print(message)
    print(f"{'='*60}\n")
    
    if NOTIFICATION_AVAILABLE:
        try:
            # plyer 방식
            notification.notify(
                title=title,
                message=message[:256],  # 길이 제한
                timeout=30
            )
        except:
            try:
                # win10toast 방식
                toaster.show_toast(title, message[:256], duration=30, threaded=True)
            except Exception as e:
                print(f"(알림 표시 실패: {e})")
    else:
        print("(데스크톱 알림 라이브러리가 설치되어 있지 않습니다)")
        print("설치: pip install plyer 또는 pip install win10toast")


def get_page_html() -> str:
    """목록 페이지 HTML 가져오기"""
    response = requests.get(LIST_URL, headers=HEADERS, timeout=10, verify=False)
    response.raise_for_status()
    return response.text


def find_pdf_link(html: str, target_date: str) -> str | None:
    """
    특정 날짜 행에서 결정문 PDF 링크 찾기
    반환: PDF 다운로드 URL 또는 None
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    # 테이블 행들을 찾음
    rows = soup.find_all('tr')
    
    for row in rows:
        # 해당 날짜가 포함된 행 찾기
        row_text = row.get_text()
        if target_date in row_text:
            # 결정문 컬럼의 PDF 링크 찾기
            # fileDown.do 패턴 또는 down.do 패턴
            links = row.find_all('a', href=True)
            for link in links:
                href = link.get('href', '')
                # PDF 다운로드 링크 패턴 확인
                if 'fileDown.do' in href or 'down.do' in href:
                    # fileSn=2가 PDF (보통 1=HWP, 2=PDF)
                    if 'fileSn=2' in href or 'pdf' in href.lower():
                        # 상대경로면 절대경로로 변환
                        if href.startswith('/'):
                            return f"https://www.bok.or.kr{href}"
                        elif not href.startswith('http'):
                            return f"https://www.bok.or.kr/{href}"
                        return href
                    
            # fileSn 구분 없이 첫 번째 fileDown 링크라도 찾기
            for link in links:
                href = link.get('href', '')
                if 'fileDown.do' in href:
                    if href.startswith('/'):
                        return f"https://www.bok.or.kr{href}"
                    elif not href.startswith('http'):
                        return f"https://www.bok.or.kr/{href}"
                    return href
    
    return None


def download_pdf(url: str) -> str:
    """PDF 파일 다운로드 후 임시 파일 경로 반환"""
    response = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    response.raise_for_status()
    
    # 임시 파일로 저장
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    temp_file.write(response.content)
    temp_file.close()
    
    return temp_file.name


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF에서 텍스트 추출"""
    text = ""
    
    if PDF_LIBRARY == "pymupdf":
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text()
        doc.close()
    elif PDF_LIBRARY == "pdfplumber":
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
    else:
        raise ImportError("PDF 라이브러리가 설치되어 있지 않습니다. pip install PyMuPDF 또는 pip install pdfplumber")
    
    return text


def parse_key_info(text: str) -> dict:
    """결정문에서 핵심 정보 추출"""
    info = {
        "내년성장률": None,
        "주요내용": []
    }
    
    # 문단 단위로 분리 (줄바꿈 기준)
    paragraphs = re.split(r'\n\s*\n|\n', text)
    
    # '내년 성장률' 키워드가 포함된 문단 찾기
    growth_keywords = ['내년 성장률', '내년성장률', '내년 경제성장률']
    
    for para in paragraphs:
        para = para.strip()
        if len(para) < 10:
            continue
            
        for keyword in growth_keywords:
            if keyword in para:
                info["내년성장률"] = para
                info["주요내용"].append(para)
                break
        
        # 찾았으면 종료
        if info["내년성장률"]:
            break
    
    # 못 찾았을 경우 문장 단위로 재시도
    if not info["내년성장률"]:
        sentences = text.split('.')
        for sent in sentences:
            sent = sent.strip()
            for keyword in growth_keywords:
                if keyword in sent:
                    info["내년성장률"] = sent
                    info["주요내용"].append(sent)
                    break
            if info["내년성장률"]:
                break
    
    return info


def format_result(info: dict) -> str:
    """결과를 보기 좋게 포맷팅"""
    lines = []
    lines.append(f"📊 내년 성장률 관련:")
    
    if info["내년성장률"]:
        lines.append(f"  {info['내년성장률']}")
    else:
        lines.append("  ❌ 관련 내용 없음")
    
    return "\n".join(lines)


def search_in_text(text: str, keyword: str) -> list:
    """
    텍스트에서 키워드가 포함된 문장들을 찾아 반환
    """
    results = []
    
    # 문장 단위로 분리 (마침표, 물음표, 느낌표 기준)
    sentences = re.split(r'[.?!]\s*', text)
    
    for sent in sentences:
        sent = sent.strip()
        if keyword in sent and len(sent) > 10:
            results.append(sent)
    
    return results


def interactive_search(text: str):
    """
    사용자가 원하는 키워드를 입력하여 내용 검색하는 인터랙티브 모드
    """
    print("\n" + "="*60)
    print("🔍 키워드 검색 모드")
    print("="*60)
    print("찾고 싶은 키워드를 입력하세요.")
    print("여러 키워드는 쉼표(,)로 구분")
    print("종료하려면 'q' 또는 'exit' 입력")
    print("="*60)
    
    while True:
        try:
            user_input = input("\n🔎 검색 키워드: ").strip()
            
            # 종료 조건
            if user_input.lower() in ['q', 'exit', 'quit', '종료']:
                print("검색 모드 종료")
                break
            
            if not user_input:
                print("키워드를 입력해주세요.")
                continue
            
            # 쉼표로 여러 키워드 분리
            keywords = [kw.strip() for kw in user_input.split(',') if kw.strip()]
            
            for keyword in keywords:
                print(f"\n📌 '{keyword}' 검색 결과:")
                print("-" * 40)
                
                results = search_in_text(text, keyword)
                
                if results:
                    for i, result in enumerate(results, 1):
                        print(f"  {i}. {result}")
                    print(f"\n  ✅ 총 {len(results)}개 문장에서 발견")
                else:
                    print(f"  ❌ '{keyword}' 관련 내용 없음")
                    
        except KeyboardInterrupt:
            print("\n검색 모드 종료")
            break


def monitor():
    """메인 모니터링 루프"""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║       한국은행 금통위 결정문 모니터링 시작                    ║
║                                                              ║
║  대상 날짜: {TARGET_DATE}                                     ║
║  체크 간격: {CHECK_INTERVAL}초                                        ║
║  시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                       ║
║                                                              ║
║  종료하려면 Ctrl+C를 누르세요                                ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    check_count = 0
    
    while True:
        try:
            check_count += 1
            current_time = datetime.now().strftime('%H:%M:%S')
            print(f"[{current_time}] 체크 #{check_count}...", end=" ")
            
            # 페이지 확인
            html = get_page_html()
            pdf_link = find_pdf_link(html, TARGET_DATE)
            
            if pdf_link:
                print(f"\n\n🎉 결정문 발견! 다운로드 중...")
                print(f"링크: {pdf_link}")
                
                # PDF 다운로드
                pdf_path = download_pdf(pdf_link)
                print(f"다운로드 완료: {pdf_path}")
                
                # 텍스트 추출
                print("텍스트 추출 중...")
                text = extract_text_from_pdf(pdf_path)

                # 마지막 □ 문단 미리보기
                print("\n" + "="*60)
                print("📋 마지막 문단 미리보기:")
                print("="*60)
                # □로 시작하는 문단들만 추출
                box_paragraphs = re.findall(r'□[^□]+', text)
                if box_paragraphs:
                    last_box = box_paragraphs[-1].strip()
                    print(last_box)
                else:
                    print("(□ 문단을 찾을 수 없습니다)")
                print("="*60 + "\n")

                # 핵심 정보 파싱
                print("핵심 정보 분석 중...")
                info = parse_key_info(text)
                
                # 결과 출력 및 알림
                result = format_result(info)
                show_notification(
                    f"🏦 금통위 결정문 발표! ({TARGET_DATE})",
                    f"내년 성장률: {info['내년성장률'][:50] if info['내년성장률'] else '확인 필요'}..."
                )
                
                print("\n" + "="*60)
                print("📄 전체 파싱 결과:")
                print("="*60)
                print(result)
                print("="*60)
                
                # 전문 텍스트도 저장
                output_file = f"결정문_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(f"=== 핵심 정보 ===\n{result}\n\n")
                    f.write(f"=== 전문 ===\n{text}")
                print(f"\n💾 전문 저장됨: {output_file}")
                
                # 임시 PDF 파일 삭제
                os.unlink(pdf_path)
                
                print("\n✅ 모니터링 완료!")
                
                # 인터랙티브 검색 모드 진입
                interactive_search(text)
                
                break
            else:
                print("아직 미게시")
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n\n⏹️ 모니터링 중단됨")
            break
        except requests.exceptions.RequestException as e:
            print(f"네트워크 오류: {e}")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"오류 발생: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # 라이브러리 체크
    print("라이브러리 체크...")
    if PDF_LIBRARY:
        print(f"  ✅ PDF 라이브러리: {PDF_LIBRARY}")
    else:
        print("  ❌ PDF 라이브러리 없음 - 설치 필요: pip install PyMuPDF")
    
    if NOTIFICATION_AVAILABLE:
        print("  ✅ 알림 라이브러리 사용 가능")
    else:
        print("  ⚠️ 알림 라이브러리 없음 - 설치 권장: pip install plyer")
    
    print()
    monitor()
