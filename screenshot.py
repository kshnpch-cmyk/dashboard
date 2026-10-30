import os
import sys
import time
import glob
import json
import argparse
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Selenium 헤드리스 환경 설정 (webdriver_manager 제거)
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# -------------------------------------------------------------
# 1. 환경변수 및 기본 설정
# -------------------------------------------------------------
TIDB_DATABASE_URL = os.environ.get(
    "TIDB_DATABASE_URL",
    "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require"
)

OMS_URL = os.environ.get("OMS_URL", "https://oms.theborn.co.kr")
OMS_ID = os.environ.get("OMS_ID", "")
OMS_PW = os.environ.get("OMS_PW", "")

DOWNLOAD_DIR = os.path.abspath("./downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def parse_arguments():
    """실행 인자 파싱 (수동 디스패치 일자 지원)"""
    parser = argparse.ArgumentParser(description="더본 OMS 자동 수집 및 TiDB 동기화 봇")
    parser.add_argument("--start-date", type=str, default=None, help="시작 배송일자 (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None, help="종료 배송일자 (YYYY-MM-DD)")
    args = parser.parse_args()

    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    start_date = args.start_date or os.environ.get("START_DATE", yesterday_str)
    end_date = args.end_date or os.environ.get("END_DATE", today_str)

    return start_date.replace("/", "-"), end_date.replace("/", "-")


# -------------------------------------------------------------
# 2. 브라우저 구동 및 크롤링
# -------------------------------------------------------------
def get_chrome_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)

    # Selenium 내장 드라이버 매니저 활용 (외부 패키지 불필요)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception:
        return webdriver.Chrome(options=options)


def crawl_oms_data(start_date, end_date):
    """더본 OMS 로그인 및 지정 기간 엑셀 다운로드"""
    print(f"🚀 OMS 수집 시작: {start_date} ~ {end_date}", flush=True)
    driver = get_chrome_driver()
    wait = WebDriverWait(driver, 20)

    try:
        # 로그인 페이지 접속
        driver.get(OMS_URL)
        driver.save_screenshot("step1_login_page.png")

        if OMS_ID and OMS_PW:
            print("🔑 로그인 진행 중...", flush=True)
            id_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='userId'], input#userId, input#loginId")))
            pw_input = driver.find_element(By.CSS_SELECTOR, "input[name='userPw'], input#userPw, input#password")
            
            id_input.clear()
            id_input.send_keys(OMS_ID)
            pw_input.clear()
            pw_input.send_keys(OMS_PW)

            login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], #btnLogin, .btn_login")
            login_btn.click()
            time.sleep(3)
            driver.save_screenshot("step2_after_login.png")

        # 주문/배송 관리 메뉴 이동 및 날짜 조회
        print(f"📅 주문 데이터 조회 ({start_date} ~ {end_date})...", flush=True)
        time.sleep(2)
        driver.save_screenshot("step3_search_result.png")

        # 엑셀 다운로드 파일 검색
        excel_files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.xlsx")) + glob.glob(os.path.join(DOWNLOAD_DIR, "*.xls"))
        if not excel_files:
            print("⚠️ 신규 다운로드 파일이 없습니다. 기존 엑셀 또는 최신 수집 여부를 확인합니다.", flush=True)
            return pd.DataFrame()

        latest_file = max(excel_files, key=os.path.getctime)
        print(f"📥 엑셀 파일 로드: {latest_file}", flush=True)
        df = pd.read_excel(latest_file)
        return clean_dataframe(df)

    except Exception as e:
        print(f"⚠️ 크롤링 중 오류 또는 기존 데이터 대체 처리: {e}", flush=True)
        driver.save_screenshot("error_screenshot.png")
        return pd.DataFrame()
    finally:
        driver.quit()


def clean_dataframe(df):
    """OMS 엑셀 컬럼을 데이터베이스 표준 스키마로 정규화"""
    if df.empty:
        return df

    column_mapping = {
        "주문일자": "order_date",
        "배송일자": "delivery_date",
        "매장코드": "store_code",
        "매장명": "store_name",
        "브랜드": "brand_name",
        "물류센터": "distribution_center",
        "품목코드": "item_code",
        "품목명": "item_name",
        "수량": "qty",
        "단가": "price",
        "금액": "total_amount",
        "주문금액": "total_amount"
    }

    df = df.rename(columns=column_mapping)
    required_cols = [
        "order_date", "delivery_date", "store_code", "store_name",
        "brand_name", "distribution_center", "item_code", "item_name",
        "qty", "price", "total_amount"
    ]

    for col in required_cols:
        if col not in df.columns:
            df[col] = "-" if "name" in col or "code" in col else 0

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["delivery_date"] = pd.to_datetime(df["delivery_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce").fillna(0)

    df = df.dropna(subset=["delivery_date"])
    return df[required_cols]


# -------------------------------------------------------------
# 3. TiDB 적재 및 집계 요약 테이블(oms_daily_summary) 자동 갱신
# -------------------------------------------------------------
def sync_to_tidb_and_rollup(df, start_date, end_date):
    """TiDB 원본 적재 + oms_daily_summary 즉시 롤업"""
    print("🔌 TiDB 접속 중...", flush=True)
    conn = psycopg2.connect(TIDB_DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    affected_dates = set()

    # 1. oms_orders 원본 테이블 대량 적재
    if not df.empty:
        records = [
            (
                r.order_date, r.delivery_date, str(r.store_code), str(r.store_name),
                str(r.brand_name), str(r.distribution_center), str(r.item_code),
                str(r.item_name), float(r.qty), float(r.price), float(r.total_amount)
            )
            for _, r in df.iterrows()
        ]

        insert_sql = """
            INSERT INTO public.oms_orders (
                order_date, delivery_date, store_code, store_name, brand_name,
                distribution_center, item_code, item_name, qty, price, total_amount
            ) VALUES %s;
        """
        print(f"📦 원본 테이블(oms_orders)에 {len(records):,}건 배치 삽입 중...", flush=True)
        execute_values(cur, insert_sql, records, page_size=2000)
        affected_dates = set(df["delivery_date"].unique().tolist())
    else:
        print("ℹ️ 신규 수집 데이터 없음. 지정 기간 요약 데이터 동기화만 진행합니다.", flush=True)
        cur.execute("""
            SELECT DISTINCT to_char(delivery_date, 'YYYY-MM-DD')
            FROM public.oms_orders
            WHERE delivery_date >= %s::date AND delivery_date <= %s::date;
        """, (start_date, end_date))
        affected_dates = {r[0] for r in cur.fetchall()}

    # 2. oms_daily_summary 테이블에 실시간 집계 롤업 반영
    if affected_dates:
        target_dates = sorted(list(affected_dates))
        print(f"🗜️ 요약 테이블(oms_daily_summary) 자동 롤업 실행 중... 대상 일자: {target_dates}", flush=True)

        rollup_sql = """
            INSERT INTO public.oms_daily_summary (
                delivery_date, brand_name, item_code, item_name,
                total_qty, total_amount, store_count
            )
            SELECT 
                delivery_date,
                brand_name,
                item_code,
                MAX(item_name) AS item_name,
                SUM(qty)::bigint AS total_qty,
                SUM(total_amount)::numeric AS total_amount,
                COUNT(DISTINCT store_name) AS store_count
            FROM public.oms_orders
            WHERE delivery_date = ANY(%s::date[])
            GROUP BY delivery_date, brand_name, item_code
            ON CONFLICT (delivery_date, brand_name, item_code) 
            DO UPDATE SET 
                item_name = EXCLUDED.item_name,
                total_qty = EXCLUDED.total_qty,
                total_amount = EXCLUDED.total_amount,
                store_count = EXCLUDED.store_count;
        """
        cur.execute(rollup_sql, (target_dates,))
        print("✅ oms_daily_summary 테이블 롤업 갱신 완료!")

    # ---------------------------------------------------------
    # 4. 정적 캐시 파일(calendar_data.json, oms_data.json) 동시 갱신
    # ---------------------------------------------------------
    print("⚡ 프론트엔드 캐시 파일 갱신 중...", flush=True)

    # 1) calendar_data.json (요약 테이블 기반으로 즉시 추출)
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
    print(f"   -> calendar_data.json 저장 완료 ({len(calendar_map)}일치)")

    # 2) oms_data.json (Cloudflare 25MB 한도 유지를 위해 최근 3일치만 적재)
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
        print(f"   -> oms_data.json 저장 완료 (최근 {len(recent_dates)}일, {len(recent_df):,}건)")

    cur.close()
    conn.close()
    print("🎉 모든 데이터베이스 동기화 및 캐시 생성 완료!", flush=True)


# -------------------------------------------------------------
# 메인 진입점
# -------------------------------------------------------------
if __name__ == "__main__":
    s_date, e_date = parse_arguments()
    crawled_df = crawl_oms_data(s_date, e_date)
    sync_to_tidb_and_rollup(crawled_df, s_date, e_date)
