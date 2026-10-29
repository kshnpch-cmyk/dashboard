import os
import re
import glob
import time
import json
import warnings
import pandas as pd
from datetime import datetime, timedelta, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# TiDB (PostgreSQL 호환) 드라이버
import psycopg2
from psycopg2.extras import execute_values

# openpyxl CellStyle count 속성 오류 방지 패치
import openpyxl.styles.cell_style
_original_cell_style_init = openpyxl.styles.cell_style.CellStyle.__init__

def _patched_cell_style_init(self, *args, **kwargs):
    kwargs.pop('count', None)
    _original_cell_style_init(self, *args, **kwargs)

openpyxl.styles.cell_style.CellStyle.__init__ = _patched_cell_style_init

warnings.filterwarnings('ignore')

# 💡 TiDB Cloud 접속 환경변수 수신
TIDB_DATABASE_URL = os.environ.get(
    "TIDB_DATABASE_URL",
    "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require"
).strip()

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


def delete_existing_period_data(conn, start_date_str, end_date_str):
    """💡 동기화 대상 기간(start_date ~ end_date)의 기존 DB 데이터를 선-삭제하여 중복 및 취소건을 초기화합니다."""
    s_date = start_date_str.replace('/', '-')
    e_date = end_date_str.replace('/', '-')

    print(f"🧹 [선-삭제 실행] DB 기존 배송일자 범위 데이터 삭제 진행 ({s_date} ~ {e_date})...", flush=True)

    try:
        with conn.cursor() as cur:
            delete_sql = """
                DELETE FROM public.oms_orders 
                WHERE delivery_date >= %s::date AND delivery_date <= %s::date;
            """
            cur.execute(delete_sql, (s_date, e_date))
        conn.commit()
        print(f"🗑️ 기존 데이터 삭제 성공 ({s_date} ~ {e_date})", flush=True)
    except Exception as e:
        conn.rollback()
        print(f"❌ 데이터 삭제 중 오류 발생: {e}", flush=True)
        raise e


def ensure_indexes_safely(conn):
    """
    ⚡ TiDB 분산 인덱스 제약에 맞춰 1개씩 순차 실행하며,
    이미 존재하거나 백그라운드 빌드 중인 경우에도 동기화 작업이 중단되지 않도록 보호합니다.
    """
    index_tasks = [
        ("idx_oms_orders_delivery_date", "CREATE INDEX IF NOT EXISTS idx_oms_orders_delivery_date ON public.oms_orders (delivery_date)"),
        ("idx_oms_orders_brand_delivery", "CREATE INDEX IF NOT EXISTS idx_oms_orders_brand_delivery ON public.oms_orders (brand_name, delivery_date)"),
        ("idx_oms_orders_store_delivery", "CREATE INDEX IF NOT EXISTS idx_oms_orders_store_delivery ON public.oms_orders (store_name, delivery_date)")
    ]

    print("⚡ [인덱스 상태 점검] 속도 최적화 인덱스 점검 시작...", flush=True)
    for idx_name, sql in index_tasks:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            print(f"  ✅ 인덱스 확인/생성 완료: {idx_name}", flush=True)
        except Exception as e:
            conn.rollback()
            # 이미 빌드 중이거나 존재하는 경우 로그만 남기고 다음 작업으로 안전하게 통과
            print(f"  ℹ️ 인덱스 처리 건너뜀 ({idx_name}): {e}", flush=True)


def sync_to_tidb(combined_df):
    """수집된 Dataframe을 기존 데이터 삭제 후 TiDB Cloud oms_orders 테이블로 업로드"""
    if combined_df is None or combined_df.empty:
        print("⚠️ TiDB에 업로드할 데이터가 없습니다.", flush=True)
        return

    print(f"🚀 TiDB 클라우드 DB 동기화를 시작합니다...", flush=True)

    conn = psycopg2.connect(TIDB_DATABASE_URL)
    try:
        # 1. 기존 기간 데이터 삭제
        delete_existing_period_data(conn, target_start_date, target_end_date)

        # 2. 인덱스 자동 보장 (TiDB DDL 제약 방어 로직)
        ensure_indexes_safely(conn)

        # 3. 신규 데이터 대량 INSERT
        df_clean = combined_df.fillna('').astype(str)
        records = []

        for _, row in df_clean.iterrows():
            def parse_num(val):
                try:
                    s = str(val).replace(',', '').strip()
                    return float(s) if s else 0.0
                except:
                    return 0.0

            def parse_date(val):
                s = str(val).split('T')[0].replace('/', '-').strip()
                return s if len(s) >= 8 else None

            center_val = str(row.get("distribution_center", row.get("물류센터", row.get("배송센터", row.get("센터명", ""))))).strip()

            record = (
                parse_date(row.get("주문일자", "")),
                parse_date(row.get("배송일자", "")),
                str(row.get("거래처코드", row.get("점포코드", ""))),
                str(row.get("거래처명", row.get("점포명", ""))),
                str(row.get("브랜드명", row.get("브랜드", ""))),
                center_val,
                str(row.get("품목코드", "")),
                str(row.get("품목명", "")),
                int(parse_num(row.get("수량", row.get("주문수량", 0)))),
                parse_num(row.get("단가", row.get("공급단가", 0))),
                parse_num(row.get("금액", row.get("공급가액", 0)))
            )
            records.append(record)

        batch_size = 1000
        total_records = len(records)
        insert_sql = """
            INSERT INTO public.oms_orders (
                order_date, delivery_date, store_code, store_name, brand_name,
                distribution_center, item_code, item_name, qty, price, total_amount
            ) VALUES %s
        """

        with conn.cursor() as cur:
            for i in range(0, total_records, batch_size):
                batch = records[i:i + batch_size]
                execute_values(cur, insert_sql, batch)
                conn.commit()
                print(f"✅ DB 동기화(새로 재업로드) 완료: {min(i + batch_size, total_records)} / {total_records} 건", flush=True)

        print("🎉 모든 주문 데이터가 TiDB Cloud에 성공적으로 동기화되었습니다.", flush=True)

    except Exception as e:
        conn.rollback()
        print(f"❌ DB 동기화 오류: {e}", flush=True)
        raise e
    finally:
        conn.close()


try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] OMS 자동 수집 시작", flush=True)
    print(f"조회 지정 기간: {target_start_date} ~ {target_end_date}", flush=True)

    # 3. 어드민 로그인
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

    # 10. TiDB Cloud DB로 동기화 전송
    sync_to_tidb(new_df)

    # 임시 다운로드 파일 정리
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
