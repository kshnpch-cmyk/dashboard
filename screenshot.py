import os
import glob
import time
import requests
import warnings
import pandas as pd
from datetime import datetime, timedelta, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# 경고 메시지 비활성화
warnings.filterwarnings('ignore')

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycby2-YHNufhOlHJcIBspE1bljMNtIEf3aXoXWqqakUgqvndDCf0nT1MSVuZkopmuEOvK/exec"

# 1. KST 기준 날짜 및 시트 탭 이름 계산
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

start_date_obj = now_kst + timedelta(days=1)
if now_kst.hour >= 11:
    end_date_obj = now_kst + timedelta(days=3)
else:
    end_date_obj = now_kst + timedelta(days=2)

auto_start_str = start_date_obj.strftime("%Y/%m/%d")
auto_end_str = end_date_obj.strftime("%Y/%m/%d")

target_start_date = os.environ.get('START_DATE') or auto_start_str
target_end_date = os.environ.get('END_DATE') or auto_end_str

clean_start_date = target_start_date.replace('-', '/').replace('.', '/')
date_parts = clean_start_date.split('/')
if len(date_parts) >= 2:
    target_tab_name = f"{date_parts[0]}{date_parts[1].zfill(2)}"
else:
    target_tab_name = start_date_obj.strftime("%Y%m")

# 2. 크롬 다운로드 경로 설정
download_dir = os.getcwd()

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

options.add_experimental_option("prefs", {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
})

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

# Headless 모드 다운로드 권한 강제 승인
driver.command_executor._commands["send_command"] = ("POST", '/session/$sessionId/chromium/send_command')
params = {'cmd': 'Page.setDownloadBehavior', 'params': {'behavior': 'allow', 'downloadPath': download_dir}}
driver.execute_script("return null;")
driver.execute("send_command", params)

try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] OMS 엑셀 다운로드 동기화 시작")
    print(f"조회 지정 기간: {target_start_date} ~ {target_end_date} ➔ [저장 대상 시트 탭: '{target_tab_name}']")

    # 3. 로그인
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    driver.find_element(By.ID, 'companyCd').send_keys('1000')
    driver.find_element(By.ID, 'userId').send_keys('1220503')
    driver.find_element(By.ID, 'userPw').send_keys('theborn8@' + Keys.ENTER)
    time.sleep(4) 

    # 4. 메뉴 이동
    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    time.sleep(2) 

    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    time.sleep(5) 

    # 5. 날짜 세팅 및 조회
    js_script = f"""
        var rangeInput = document.getElementsByName('BOR111_dateRange')[0];
        if(rangeInput) {{
            rangeInput.value = '{target_start_date} ~ {target_end_date}';
            rangeInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        var startInput = document.getElementById('BOR111_startDt');
        var endInput = document.getElementById('BOR111_endDt');
        if(startInput) startInput.value = '{target_start_date}';
        if(endInput) endInput.value = '{target_end_date}';
    """
    driver.execute_script(js_script)
    time.sleep(2)

    try:
        search_btn = driver.find_element(By.CSS_SELECTOR, "button.form_btn_search[data-shortcut='F2']")
        search_btn.click()
    except Exception:
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.F2)

    time.sleep(6) # 데이터 로딩 대기

    # 6. 컬럼 헤더 영역 우클릭
    print("🖱️ [STEP 1/5] 컬럼 헤더 영역 우클릭 실행...")
    
    header_element = None
    for col_text in ['배송일자', '품목코드', '품목명', '거래처코드', '주문번호']:
        try:
            header_element = driver.find_element(By.XPATH, f"//th[contains(text(), '{col_text}')]")
            if header_element:
                break
        except Exception:
            continue

    if not header_element:
        header_element = driver.find_element(By.CSS_SELECTOR, 'thead th') or driver.find_element(By.TAG_NAME, 'th')

    try:
        ActionChains(driver).context_click(header_element).perform()
    except Exception:
        driver.execute_script("""
            var th = arguments[0];
            var event = new MouseEvent('contextmenu', {
                'bubbles': true,
                'cancelable': true,
                'view': window,
                'buttons': 2
            });
            th.dispatchEvent(event);
        """, header_element)

    time.sleep(1.5)

    # 7. '엑셀다운로드' 메뉴 클릭
    excel_btn = driver.find_element(By.XPATH, "//*[contains(text(), '엑셀다운로드')]")
    driver.execute_script("arguments[0].click();", excel_btn)
    print("🖱️ [STEP 2/5] '엑셀다운로드' 메뉴 클릭 완료. 파일명 입력 팝업 대기 중...")

    time.sleep(2)

    # 8. 파일명 입력 팝업 처리
    print("📝 [STEP 3/5] 파일명 입력 및 다운로드 요청...")
    
    try:
        swal_input = driver.find_element(By.CSS_SELECTOR, "input.swal2-input") or driver.find_element(By.CSS_SELECTOR, ".swal2-popup input")
        swal_input.clear()
        swal_input.send_keys("oms_download")
    except Exception:
        driver.execute_script("""
            var input = document.querySelector('.swal2-input') || document.querySelector('.swal2-popup input');
            if(input){
                input.value = 'oms_download';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }
        """)

    time.sleep(1)

    download_btn = driver.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
    driver.execute_script("arguments[0].click();", download_btn)

    print("⏳ 엑셀 파일 생성 대기 중...")
    time.sleep(5)

    # 8-1. 완료 팝업 [OK] 버튼 클릭
    print("🖱️ '다운로드 완료' 팝업 [OK] 버튼 클릭...")
    try:
        ok_btn = driver.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
        driver.execute_script("arguments[0].click();", ok_btn)
    except Exception:
        driver.execute_script("""
            var btns = document.querySelectorAll('button');
            for(var i=0; i<btns.length; i++){
                if(btns[i].innerText.trim() === 'OK'){
                    btns[i].click();
                    break;
                }
            }
        """)

    print("📥 최종 파일 저장 수신 대기 중...")
    time.sleep(5)

    driver.save_screenshot("oms_result.png")

    # 9. 다운로드된 엑셀 파일 열기 및 세부 디버깅 파싱
    print("\n--------------------------------------------------")
    print("📂 [STEP 4/5] 다운로드된 엑셀 파일 열기 및 정밀 파싱 분석")
    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    
    if not list_of_files:
        raise Exception("다운로드 폴더 내에서 엑셀 파일(.xlsx/.xls)을 찾지 못했습니다.")

    latest_file = max(list_of_files, key=os.path.getctime)
    file_size_bytes = os.path.getsize(latest_file)
    print(f"📄 대상 파일 감지: {os.path.basename(latest_file)} (용량: {file_size_bytes:,} bytes)")

    # 🔍 세부 분석 로그: 파일 헤더 검사
    file_preview = ""
    file_encoding = 'utf-8'
    for enc in ['utf-8', 'euc-kr', 'cp949', 'utf-16']:
        try:
            with open(latest_file, 'r', encoding=enc) as f:
                file_preview = f.read(500)
                file_encoding = enc
                print(f"🔍 [파일 분석] 성공한 인코딩 포맷: '{enc}'")
                print(f"🔍 [파일 분석] 헤더 미리보기(500자):\n{file_preview[:200]}...\n")
                break
        except Exception:
            continue

    df = None

    # 시도 A: BeautifulSoup 기반 HTML 직접 수집 (euc-kr / cp949 인코딩 완벽 대응)
    if "<table" in file_preview.lower() or "<html" in file_preview.lower() or "xml" in file_preview.lower():
        print("💡 [파싱 전략 A] HTML/XML 표 형식 감지됨 -> BeautifulSoup 파서 작동")
        try:
            from bs4 import BeautifulSoup
            with open(latest_file, 'r', encoding=file_encoding, errors='ignore') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')
            
            tables = soup.find_all('table')
            print(f"    └─ 발견된 <table> 태그 개수: {len(tables)}개")
            
            for idx, table in enumerate(tables):
                rows = []
                for tr in table.find_all('tr'):
                    cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                    if cells:
                        rows.append(cells)
                if len(rows) > 1: # 데이터가 유의미하게 존재하는 테이블 선택
                    df = pd.DataFrame(rows[1:], columns=rows[0])
                    print(f"    └─ {idx+1}번째 테이블에서 데이터 추출 성공! (행: {len(rows)-1}, 열: {len(rows[0])})")
                    break
        except Exception as e:
            print(f"    ❌ [전략 A 실패 로그]: {e}")

    # 시도 B: Pandas 다중 엔진 자동 순회 시도
    if df is None or df.empty:
        print("💡 [파싱 전략 B] Pandas 범용 엔진 파싱 순회 시도")
        parse_methods = [
            ("pd.read_html (lxml)", lambda f: pd.read_html(f, flavor='lxml', encoding=file_encoding)[0]),
            ("pd.read_html (html5lib)", lambda f: pd.read_html(f, flavor='html5lib', encoding=file_encoding)[0]),
            ("openpyxl", lambda f: pd.read_excel(f, engine='openpyxl')),
            ("xlrd", lambda f: pd.read_excel(f, engine='xlrd')),
            ("default_read_excel", lambda f: pd.read_excel(f))
        ]

        for name, method in parse_methods:
            try:
                res_df = method(latest_file)
                if not res_df.empty:
                    df = res_df
                    print(f"    └─ [{name}] 엔진으로 데이터 수집 성공!")
                    break
            except Exception as e_engine:
                print(f"    └─ [{name}] 엔진 실패 원인: {e_engine}")

    if df is None or df.empty:
        raise Exception("모든 파싱 방법으로 읽기에 실패했습니다. 파일 헤더 정보를 확인하세요.")

    # 데이터 정제 및 가공
    df = df.fillna('')
    df = df.astype(str)

    header = df.columns.tolist()
    data_rows = df.values.tolist()
    final_rows = [header] + data_rows

    print(f"\n📋 [데이터 복사 완성] 헤더 {len(header)}열 / 데이터 {len(data_rows)}행 추출 완료")
    if header:
        print(f"    └─ 헤더 추출 결과: {header[:5]}")
    if data_rows:
        print(f"    └─ 데이터 샘플(1행): {data_rows[0][:3]}")

    # 10. 구글 스프레드시트 Webhook 전송
    print("\n--------------------------------------------------")
    print(f"🚀 [STEP 5/5] 구글 시트 '{target_tab_name}' 탭으로 데이터 전송 중...")
    
    payload = {
        "tabName": target_tab_name,
        "data": final_rows
    }

    start_time = time.time()
    response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True, timeout=30)
    elapsed = round(time.time() - start_time, 2)

    print(f"📡 구글 시트 서버 응답 코드: {response.status_code} (소요시간: {elapsed}초)")
    print(f"✅ 구글 시트 웹훅 처리 결과: {response.text}")
    print("--------------------------------------------------\n")

    # 파일 정리
    if os.path.exists(latest_file):
        os.remove(latest_file)

except Exception as e:
    print(f"\n❌ [최종 오류 발생]: {e}")
    try:
        driver.save_screenshot("oms_result.png")
        print("📸 에러 시점 화면 캡처 완료: oms_result.png")
    except Exception:
        pass
finally:
    driver.quit()
