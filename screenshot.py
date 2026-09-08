import os
import time
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxZx_c4oiyDksK2lTKotOl7nkd--MthKng_bRQkztjXECQQqGko3HzRxzuv6hFkNlKj/exec"

# 1. KST 기준 시간별 자동 조회 범위 및 동적 탭 이름 계산
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

start_date_obj = now_kst + timedelta(days=1)
if now_kst.hour >= 11:
    end_date_obj = now_kst + timedelta(days=3)
else:
    end_date_obj = now_kst + timedelta(days=2)

auto_start_str = start_date_obj.strftime("%Y/%m/%d")
auto_end_str = end_date_obj.strftime("%Y/%m/%d")

# 💡 수동 지정 시 탭 이름(YYYYMM) 파싱 로직
target_start_date = os.environ.get('START_DATE') or auto_start_str
target_end_date = os.environ.get('END_DATE') or auto_end_str

clean_start_date = target_start_date.replace('-', '/').replace('.', '/')
date_parts = clean_start_date.split('/')
if len(date_parts) >= 2:
    target_tab_name = f"{date_parts[0]}{date_parts[1].zfill(2)}"
else:
    target_tab_name = start_date_obj.strftime("%Y%m")

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] 동기화 진행")
    print(f"조회 지정 기간: {target_start_date} ~ {target_end_date} ➔ [저장 대상 시트 탭: '{target_tab_name}']")

    # 2. 로그인
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    driver.find_element(By.ID, 'companyCd').send_keys('9000')
    driver.find_element(By.ID, 'userId').send_keys('admin')
    driver.find_element(By.ID, 'userPw').send_keys('1234' + Keys.ENTER)
    time.sleep(4) 

    # 3. 메뉴 이동
    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    time.sleep(2) 

    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    time.sleep(5) 

    # 4. 날짜 세팅
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

    # 5. 조회 버튼 클릭
    try:
        search_btn = driver.find_element(By.CSS_SELECTOR, "button.form_btn_search[data-shortcut='F2']")
        search_btn.click()
        print("조회 버튼 클릭 성공")
    except Exception:
        body = driver.find_element(By.TAG_NAME, 'body')
        body.send_keys(Keys.F2)

    time.sleep(10)

    # 6. 헤더 및 전체 컬럼(규격, 세액, 부가세, 등록일시, 등록자ID 등) 끝까지 완벽 파싱
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    rows = []
    
    # 6-1. 첫 번째 데이터 행의 컬럼 수에 맞춰 헤더 태그 전체 탐색
    table_rows = soup.select('tbody tr') or soup.select('tr')
    sample_cols_len = 0
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td')]
        if cols and any("OMS" in c for c in cols):
            sample_cols_len = len(cols)
            break

    # 💡 모든 th 태그 수집 및 빈 셀명 보정
    th_elements = soup.select('thead tr th') or soup.select('tr th') or soup.select('th')
    dynamic_header = []
    
    for th in th_elements:
        txt = th.get_text(strip=True)
        dynamic_header.append(txt if txt else f"컬럼_{len(dynamic_header)+1}")

    if dynamic_header and dynamic_header[0] != "No.":
        dynamic_header.insert(0, "No.")

    # 6-2. 실제 데이터 행 수집
    row_count = 1
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td')]
        if cols and len(cols) >= 5:
            row_str = "".join(cols)
            if "indicator" in row_str.lower() or "summary" in row_str.lower() or "summa" in row_str.lower():
                continue
            
            if any("OMS" in c for c in cols):
                if not cols[0].isdigit():
                    cols.insert(0, str(row_count))
                    row_count += 1
                rows.append(cols)

    # 💡 헤더 길이가 실제 데이터 길이보다 짧을 경우 끝까지 자동 확장 처리
    max_data_len = max([len(r) for r in rows]) if rows else 0
    while len(dynamic_header) < max_data_len:
        dynamic_header.append(f"추가컬럼_{len(dynamic_header)+1}")

    rows.insert(0, dynamic_header)

    print(f"파싱 완료된 총 헤더 컬럼 수: {len(dynamic_header)}개")
    print(f"파싱 완료된 총 데이터 행 수: {len(rows)-1}개")

    # 7. 구글 시트로 페이로드 전송
    payload = {
        "tabName": target_tab_name,
        "data": rows
    }

    print(f"구글 시트 '{target_tab_name}' 탭으로 전송 중...")
    response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True)
    print(f"✅ 동기화 결과: {response.text}")

except Exception as e:
    print(f"오류 발생: {e}")
finally:
    driver.quit()
