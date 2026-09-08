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

# openpyxl 패치
import openpyxl.styles.cell_style
_original_cell_style_init = openpyxl.styles.cell_style.CellStyle.__init__
def _patched_cell_style_init(self, *args, **kwargs):
    kwargs.pop('count', None)
    _original_cell_style_init(self, *args, **kwargs)
openpyxl.styles.cell_style.CellStyle.__init__ = _patched_cell_style_init

warnings.filterwarnings('ignore')

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycby2-YHNufhOlHJcIBspE1bljMNtIEf3aXoXWqqakUgqvndDCf0nT1MSVuZkopmuEOvK/exec"

# 1. KST 및 날짜 계산
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

start_date_obj = now_kst + timedelta(days=1)
end_date_obj = now_kst + timedelta(days=3) if now_kst.hour >= 11 else now_kst + timedelta(days=2)

target_start_date = os.environ.get('START_DATE') or start_date_obj.strftime("%Y/%m/%d")
target_end_date = os.environ.get('END_DATE') or end_date_obj.strftime("%Y/%m/%d")

clean_start_date = target_start_date.replace('-', '/').replace('.', '/')
date_parts = clean_start_date.split('/')
target_tab_name = f"{date_parts[0]}{date_parts[1].zfill(2)}" if len(date_parts) >= 2 else start_date_obj.strftime("%Y%m")

# 2. 크롬 환경 구성
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
driver.command_executor._commands["send_command"] = ("POST", '/session/$sessionId/chromium/send_command')
params = {'cmd': 'Page.setDownloadBehavior', 'params': {'behavior': 'allow', 'downloadPath': download_dir}}
driver.execute_script("return null;")
driver.execute("send_command", params)

try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] OMS GitHub DB 동기화 시작")

    # 3~8. 로그인 및 엑셀 다운로드 (동일)
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2)
    driver.find_element(By.ID, 'companyCd').send_keys('1000')
    driver.find_element(By.ID, 'userId').send_keys('1220503')
    driver.find_element(By.ID, 'userPw').send_keys('theborn8@' + Keys.ENTER)
    time.sleep(4)

    driver.find_element(By.CSS_SELECTOR, "a[data-menu-id='BOR']").click()
    time.sleep(2)
    driver.find_element(By.XPATH, "//*[contains(text(), '기간별 주문현황')]").click()
    time.sleep(5)

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
        driver.find_element(By.CSS_SELECTOR, "button.form_btn_search[data-shortcut='F2']").click()
    except Exception:
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.F2)
    time.sleep(6)

    header_element = driver.find_element(By.CSS_SELECTOR, 'thead th') or driver.find_element(By.TAG_NAME, 'th')
    try:
        ActionChains(driver).context_click(header_element).perform()
    except Exception:
        pass
    time.sleep(1.5)

    excel_btn = driver.find_element(By.XPATH, "//*[contains(text(), '엑셀다운로드')]")
    driver.execute_script("arguments[0].click();", excel_btn)
    time.sleep(2)

    try:
        swal_input = driver.find_element(By.CSS_SELECTOR, "input.swal2-input")
        swal_input.clear()
        swal_input.send_keys("oms_download")
    except Exception:
        pass
    time.sleep(1)

    download_btn = driver.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
    driver.execute_script("arguments[0].click();", download_btn)
    time.sleep(5)

    try:
        ok_btn = driver.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
        driver.execute_script("arguments[0].click();", ok_btn)
    except Exception:
        pass
    time.sleep(5)

    # 9. 엑셀 파일 열기
    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    if not list_of_files:
        raise Exception("다운로드 파일 찾기 실패")

    latest_file = max(list_of_files, key=os.path.getctime)
    new_df = pd.read_excel(latest_file, engine='openpyxl')
    new_df = new_df.fillna('').astype(str)

    print(f"📥 당일 다운로드 원본 데이터: {len(new_df):,}행 수신")

    # 💡 10. GitHub 데이터베이스 (Parquet 파일) 업데이트
    db_file_path = "oms_database.parquet"
    
    if os.path.exists(db_file_path):
        existing_df = pd.read_parquet(db_file_path)
        print(f"📂 기존 GitHub DB 로드 완료: {len(existing_df):,}행 누적됨")
        # 기존 DB + 새 데이터 병합 후 중복 제거 (주문번호 + 주문순번 기준)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        if '주문번호' in combined_df.columns and '주문순번' in combined_df.columns:
            combined_df = combined_df.drop_duplicates(subset=['주문번호', '주문순번'], keep='last')
        else:
            combined_df = combined_df.drop_duplicates()
    else:
        combined_df = new_df

    # Parquet 압축 DB로 저장
    combined_df.to_parquet(db_file_path, index=False, compression='snappy')
    db_size_mb = round(os.path.getsize(db_file_path) / (1024 * 1024), 2)
    print(f"💾 GitHub DB 업데이트 완료: 총 {len(combined_df):,}행 누적 저장됨 (파일용량: {db_size_mb} MB)")

    # 💡 11. 구글 시트용 '요약/집계 데이터' 생성 (대시보드가 멈추지 않는 핵심)
    # 원본 3.5만 건 대신 일자/품목/거래처별로 그룹화하여 데이터 크기를 1/100 수준으로 축소
    print("📊 구글 시트 전송용 통계/집계 데이터 가공 중...")
    
    # 예시: 일자별/품목별/거래처별 주문 건수 요약표 (필요 컬럼에 따라 자동 조정)
    group_cols = [c for c in ['배송일자', '거래처명', '품목명', '브랜드명'] if c in combined_df.columns]
    
    if group_cols:
        summary_df = combined_df.groupby(group_cols).size().reset_index(name='주문건수')
    else:
        summary_df = combined_df.head(1000) # 기본 fallback

    summary_df = summary_df.fillna('').astype(str)
    header = summary_df.columns.tolist()
    data_rows = summary_df.values.tolist()
    final_rows = [header] + data_rows

    print(f"🚀 구글 시트 전송 규모: {len(final_rows):,}행 (요약 집계 데이터)")

    # 12. 구글 시트로 집계 데이터 전송
    payload = {
        "tabName": target_tab_name,
        "data": final_rows
    }

    response = requests.post(WEBHOOK_URL, json=payload, allow_redirects=True, timeout=60)
    print(f"✅ 구글 시트 전송 완료: {response.text}")

    if os.path.exists(latest_file):
        os.remove(latest_file)

except Exception as e:
    print(f"❌ 오류 발생: {e}")
    try:
        driver.save_screenshot("oms_result.png")
    except Exception:
        pass
finally:
    driver.quit()
