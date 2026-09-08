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
    print("🖱️ 컬럼 헤더 영역 우클릭 실행...")
    
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
    print("🖱️ '엑셀다운로드' 메뉴 클릭 완료. SweetAlert2 팝업 대기 중...")

    time.sleep(2)

    # 8. SweetAlert2 팝업 파일명 입력 및 [다운로드](swal2-confirm) 버튼 정밀 클릭
    print("📝 SweetAlert2 다운로드 버튼 클릭 중...")
    
    # 팝업창 내 input에 파일명 입력
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

    # swal2-confirm 버튼 클릭
    download_btn = driver.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
    driver.execute_script("arguments[0].click();", download_btn)

    print("📥 SweetAlert2 [다운로드] 버튼 클릭 성공! 엑셀 파일 수신 대기 중...")
    time.sleep(8) # 엑셀 다운로드 완료 대기

    driver.save_screenshot("oms_result.png")

    # 9. 다운로드된 엑셀 파일 찾기 및 pandas 파싱
    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    
    if not list_of_files:
        raise Exception("다운로드된 엑셀 파일을 찾지 못했습니다.")

    latest_file = max(list_of_files, key=os.path.getctime)
    print(f"📄 추출된 엑셀 파일: {latest_file}")

    df = pd.read_excel(latest_file)
    df = df.fillna('')

    header = df.columns.tolist()
    data_rows = df.values.tolist()
    final_rows = [header] + data_rows

    print(f"✅ 엑셀 파싱 성공! - 헤더: {len(header)}열 / 총 데이터: {len(data_rows)}행")

    # 10. 구글 시트로 데이터 전송
    payload = {
        "tabName": target_tab_name,
        "data": final_rows
    }

    print(f"구글 시트 '{target_tab_name}' 탭으로 전송 중...")
    response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True)
    print(f"✅ 동기화 결과: {response.text}")

    # 다운로드 파일 정리 삭제
    if os.path.exists(latest_file):
        os.remove(latest_file)

except Exception as e:
    print(f"❌ 오류 발생: {e}")
    try:
        driver.save_screenshot("oms_result.png")
        print("📸 에러 시점 화면 캡처 완료: oms_result.png")
    except Exception:
        pass
finally:
    driver.quit()
