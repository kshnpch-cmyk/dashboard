import os
import re
import glob
import time
import json
import warnings
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# openpyxl CellStyle count 속성 오류 방지 패치
import openpyxl.styles.cell_style
_original_cell_style_init = openpyxl.styles.cell_style.CellStyle.__init__

def _patched_cell_style_init(self, *args, **kwargs):
    kwargs.pop('count', None)
    _original_cell_style_init(self, *args, **kwargs)

openpyxl.styles.cell_style.CellStyle.__init__ = _patched_cell_style_init

warnings.filterwarnings('ignore')

# 💡 GitHub Secrets 환경변수 수신 및 정규식 URL 파싱 (괄호/마크다운 자동 제거)
raw_url = os.environ.get("SUPABASE_URL", "https://zbilhsgfgyfrolveaego.supabase.co").strip()
url_match = re.search(r'https?://[^\s\)\>\]\"\']+', raw_url)
SUPABASE_URL = url_match.group(0) if url_match else "https://zbilhsgfgyfrolveaego.supabase.co"

SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

OMS_COMPANY_CODE = os.environ.get("OMS_COMPANY_CODE", "1000").strip()
OMS_ID = os.environ.get("OMS_ID", "1220503").strip()
OMS_PW = os.environ.get("OMS_PW", "theborn8@").strip()

# 1. KST 날짜 계산 및 지정 기간 파라미터 수신
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

start_date_obj = now_kst + timedelta(days=1)
end_date_obj = now_kst + timedelta(days=3) if now_kst.hour >= 11 else now_kst + timedelta(days=2)

target_start_date = os.environ.get('START_DATE') or start_date_obj.strftime("%Y/%m/%d")
target_end_date = os.environ.get('END_DATE') or end_date_obj.strftime("%Y/%m/%d")

# 2. 크롬 브라우저 다운로드 설정
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


def sync_to_supabase(combined_df):
    """수집된 Dataframe을 Supabase oms_orders 테이블로 업로드"""
    if combined_df is None or combined_df.empty:
        print("⚠️ Supabase에 업로드할 데이터가 없습니다.", flush=True)
        return

    print(f"🚀 Supabase 클라우드 DB 동기화를 시작합니다... (Target: {SUPABASE_URL})", flush=True)

    df_clean = combined_df.fillna('').astype(str)
    records = []

    for _, row in df_clean.iterrows():
        def parse_num(val):
            try:
                s = str(val).replace(',', '').strip()
                return float(s) if s else 0
            except:
                return 0

        def parse_date(val):
            s = str(val).split('T')[0].replace('/', '-').strip()
            return s if len(s) >= 8 else None

        # 💡 Supabase DB oms_orders 테이블 스키마 매핑
        record = {
            "order_date": parse_date(row.get("주문일자", "")),
            "delivery_date": parse_date(row.get("배송일자", "")),
            "store_code": str(row.get("거래처코드", row.get("점포코드", ""))),
            "store_name": str(row.get("거래처명", row.get("점포명", ""))),
            "brand_name": str(row.get("브랜드명", "")),
            "item_code": str(row.get("품목코드", "")),
            "item_name": str(row.get("품목명", "")),
            "qty": int(parse_num(row.get("수량", row.get("주문수량", 0)))),
            "price": parse_num(row.get("단가", row.get("공급단가", 0))),
            "total_amount": parse_num(row.get("금액", row.get("공급가액", 0)))
        }
        records.append(record)

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    batch_size = 1000
    total_records = len(records)
    endpoint = f"{SUPABASE_URL}/rest/v1/oms_orders"

    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        res = requests.post(endpoint, headers=headers, json=batch)

        if res.status_code in [200, 201]:
            print(f"✅ DB 동기화 완료: {min(i + batch_size, total_records)} / {total_records} 건", flush=True)
        else:
            print(f"❌ DB 동기화 오류 ({res.status_code}): {res.text}", flush=True)


try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] OMS 자동 수집 시작", flush=True)
    print(f"조회 지정 기간: {target_start_date} ~ {target_end_date}", flush=True)

    # 3. 로그인
    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2)
    driver.find_element(By.ID, 'companyCd').send_keys(OMS_COMPANY_CODE)
    driver.find_element(By.ID, 'userId').send_keys(OMS_ID)
    driver.find_element(By.ID, 'userPw').send_keys(OMS_PW + Keys.ENTER)
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
        driver.find_element(By.CSS_SELECTOR, "button.form_btn_search[data-shortcut='F2']").click()
    except Exception:
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.F2)
    time.sleep(6)

    # 6. 컬럼 헤더 영역 우클릭
    header_element = driver.find_element(By.CSS_SELECTOR, 'thead th') or driver.find_element(By.TAG_NAME, 'th')
    try:
        ActionChains(driver).context_click(header_element).perform()
    except Exception:
        pass
    time.sleep(1.5)

    # 7. '엑셀다운로드' 메뉴 클릭
    excel_btn = driver.find_element(By.XPATH, "//*[contains(text(), '엑셀다운로드')]")
    driver.execute_script("arguments[0].click();", excel_btn)
    time.sleep(2)

    # 8. SweetAlert 팝업 처리 및 파일 다운로드 실행
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

    # 9. 다운로드한 엑셀 파일 읽기
    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    if not list_of_files:
        raise Exception("다운로드 파일 수신 실패")

    latest_file = max(list_of_files, key=os.path.getctime)
    new_df = pd.read_excel(latest_file, engine='openpyxl')

    print(f"📥 수신된 원본 데이터: {len(new_df):,}행", flush=True)

    # 10. Supabase DB로 동기화 전송
    sync_to_supabase(new_df)

    # 임시 다운로드 파일 정제
    if os.path.exists(latest_file):
        os.remove(latest_file)

except Exception as e:
    print(f"❌ 오류 발생: {e}", flush=True)
    try:
        driver.save_screenshot("oms_result.png")
    except Exception:
        pass
finally:
    driver.quit()
