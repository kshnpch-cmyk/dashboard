import os
import sys
import time
import re
import logging
from datetime import datetime
import pandas as pd
import requests

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://zbilhsgfgyfrolveaego.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "sb_publishable_OWW3nk7m7Vy3Ex0205ypgg_IG3xdBjN")

OMS_LOGIN_URL = "https://oms.theborn.co.kr/login.do"
OMS_ORDER_LIST_URL = "https://oms.theborn.co.kr/order/orderList.do"

OMS_COMPANY_CODE = os.environ.get("OMS_COMPANY_CODE", "")
OMS_ID = os.environ.get("OMS_ID", os.environ.get("OMS_USER_ID", ""))
OMS_PW = os.environ.get("OMS_PW", os.environ.get("OMS_USER_PW", ""))

def clean_int(val):
    if not val or pd.isna(val): return 0
    try:
        s = re.sub(r'[^0-9.-]', '', str(val))
        return int(float(s)) if s else 0
    except Exception: return 0

def clean_str(val):
    if not val or pd.isna(val): return ""
    return str(val).strip()

def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    download_dir = os.getcwd()
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

def delete_existing_range(start_date_hyphen, end_date_hyphen):
    headers = { "apikey": SUPABASE_SECRET_KEY, "Authorization": f"Bearer {SUPABASE_SECRET_KEY}" }
    delete_url = f"{SUPABASE_URL}/rest/v1/oms_orders?delivery_date=gte.{start_date_hyphen}&delivery_date=lte.{end_date_hyphen}"
    try:
        logging.info(f"🧹 [선-삭제] 기존 배송일자 데이터 삭제 ({start_date_hyphen} ~ {end_date_hyphen})...")
        res = requests.delete(delete_url, headers=headers, timeout=30)
        if res.status_code in [200, 204]:
            logging.info(f"🗑️ 기존 데이터 삭제 완료")
    except Exception as e:
        logging.error(f"❌ 삭제 중 오류: {e}")

def sync_to_supabase(records, batch_size=1000):
    if not records: return
    total = len(records)
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    url = f"{SUPABASE_URL}/rest/v1/oms_orders"
    logging.info(f"🚀 Supabase DB 업로드 시작 (총 {total:,}건)")

    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        try:
            res = requests.post(url, headers=headers, json=batch, timeout=60)
            current_count = min(i + batch_size, total)
            if res.status_code in [200, 201]:
                logging.info(f"✅ DB 업로드 중: {current_count} / {total} 건")
        except Exception as e:
            logging.error(f"❌ 업로드 통신 에러: {e}")

def run():
    # 날짜 인자 바인딩
    start_date = os.environ.get("START_DATE") or (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else datetime.now().strftime('%Y/%m/%d'))
    end_date = os.environ.get("END_DATE") or (sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else start_date)

    start_date = start_date.replace('-', '/')
    end_date = end_date.replace('-', '/')

    start_date_hyphen = start_date.replace('/', '-')
    end_date_hyphen = end_date.replace('/', '-')

    logging.info(f"🚀 OMS 크롤링 시작 [조회 기간: {start_date} ~ {end_date}]")

    driver = None
    try:
        driver = get_chrome_driver()
        wait = WebDriverWait(driver, 20)

        # 1. 로그인
        driver.get(OMS_LOGIN_URL)
        time.sleep(2)

        if OMS_COMPANY_CODE:
            for c_id in ["companyCode", "compCode", "corpCode"]:
                try:
                    c_elem = driver.find_element(By.ID, c_id)
                    if c_elem:
                        c_elem.clear()
                        c_elem.send_keys(OMS_COMPANY_CODE)
                        break
                except Exception: pass

        id_input = wait.until(EC.presence_of_element_located((By.ID, "userId")))
        pw_input = driver.find_element(By.ID, "userPw")

        id_input.clear()
        id_input.send_keys(OMS_ID)
        pw_input.clear()
        pw_input.send_keys(OMS_PW)
        pw_input.send_keys(Keys.RETURN)
        time.sleep(4)

        try:
            alert = driver.switch_to.alert
            alert.accept()
            time.sleep(2)
        except Exception: pass

        # 2. 주문 목록 이동
        driver.get(OMS_ORDER_LIST_URL)
        time.sleep(4)

        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes:
            driver.switch_to.frame(0)

        # 3. 날짜 세팅 및 조회
        js_set_dates = """
            var s = document.getElementById('BOR111_startDt') || document.getElementById('startDate');
            var e = document.getElementById('BOR111_endDt') || document.getElementById('endDate');
            if(s) { s.value = arguments[0]; }
            if(e) { e.value = arguments[1]; }
        """
        driver.execute_script(js_set_dates, start_date, end_date)
        time.sleep(1)

        # 조회 실행 함수 직접 호출
        driver.execute_script("if(typeof fn_search === 'function') { fn_search(); } else if(typeof doSearch === 'function') { doSearch(); }")
        
        try:
            search_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-search, button#btnSearch, input[value='조회'], a.btn-search")
            search_btn.click()
        except Exception: pass

        time.sleep(8) # 조회 대기

        # 4. 엑셀 다운로드
        excel_btn = None
        try:
            excel_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-excel, a.btn-excel, #btnExcel, input[value='엑셀']")
        except Exception: pass

        records = []
        if excel_btn:
            excel_btn.click()
            time.sleep(10) # 엑셀 내려받기 대기

            download_dir = os.getcwd()
            files = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.endswith('.xlsx') or f.endswith('.xls')]
            if files:
                latest_file = max(files, key=os.path.getctime)
                df = pd.read_excel(latest_file)
                logging.info(f"📥 엑셀 파일 수신 완료: 총 {len(df):,}행 추출")

                for _, row in df.iterrows():
                    center_val = clean_str(row.get("distribution_center") or row.get("물류센터") or row.get("배송센터") or row.get("센터명"))
                    records.append({
                        "store_code": clean_str(row.get("점포코드")),
                        "store_name": clean_str(row.get("점포명")),
                        "brand_name": clean_str(row.get("브랜드")),
                        "distribution_center": center_val,
                        "order_date": clean_str(row.get("주문일자")),
                        "delivery_date": clean_str(row.get("배송일자")),
                        "item_code": clean_str(row.get("품목코드")),
                        "item_name": clean_str(row.get("품목명")),
                        "qty": clean_int(row.get("수량")),
                        "price": clean_int(row.get("단가")),
                        "total_amount": clean_int(row.get("금액") or (clean_int(row.get("수량")) * clean_int(row.get("단가")))),
                        "created_at": datetime.now().isoformat()
                    })
                try: os.remove(latest_file)
                except Exception: pass

        if records:
            delete_existing_range(start_date_hyphen, end_date_hyphen)
            sync_to_supabase(records)
            logging.info("✨ 수집 및 동기화 작업 완료!")
        else:
            logging.warning("⚠️ 수집된 데이터가 0행입니다.")

    except Exception as e:
        logging.error(f"❌ 크롤링 에러 발생: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    run()
