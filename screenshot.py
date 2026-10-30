import os
import sys
import time
import re
import glob
import json
import logging
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

# ---------------------------------------------------------------------------
# Environment Variables & Configuration
# ---------------------------------------------------------------------------
TIDB_DATABASE_URL = os.environ.get(
    "TIDB_DATABASE_URL",
    "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require"
)

OMS_LOGIN_URL = "https://oms.theborn.co.kr/login.do"
OMS_ORDER_LIST_URL = "https://oms.theborn.co.kr/order/orderList.do"

# OMS 로그인 계정 환경변수 호환 처리
USER_ID = os.environ.get("OMS_USER_ID") or os.environ.get("OMS_ID") or ""
USER_PW = os.environ.get("OMS_USER_PW") or os.environ.get("OMS_PW") or ""

DOWNLOAD_DIR = os.path.abspath("./downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
        return "-"
    s = str(val).strip()
    return s if s else "-"

def parse_date_str(val):
    if not val or pd.isna(val):
        return None
    s = str(val).split('T')[0].replace('/', '-').strip()
    return s if len(s) >= 8 else None

def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    # webdriver_manager 설치 여부와 무관하게 안전 구동
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception:
        driver = webdriver.Chrome(options=chrome_options)

    # 헤드리스 크롬 백그라운드 다운로드 강제 허용
    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {"behavior": "allow", "downloadPath": DOWNLOAD_DIR}
    )
    return driver

# ---------------------------------------------------------------------------
# TiDB Database Sync & Rollup
# ---------------------------------------------------------------------------
def sync_to_tidb_and_rollup(records, s_date_iso, e_date_iso):
    """선-삭제 후 대량 적재 + 요약 테이블 롤업 + 캐시 파일 생성"""
    logging.info(f"🔌 TiDB 접속 시작... (대상 기간: {s_date_iso} ~ {e_date_iso})")
    conn = psycopg2.connect(TIDB_DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    # 1. 대상 배송일자 범위 선-삭제 (중복 및 취소건 완전 초기화)
    logging.info(f"🧹 [선-삭제 실행] 배송일자 {s_date_iso} ~ {e_date_iso} 범위 기존 데이터 정리")
    cur.execute("""
        DELETE FROM public.oms_orders 
        WHERE delivery_date >= %s::date AND delivery_date <= %s::date;
    """, (s_date_iso, e_date_iso))

    # 2. oms_orders 테이블에 대량 적재 (2,000건씩 배치 처리)
    if records:
        insert_data = [
            (
                r["order_date"], r["delivery_date"], r["store_code"], r["store_name"],
                r["brand_name"], r["distribution_center"], r["item_code"], r["item_name"],
                r["qty"], r["price"], r["total_amount"]
            )
            for r in records if r["delivery_date"]
        ]

        insert_sql = """
            INSERT INTO public.oms_orders (
                order_date, delivery_date, store_code, store_name, brand_name,
                distribution_center, item_code, item_name, qty, price, total_amount
            ) VALUES %s;
        """
        logging.info(f"📦 oms_orders 테이블에 {len(insert_data):,}건 배치 삽입 중...")
        execute_values(cur, insert_sql, insert_data, page_size=2000)
        logging.info("✅ oms_orders 신규 데이터 적재 완료")

    # 3. oms_daily_summary 테이블 선-삭제 후 롤업 집계
    logging.info("🗜️ 요약 테이블(oms_daily_summary) 롤업 갱신 진행...")
    cur.execute("""
        DELETE FROM public.oms_daily_summary 
        WHERE delivery_date >= %s::date AND delivery_date <= %s::date;
    """, (s_date_iso, e_date_iso))

    cur.execute("""
        INSERT INTO public.oms_daily_summary (
            delivery_date, brand_name, item_code, item_name,
            total_qty, total_amount, store_count
        )
        SELECT 
            delivery_date, brand_name, item_code, MAX(item_name),
            SUM(qty)::bigint, SUM(total_amount)::numeric, COUNT(DISTINCT store_name)
        FROM public.oms_orders
        WHERE delivery_date >= %s::date AND delivery_date <= %s::date
        GROUP BY delivery_date, brand_name, item_code;
    """, (s_date_iso, e_date_iso))
    logging.info("✅ 요약 테이블(oms_daily_summary) 롤업 완료")

    # 4. 프론트엔드 캐시 파일 갱신
    logging.info("⚡ 프론트엔드 캐시 파일 갱신 중...")

    # 1) calendar_data.json
    cur.execute("""
        SELECT to_char(delivery_date, 'YYYY-MM-DD'), SUM(total_qty)::bigint
        FROM public.oms_daily_summary
        GROUP BY delivery_date
        ORDER BY delivery_date ASC;
    """)
    cal_rows = cur.fetchall()
    calendar_map = {r[0]: int(r[1]) for r in cal_rows if r[0] and r[1] is not None}
    with open("calendar_data.json", "w", encoding="utf-8") as f:
        json.dump(calendar_map, f, ensure_ascii=False)
    logging.info(f"   -> calendar_data.json 저장 완료 ({len(calendar_map)}일치)")

    # 2) oms_data.json (최근 3일치만 안전하게 15MB 이내로 저장)
    all_dates = sorted([d for d in calendar_map.keys() if d and calendar_map[d] > 0])
    recent_dates = all_dates[-3:] if len(all_dates) >= 3 else all_dates

    if recent_dates:
        cur.execute("""
            SELECT 
                to_char(order_date, 'YYYY-MM-DD') AS order_date,
                to_char(delivery_date, 'YYYY-MM-DD') AS delivery_date,
                store_code, store_name, brand_name, distribution_center,
                item_code, item_name, qty, price, total_amount
            FROM public.oms_orders
            WHERE delivery_date = ANY(%s::date[])
            ORDER BY delivery_date ASC, brand_name ASC;
        """, (recent_dates,))
        cols = [desc[0] for desc in cur.description]
        recent_df = pd.DataFrame(cur.fetchall(), columns=cols)
        recent_df.to_json("oms_data.json", orient="records", force_ascii=False)
        logging.info(f"   -> oms_data.json 저장 완료 (최근 {len(recent_dates)}일, {len(recent_df):,}건)")

    cur.close()
    conn.close()
    logging.info("🎉 모든 데이터베이스 동기화 및 캐시 생성 완료!")

# ---------------------------------------------------------------------------
# Main Scraping Logic (기존 OMS 브라우저 조작 원본 복원)
# ---------------------------------------------------------------------------
def run_capture(target_start_date, target_end_date):
    driver = None
    try:
        # 기존 다운로드 폴더 내 잔여 파일 삭제
        for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
            try: os.remove(f)
            except Exception: pass

        logging.info(f"🚀 OMS 크롤링 시작 [조회 기간: {target_start_date} ~ {target_end_date}]")
        driver = get_chrome_driver()
        wait = WebDriverWait(driver, 20)

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

        time.sleep(3)
        logging.info("✅ OMS 로그인 완료")

        # 2. 주문 내역 페이지 이동
        driver.get(OMS_ORDER_LIST_URL)
        time.sleep(3)

        # 3. 날짜 설정 및 검색 (배송일자/주문일자 조건)
        start_date_elem = wait.until(EC.presence_of_element_located((By.ID, "startDate")))
        end_date_elem = driver.find_element(By.ID, "endDate")

        driver.execute_script("arguments[0].value = arguments[1];", start_date_elem, target_start_date)
        driver.execute_script("arguments[0].value = arguments[1];", end_date_elem, target_end_date)

        search_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-search, button#btnSearch, input[type='button'][value='조회']")
        search_btn.click()
        time.sleep(5)

        # 4. 엑셀 다운로드 시도
        excel_btn = None
        try:
            excel_btn = driver.find_element(By.CSS_SELECTOR, "button.btn-excel, a.btn-excel, #btnExcel")
        except Exception:
            pass

        records = []
        if excel_btn:
            logging.info("📥 엑셀 다운로드 버튼 감지 - 파일 수집 시도")
            excel_btn.click()

            # 다운로드 완료 대기 (최대 30초)
            latest_file = None
            for _ in range(30):
                files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if (f.endswith('.xlsx') or f.endswith('.xls')) and not f.endswith('.crdownload')]
                if files:
                    latest_file = max(files, key=os.path.getctime)
                    break
                time.sleep(1)

            if latest_file:
                logging.info(f"📥 다운로드 파일 파싱: {latest_file}")
                df = pd.read_excel(latest_file)
                for _, row in df.iterrows():
                    o_date = parse_date_str(row.get("주문일자"))
                    d_date = parse_date_str(row.get("배송일자"))
                    qty = clean_int(row.get("수량"))
                    price = clean_int(row.get("단가"))
                    amt = clean_int(row.get("금액") or row.get("주문금액") or (qty * price))

                    records.append({
                        "store_code": clean_str(row.get("점포코드") or row.get("매장코드")),
                        "store_name": clean_str(row.get("점포명") or row.get("매장명")),
                        "brand_name": clean_str(row.get("브랜드")),
                        "distribution_center": clean_str(row.get("센터명") or row.get("물류센터")),
                        "order_date": o_date,
                        "delivery_date": d_date,
                        "item_code": clean_str(row.get("품목코드")),
                        "item_name": clean_str(row.get("품목명")),
                        "qty": qty,
                        "price": price,
                        "total_amount": amt
                    })
                try: os.remove(latest_file)
                except Exception: pass

        # 엑셀 다운로드가 안 되었을 경우 테이블 DOM 수동 스크래핑
        if not records:
            logging.info("📄 화면 테이블 DOM 수동 스크래핑 수행")
            rows = driver.find_elements(By.CSS_SELECTOR, "table.tb-list tbody tr")
            for r in rows:
                cols = r.find_elements(By.TAG_NAME, "td")
                if len(cols) >= 8:
                    qty = clean_int(cols[8].text) if len(cols) > 8 else 0
                    price = clean_int(cols[9].text) if len(cols) > 9 else 0
                    amt = clean_int(cols[10].text) if len(cols) > 10 else (qty * price)

                    records.append({
                        "store_code": clean_str(cols[0].text),
                        "store_name": clean_str(cols[1].text),
                        "brand_name": clean_str(cols[2].text),
                        "distribution_center": clean_str(cols[3].text),
                        "order_date": parse_date_str(cols[4].text),
                        "delivery_date": parse_date_str(cols[5].text),
                        "item_code": clean_str(cols[6].text),
                        "item_name": clean_str(cols[7].text),
                        "qty": qty,
                        "price": price,
                        "total_amount": amt
                    })

        logging.info(f"📦 총 {len(records):,}건 데이터 추출 성공")

        # 5. TiDB 적재 및 요약 테이블 롤업 실행
        s_date_iso = target_start_date.replace('/', '-')
        e_date_iso = target_end_date.replace('/', '-')

        if records:
            sync_to_tidb_and_rollup(records, s_date_iso, e_date_iso)
        else:
            logging.warning("⚠️ 수집된 데이터가 없습니다. (해당 기간 주문 내역 없음)")
            # 데이터가 없는 날이라도 기존 취소/삭제 건 정리를 위해 빈 상태 롤업 수행
            sync_to_tidb_and_rollup([], s_date_iso, e_date_iso)

    except Exception as e:
        logging.error(f"❌ 크롤링 중 오류 발생: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if driver:
            driver.quit()

# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 인자 지원 (CLI argv 또는 환경변수)
    start_arg = None
    end_arg = None

    if len(sys.argv) > 1:
        # --start-date 옵션 처리
        for i, arg in enumerate(sys.argv):
            if arg == "--start-date" and i + 1 < len(sys.argv):
                start_arg = sys.argv[i + 1]
            elif arg == "--end-date" and i + 1 < len(sys.argv):
                end_arg = sys.argv[i + 1]
        
        if not start_arg and not sys.argv[1].startswith("--"):
            start_arg = sys.argv[1]
        if not end_arg and len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
            end_arg = sys.argv[2]

    today_str = datetime.now().strftime('%Y/%m/%d')
    start_date = start_arg or os.environ.get("START_DATE", today_str)
    end_date = end_arg or os.environ.get("END_DATE", start_date)

    # OMS 입력창 규격 (YYYY/MM/DD)으로 통일
    start_date = start_date.replace('-', '/')
    end_date = end_date.replace('-', '/')

    run_capture(start_date, end_date)
