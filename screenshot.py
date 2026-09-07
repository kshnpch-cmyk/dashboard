import os
import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ⚠️ 배포하신 구글 앱스 스크립트 웹 앱 URL을 아래에 넣어주세요.
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbw--_Gs008VRb5-aMBcF1GY7QqUhLnJ87ryxElvHU7K_497ozytdZlL2LLFYEcik4eU/exec"

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

    time.sleep(6) # 데이터 렌더링을 위해 충분히 대기

    # 5. HTML 데이터 추출
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    rows = []
    # 모든 테이블 행 파싱
    table_rows = soup.select('tr') 
    for tr in table_rows:
        cols = [td.get_text(strip=True) for td in tr.select('td, th')]
        # 완전 빈 행이 아니거나 의미 있는 셀 데이터를 가졌을 경우 추가
        if cols and any(cols): 
            rows.append(cols)

    print(f"파싱 완료된 데이터 행 수: {len(rows)}개")

    # 6. 구글 시트 웹훅 전송
    if not WEBHOOK_URL or "여기에_복사한_URL" in WEBHOOK_URL:
        print("❌ WEBHOOK_URL이 설정되지 않았습니다. URL을 확인해 주세요.")
    elif not rows:
        print("❌ 파싱된 테이블 데이터가 존재하지 않아 전송을 스킵합니다.")
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
