import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# 1. 크롬 브라우저 옵션 설정 (GitHub Actions 가상 환경용)
options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080) # 화면 크기 설정

try:
    # 2. 관리자 로그인 페이지 접속
    print("사이트 접속 중...")
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    # 3. 로그인 정보 입력 및 엔터키로 진행
    driver.find_element(By.ID, 'companyCd').send_keys('9000')
    driver.find_element(By.ID, 'userId').send_keys('admin')
    driver.find_element(By.ID, 'userPw').send_keys('1234' + Keys.ENTER)
    
    print("로그인 완료! 메인 화면 진입 대기 중...")
    time.sleep(4) 

    # 4. '주문관리' 메인 메뉴 클릭
    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    print("주문관리 메뉴 클릭! 하위 메뉴 펼침 대기 중...")
    time.sleep(2) 

    # 5. '기간별 주문현황' 하위 메뉴 클릭
    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    print("기간별 주문현황 클릭! 페이지 로딩 대기 중...")
    time.sleep(5) 

    # 6. 대시보드에서 전달받은 날짜 환경변수 가져오기 (예: 2026/09/01 ~ 2026/09/30)
    target_start_date = os.environ.get('START_DATE')
    target_end_date = os.environ.get('END_DATE')

    if target_start_date and target_end_date:
        print(f"전달받은 날짜 세팅 중: {target_start_date} ~ {target_end_date}")
        
        # 자바스크립트를 이용해 보이지 않는 input 및 화면의 dateRange 입력창에 날짜 대입 & change 이벤트 주사
        js_script = f"""
            // 화면의 배송일자 박스 값 변경
            var rangeInput = document.getElementsByName('BOR111_dateRange')[0];
            if(rangeInput) {{
                rangeInput.value = '{target_start_date} ~ {target_end_date}';
                rangeInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            
            // 실제 데이터 전달용 숨겨진 startDt, endDt 값 변경
            var startInput = document.getElementById('BOR111_startDt');
            var endInput = document.getElementById('BOR111_endDt');
            if(startInput) {{
                startInput.value = '{target_start_date}';
                startInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            if(endInput) {{
                endInput.value = '{target_end_date}';
                endInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}

            // jQuery 이벤트도 함께 발생시켜 화면 갱신 유도
            if(window.jQuery) {{
                jQuery('input[name="BOR111_dateRange"]').trigger('change');
                jQuery('#BOR111_startDt').trigger('change');
                jQuery('#BOR111_endDt').trigger('change');
            }}
        """
        driver.execute_script(js_script)
        time.sleep(2)
        
        # 7. 조회 버튼 클릭하여 새로고침 적용
        try:
            search_btn = driver.find_element(By.XPATH, "//button[contains(., '조회')] | //a[contains(., '조회')] | //input[@value='조회']")
            search_btn.click()
            print("조회 버튼 클릭 완료!")
            time.sleep(5) # 데이터 로딩 대기
        except Exception as btn_err:
            print(f"조회 버튼 클릭 스킵: {btn_err}")

    # 8. 최종 화면 캡처 및 저장
    driver.save_screenshot('capture.png')
    print("성공적으로 캡처가 완료되었습니다!")

except Exception as e:
    print(f"오류가 발생했습니다: {e}")
    driver.save_screenshot('capture.png') 
    
finally:
    driver.quit()
