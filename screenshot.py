import os
import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbykxUSe-5RA20YOav7F5ohYZrD1739O7FInVzP-vR_jB31iMIsyRj4HSo9-e0Oedc0q/exec"

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

try:
    # 1. 로그인
    print("사이트 접속 중...")
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    driver.find_element(By.ID, 'companyCd').send_keys('9000')
    driver.find_element(By.ID, 'userId').send_keys('admin')
    driver.find_element(By.ID, 'userPw').send_keys('1234' + Keys.ENTER)
    time.sleep(4) 

    # 2. 메뉴 이동
    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    time.sleep(2) 

    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    time.sleep(5) 

    # 3. 날짜 설정
    target_start_date = os.environ.get('START_DATE', '2026/08/01')
    target_end_date = os.environ.get('END_DATE', '2026/08/31')

    print(f"날짜 세팅 진행: {target_start_date} ~ {target_end_date}")
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

    # 4. 조회 버튼 클릭
    print("조회 버튼 클릭 시도 중...")
    try:
        search_btn = driver.find_element(By.CSS_SELECTOR, "button.form_btn_search[data-shortcut='F2']")
        search_btn.click()
        print("✅ form_btn_search 버튼 클릭 성공!")
    except Exception as e:
        print(f"버튼 직접 클릭 실패, F2 키 입력으로 대체: {e}")
        body = driver.find_element(By.TAG_NAME, 'body')
        body.send_keys(Keys.F2)

    time.sleep(6) # 데이터 로딩 대기

    # 5. 정밀 데이터 추출 및 정제 (잡동사니 필터링)
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    rows = []
    
    # 올바른 헤더 라인 추가
    header = [
        "No.", "주문번호", "주문순번", "주문일자", "주문구분", "배송일자", 
        "거래처코드", "거래처명", "브랜드명", "화주사코드", "화주사명", 
        "센터코드", "센터명", "창고코드", "창고명", "품목코드", "품목명", "세트품목코드", "세트품목명", "품목온도"
    ]
    rows.append(header)

    # 모든 행 중 OMS 데이터가 시작하는 실제 목록만 필터링
    table_rows = soup.select('tr') 
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td, th')]
        
        # 💡 정제 조건:
        # 1. OMS 주문번호 패턴(OMS로 시작)이 들어있거나,
        # 2. 순수한 숫자로 시작하는 실제 주문 데이터 행만 픽업
        if cols and len(cols) >= 5:
            first_col = cols[0]
            second_col = cols[1] if len(cols) > 1 else ""
            
            # 'indicator column' 등 불필요한 하단 노이즈 제거
            if "indicator" in first_col.lower() or "summary" in first_col.lower():
                continue
                
            # 실제 주문 데이터 행 추출 (주문번호 OMS 포함 또는 첫 열이 숫자)
            if second_col.startswith("OMS") or first_col.isdigit():
                rows.append(cols)

    print(f"정제 완료된 깨끗한 데이터 행 수: {len(rows)}개")

    # 6. 구글 앱스 스크립트 웹훅 전송
    if len(rows) <= 1: # 헤더만 있는 경우
        print("❌ 파싱된 실제 주문 데이터가 없어 전송을 스킵합니다.")
    else:
        print("웹훅을 통해 구글 시트로 데이터 전송 중...")
        response = requests.post(WEBHOOK_URL, json=rows, allow_redirects=True)
        print(f"응답 상태 코드: {response.status_code}")
        print(f"구글 시트 응답 내용: {response.text}")

    driver.save_screenshot('capture.png')

except Exception as e:
    print(f"오류 발생: {e}")
    driver.save_screenshot('capture.png') 
    
finally:
    driver.quit()
