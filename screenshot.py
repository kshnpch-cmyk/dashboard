import os
import sys
import time
import re
import json
import logging
from datetime import datetime, timedelta
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://zbilhsgfgyfrolveaego.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

OMS_LOGIN_URL = "https://oms.theborn.co.kr/login.do"
OMS_ORDER_LIST_URL = "https://oms.theborn.co.kr/order/orderList.do"

USER_ID = os.environ.get("OMS_USER_ID", "")
USER_PW = os.environ.get("OMS_USER_PW", "")

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

    # 📥 자동 엑셀 다운로드 디렉토리 설정
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

def run_capture(target_start_date, target_end_date):
    driver = None
    try:
        logging.info(f"🚀 OMS 크롤링 시작 [조회 기간: {target_start_date} ~ {target_end_date}]")
        driver = get_chrome_driver()
        wait = WebDriverWait(driver, 25)

        # 1. 로그인
        driver.get(OMS_LOGIN_URL)
        time.sleep(2)

        id_input = wait.until(EC.presence_of_element_located((By.ID, "userId")))
        pw_input = driver.find_element(By.ID, "userPw")

        id_input.clear()
        id_input.send_keys(USER_ID)
        pw_input.clear()
        pw_input.send_keys(USER_PW)
        pw_input.send_keys(Keys.RETURN)

        time.sleep(5)
        logging.info("✅ OMS 로그인 성공 및 세션 확보")

        # 2. 주문 내역 페이지 이동
        driver.get(OMS_ORDER_LIST_URL)
        time.sleep(5)

        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes:
            logging.info(f"🔍 iframe {len(iframes)}개 감지 - 첫 번째 프레임으로 전환")
            driver.switch_to.frame(0)

        # 3. 날짜 입력 및 조회 클릭
        start_date_elem = wait.until(EC.presence_of_element_located((By.ID, "startDate")))
        end_date_elem = driver.find_element(By.ID, "endDate")

        driver.execute_script("arguments[0].value = arguments[1];", start_date_elem, target_start_date)
        driver.execute_script("arguments[0].value = arguments[1];", end_date_elem, target_end_date)

        # 검색 버튼 실행 (이벤트 트리거 포함)
        driver.execute_script("if(typeof fn_search === 'function') { fn_search(); } else if(typeof doSearch === 'function') { doSearch(); }")
        try:
            search_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-search, button#btnSearch, input[value='조회'], a.btn-search")
            search_btn.click()
        except Exception:
            pass

        logging.info("🔍 조회 실행 완료 - 데이터 로딩 대기 중...")
        time.sleep(10) # 서버 응답 대기

        # 4. 엑셀 다운로드 시도 (JS 직접 호출 + 버튼 탐색)
        records = []
        logging.info("📥 엑셀 다운로드 실행 시도")
        try:
            # 더본 OMS 자바스크립트 엑셀 함수 직접 호출
            driver.execute_script("if(typeof fn_excel === 'function') { fn_excel(); } else if(typeof fn_excelDown === 'function') { fn_excelDown(); } else if(typeof doExcel === 'function') { doExcel(); }")
        except Exception:
            pass

        # 엑셀 다운로드 버튼 클릭
        for ex_sel in ["button.btn-excel", "a.btn-excel", "#btnExcel", "input[value='엑셀']", ".btn_excel"]:
            try:
                ex_btn = driver.find_element(By.CSS_SELECTOR, ex_sel)
                if ex_btn:
                    ex_btn.click()
                    break
            except: pass

        time.sleep(10) # 파일 다운로드 완료 대기

        # 5. 다운로드된 엑셀 파일 읽기
        download_dir = os.getcwd()
        files = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.endswith('.xlsx') or f.endswith('.xls')]
        
        if files:
            latest_file = max(files, key=os.path.getctime)
            logging.info(f"📂 엑셀 파일 감지 및 읽기: {latest_file}")
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
            try:
                os.remove(latest_file)
            except: pass

        # 6. 엑셀 파일이 없을 경우 화면 DOM 직접 스크래핑
        if not records:
            logging.info("📄 엑셀 파일 미감지 - 화면 테이블 DOM 직접 추출 시도")
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            for r in rows:
                cols = r.find_elements(By.TAG_NAME, "td")
                if len(cols) >= 6:
                    records.append({
                        "store_code": clean_str(cols[0].text),
                        "store_name": clean_str(cols[1].text),
                        "brand_name": clean_str(cols[2].text) if len(cols) > 2 else "",
                        "distribution_center": clean_str(cols[3].text) if len(cols) > 3 else "",
                        "order_date": clean_str(cols[4].text) if len(cols) > 4 else "",
                        "delivery_date": clean_str(cols[5].text) if len(cols) > 5 else "",
                        "item_code": clean_str(cols[6].text) if len(cols) > 6 else "",
                        "item_name": clean_str(cols[7].text) if len(cols) > 7 else "",
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
            logging.info("✨ 모든 프로세스가 성공적으로 마무리되었습니다.")
        else:
            logging.warning("⚠️ 수집된 데이터가 존재하지 않습니다. OMS 조회 조건 날짜에 데이터가 있는지 확인해 주세요.")

    except Exception as e:
        logging.error(f"❌ 크롤링 오류: {e}", exc_info=True)
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
