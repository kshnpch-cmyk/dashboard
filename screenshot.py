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
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

# ---------------------------------------------------------------------------
# Environment Variables (요청하신 변수명 매핑)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://zbilhsgfgyfrolveaego.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

OMS_LOGIN_URL = "https://oms.theborn.co.kr/login.do"
OMS_ORDER_LIST_URL = "https://oms.theborn.co.kr/order/orderList.do"

# 🔑 변경된 로그인 환경변수
OMS_ID = os.environ.get("OMS_ID", "")
OMS_PW = os.environ.get("OMS_PW", "")
OMS_COMPANY_CODE = os.environ.get("OMS_COMPANY_CODE", "")

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def clean_int(val):
    if not val or pd.isna(val):
        return 0
    try:
        s = re.sub(r'[^0-9.-]', '', str(val))
        return int(float(s)) if s else 0
    except Exception:
        return 0

def clean_str(val):
    if not val or pd.isna(val):
        return ""
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
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ---------------------------------------------------------------------------
# Supabase Operations
# ---------------------------------------------------------------------------
def upsert_to_supabase(table_name, records, batch_size=500):
    if not records:
        logging.info(f"[{table_name}] 업서트할 데이터가 없습니다.")
        return

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    url = f"{SUPABASE_URL}/rest/v1/{table_name}"
    total = len(records)
    logging.info(f"[{table_name}] 총 {total}건 업서트 시작 (Batch Size: {batch_size})")

    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        try:
            res = requests.post(url, headers=headers, json=batch, timeout=30)
            if res.status_code in [200, 201]:
                logging.info(f"  - [{i + len(batch)}/{total}] 완료")
            else:
                logging.error(f"  - [{i + len(batch)}/{total}] 실패 ({res.status_code}): {res.text}")
        except Exception as e:
            logging.error(f"  - [{i + len(batch)}/{total}] 통신 에러: {e}")

def refresh_materialized_views():
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json"
    }

    views = ["refresh_daily_summary", "refresh_store_summary"]
    for view_rpc in views:
        url = f"{SUPABASE_URL}/rest/v1/rpc/{view_rpc}"
        try:
            res = requests.post(url, headers=headers, json={}, timeout=60)
            if res.status_code in [200, 204]:
                logging.info(f"✅ 구체화 뷰 갱신 성공: {view_rpc}")
            else:
                logging.warning(f"⚠️ 구체화 뷰 갱신 응답 ({res.status_code}): {res.text}")
        except Exception as e:
            logging.error(f"❌ 구체화 뷰 갱신 통신 오류 ({view_rpc}): {e}")

# ---------------------------------------------------------------------------
# Main Capture Execution
# ---------------------------------------------------------------------------
def run_capture(target_start_date, target_end_date):
    driver = None
    try:
        logging.info(f"🚀 OMS 크롤링 시작 [조회 기간: {target_start_date} ~ {target_end_date}]")
        
        if not OMS_ID or not OMS_PW:
            logging.error("❌ OMS_ID 또는 OMS_PW 환경 변수가 설정되지 않았습니다! GitHub Secrets를 확인하세요.")
            sys.exit(1)

        driver = get_chrome_driver()
        wait = WebDriverWait(driver, 20)

        # 1. 로그인 페이지 접속
        driver.get(OMS_LOGIN_URL)
        time.sleep(2)

        # 회사코드 입력란이 있을 경우 처리
        if OMS_COMPANY_CODE:
            for comp_sel in ["companyCode", "compCode", "corpCode"]:
                try:
                    comp_input = driver.find_element(By.ID, comp_sel)
                    if comp_input:
                        comp_input.clear()
                        comp_input.send_keys(OMS_COMPANY_CODE)
                        logging.info("🏢 회사코드 입력 완료")
                        break
                except Exception:
                    pass

        # ID / PW 입력
        id_input = wait.until(EC.presence_of_element_located((By.ID, "userId")))
        pw_input = driver.find_element(By.ID, "userPw")

        id_input.clear()
        id_input.send_keys(OMS_ID)
        pw_input.clear()
        pw_input.send_keys(OMS_PW)
        pw_input.send_keys(Keys.RETURN)

        time.sleep(5)
        
        # 알림창(Alert) 감지 시 예외 처리
        try:
            alert = driver.switch_to.alert
            logging.warning(f"⚠️ 로그인 중 알림창 감지: {alert.text}")
            alert.accept()
            time.sleep(2)
        except Exception:
            pass

        logging.info("✅ OMS 로그인 성공 및 세션 확보")

        # 2. 주문 내역 페이지 이동
        driver.get(OMS_ORDER_LIST_URL)
        time.sleep(5)

        # iframe 존재 여부 체크 및 전환
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes:
            logging.info(f"🔍 iframe {len(iframes)}개 감지 - 첫 번째 프레임으로 전환")
            driver.switch_to.frame(0)

        # 3. 날짜 설정 및 검색
        start_date_elem = None
        for sel in ["startDate", "sDate", "searchStartDate"]:
            try:
                start_date_elem = driver.find_element(By.ID, sel)
                if start_date_elem: break
            except Exception: pass

        if not start_date_elem:
            driver.switch_to.default_content()
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for idx, frame in enumerate(iframes):
                driver.switch_to.default_content()
                driver.switch_to.frame(idx)
                try:
                    start_date_elem = driver.find_element(By.ID, "startDate")
                    if start_date_elem:
                        logging.info(f"🔍 {idx}번째 iframe에서 startDate 포착!")
                        break
                except Exception: pass

        if not start_date_elem:
            raise Exception("날짜 입력란(startDate)을 찾을 수 없습니다. 계정 정보 및 로그인 세션을 확인하세요.")

        end_date_elem = driver.find_element(By.ID, "endDate")

        driver.execute_script("arguments[0].value = arguments[1];", start_date_elem, target_start_date)
        driver.execute_script("arguments[0].value = arguments[1];", end_date_elem, target_end_date)

        search_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-search, button#btnSearch, input[type='button'][value='조회'], a.btn-search")
        search_btn.click()
        time.sleep(6)

        # 4. 엑셀 다운로드 또는 테이블 스크래핑
        excel_btn = None
        try:
            excel_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-excel, a.btn-excel, #btnExcel, input[value='엑셀']")
        except Exception: pass

        records = []
        if excel_btn:
            logging.info("📥 엑셀 다운로드 진행")
            excel_btn.click()
            time.sleep(8)
            
            download_dir = os.getcwd()
            files = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.endswith('.xlsx') or f.endswith('.xls')]
            if files:
                latest_file = max(files, key=os.path.getctime)
                df = pd.read_excel(latest_file)
                
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

        if not records:
            logging.info("📄 화면 테이블 DOM 스크래핑 시도")
            rows = driver.find_elements(By.CSS_SELECTOR, "table.tb-list tbody tr, table tbody tr")
            for r in rows:
                cols = r.find_elements(By.TAG_NAME, "td")
                if len(cols) >= 8:
                    records.append({
                        "store_code": clean_str(cols[0].text),
                        "store_name": clean_str(cols[1].text),
                        "brand_name": clean_str(cols[2].text),
                        "distribution_center": clean_str(cols[3].text),
                        "order_date": clean_str(cols[4].text),
                        "delivery_date": clean_str(cols[5].text),
                        "item_code": clean_str(cols[6].text),
                        "item_name": clean_str(cols[7].text),
                        "qty": clean_int(cols[8].text) if len(cols) > 8 else 0,
                        "price": clean_int(cols[9].text) if len(cols) > 9 else 0,
                        "total_amount": clean_int(cols[10].text) if len(cols) > 10 else 0,
                        "created_at": datetime.now().isoformat()
                    })

        logging.info(f"📦 총 {len(records)}건 데이터 추출 완료")

        if records:
            upsert_to_supabase("oms_orders", records)
            time.sleep(2)
            refresh_materialized_views()
            logging.info("✨ 모든 수집 및 뷰 동기화 완료!")
        else:
            logging.warning("⚠️ 수집된 데이터가 없습니다.")

    except Exception as e:
        logging.error(f"❌ 크롤링 중 오류 발생: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    start_date = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else datetime.now().strftime('%Y/%m/%d')
    end_date = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else start_date

    start_date = start_date.replace('-', '/')
    end_date = end_date.replace('-', '/')

    run_capture(start_date, end_date)
