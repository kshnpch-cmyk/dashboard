import os
import time
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbykxUSe-5RA20YOav7F5ohYZrD1739O7FInVzP-vR_jB31iMIsyRj4HSo9-e0Oedc0q/exec"

# 1. 한국 시간(KST) 및 항상 다음날(오늘 + 1일) 날짜 자동 계산
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
target_date = now_kst + timedelta(days=1)

# 다음날 기준 탭 이름 및 날짜 문자열 세팅
target_tab_name = target_date.strftime("%Y%m") # 예: 202609
tomorrow_str = target_date.strftime("%Y/%m/%d") # 예: 2026/09/08

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] 동기화 진행 (기준 조회일: {tomorrow_str} / 탭: {target_tab_name})")

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

    # 4. 날짜 세팅 (수동 지정값이 없으면 '다음날' 적용)
    target_start_date = os.environ.get('START_DATE') or tomorrow_str
    target_end_date = os.environ.get('END_DATE') or tomorrow_str

    print(f"조회 기간 적용: {target_start_date} ~ {target_end_date}")
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
    except Exception:
        body = driver.find_element(By.TAG_NAME, 'body')
        body.send_keys(Keys.F2)

    time.sleep(6) # 데이터 로딩 대기

    # 6. 테이블 데이터 파싱 및 필터링
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    rows = []
    header = [
        "No.", "주문번호", "주문순번", "주문일자", "주문구분", "배송일자", 
        "거래처코드", "거래처명", "브랜드명", "화주사코드", "화주사명", 
        "센터코드", "센터명", "창고코드", "창고명", "품목코드", "품목명", "세트품목코드", "세트품목명", "품목온도"
    ]
    rows.append(header)

    table_rows = soup.select('tr') 
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td, th')]
        if cols and len(cols) >= 5:
            first_col = cols[0]
            second_col = cols[1] if len(cols) > 1 else ""
            
            if "indicator" in first_col.lower() or "summary" in first_col.lower():
                continue
                
            if second_col.startswith("OMS") or first_col.isdigit():
                rows.append(cols)

    # 7. 구글 시트로 페이로드 전송
    payload = {
        "tabName": target_tab_name,
        "data": rows
    }

    if len(rows) > 1:
        response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True)
        print(f"✅ 구글 시트 {target_tab_name} 탭 동기화 성공: {response.text}")
    else:
        print("❌ 조회된 데이터가 없어 시트 업데이트를 건너땁니다.")

except Exception as e:
    print(f"오류 발생: {e}")
finally:
    driver.quit()
