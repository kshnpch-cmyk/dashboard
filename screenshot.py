from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

try:
    print("사이트 접속 중...")
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2) 

    # 알려주신 실제 ID 적용 완료
    driver.find_element(By.ID, 'companyCd').send_keys('9000')
    driver.find_element(By.ID, 'userId').send_keys('admin')
    driver.find_element(By.ID, 'userPw').send_keys('1234' + Keys.ENTER)
    
    print("로그인 완료! 메인 화면 진입 대기 중...")
    time.sleep(4) 

    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    print("주문관리 메뉴 클릭! 하위 메뉴 펼침 대기 중...")
    time.sleep(2) 

    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    print("기간별 주문현황 클릭! 페이지 로딩 대기 중...")
    time.sleep(5) 

    driver.save_screenshot('capture.png')
    print("성공적으로 캡처가 완료되었습니다!")

except Exception as e:
    print(f"오류가 발생했습니다: {e}")
    driver.save_screenshot('capture.png') 
    
finally:
    driver.quit()
