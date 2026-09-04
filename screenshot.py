from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time

# 1. 크롬 브라우저 옵션 설정 (GitHub 가상 컴퓨터용)
options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080) # 화면 크기를 넉넉하게 설정

try:
    # 2. 관리자 로그인 페이지 접속
    print("사이트 접속 중...")
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    # 3. 로그인 정보 입력 및 엔터키 누르기
    # [주의] 아래 3개의 '실제ID' 부분은 F12로 찾으신 진짜 영문 이름으로 꼭 바꿔주세요!
    driver.find_element(By.ID, '회사코드_실제ID').send_keys('9000')
    driver.find_element(By.ID, '아이디_실제ID').send_keys('admin')
    driver.find_element(By.ID, '비밀번호_실제ID').send_keys('1234' + Keys.ENTER)
    
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

    # 6. 최종 화면 캡처 저장
    driver.save_screenshot('capture.png')
    print("성공적으로 캡처가 완료되었습니다!")

except Exception as e:
    # 에러가 발생하면 무슨 화면에서 막혔는지 확인하기 위해 에러 화면을 캡처합니다.
    print(f"오류가 발생했습니다: {e}")
    driver.save_screenshot('capture.png') 
    
finally:
    # 7. 브라우저 종료
    driver.quit()
