import os
import glob
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycby2-YHNufhOlHJcIBspE1bljMNtIEf3aXoXWqqakUgqvndDCf0nT1MSVuZkopmuEOvK/exec"

# 1. KST 기준 조회 범위 및 탭 이름 계산
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

# 2. 크롬 다운로드 경로 설정 (현재 작업 디렉터리로 지정)
download_dir = os.getcwd()

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

# 다운로드 자동 승인 설정
options.add_experimental_option("prefs", {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
})

driver = webdriver.Chrome(options=options)
driver.set_window_size(1920, 1080)

# 다운로드 권한 강제 적용 (Headless 모드 대응)
driver.command_executor._commands["send_command"] = ("POST", '/session/$sessionId/chromium/send_command')
params = {'cmd': 'Page.setDownloadBehavior', 'params': {'behavior': 'allow', 'downloadPath': download_dir}}
driver.execute_script("return null;")
driver.execute("send_command", params)

try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] 엑셀 추출 동기화 진행")
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

    time.sleep(6) # 데이터 조회 대기

    # 6. 우클릭 후 '엑셀다운로드' 버튼 클릭
    print("🖱️ 그리드 우클릭 및 엑셀 다운로드 실행...")
    
    # 그리드 바디 요소 찾기
    grid_element = driver.find_element(By.CSS_SELECTOR, 'tbody tr td') or driver.find_element(By.TAG_NAME, 'body')
    
    # 우클릭 이벤트 발생
    actions = ActionChains(driver)
    actions.context_click(grid_element).perform()
    time.sleep(1.5)

    # 팝업 메뉴에서 '엑셀다운로드' 클릭
    excel_btn = driver.find_element(By.XPATH, "//*[contains(text(), '엑셀다운로드')]")
    excel_btn.click()
    print("📥 엑셀 다운로드 요청 완료. 파일 수신 대기 중...")
    
    time.sleep(6) # 엑셀 파일 다운로드 완료 대기

    # 6-1. 스크린샷 캡처
    driver.save_screenshot("oms_result.png")

    # 7. 다운로드된 엑셀 파일 찾아 파싱하기
    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    
    if not list_of_files:
        raise Exception("다운로드된 엑셀 파일을 찾지 못했습니다.")

    latest_file = max(list_of_files, key=os.path.getctime)
    print(f"📄 추출할 엑셀 파일: {latest_file}")

    # pandas로 엑셀 읽기
    df = pd.read_excel(latest_file)
    df = df.fillna('') # 빈값을 빈 문자열로 처리

    # 헤더와 데이터 분리 후 리스트 변환
    header = df.columns.tolist()
    data_rows = df.values.tolist()

    # 구글 시트로 보낼 최종 2차원 배열 데이터 구성
    final_rows = [header] + data_rows

    print(f"✅ 엑셀 파싱 완료 - 총 헤더 컬럼 수: {len(header)}개 / 데이터 행 수: {len(data_rows)}개")

    # 8. 구글 시트로 페이로드 전송
    payload = {
        "tabName": target_tab_name,
        "data": final_rows
    }

    print(f"구글 시트 '{target_tab_name}' 탭으로 전송 중...")
    response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True)
    print(f"✅ 동기화 결과: {response.text}")

    # 사용한 엑셀 파일 정리 삭제
    if os.path.exists(latest_file):
        os.remove(latest_file)

except Exception as e:
    print(f"❌ 오류 발생: {e}")
finally:
    driver.quit()
