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

# TiDB Cloud 환경변수
TIDB_DATABASE_URL = os.environ.get(
    "TIDB_DATABASE_URL",
    "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require"
).strip()

OMS_COMPANY_CODE = os.environ.get("OMS_COMPANY_CODE", "1000").strip()
OMS_ID = os.environ.get("OMS_ID", "1220503").strip()
OMS_PW = os.environ.get("OMS_PW", "theborn8@").strip()

# 1. KST 날짜 계산
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

start_date_obj = now_kst + timedelta(days=1)
end_date_obj = now_kst + timedelta(days=3) if now_kst.hour >= 11 else now_kst + timedelta(days=2)

target_start_date = os.environ.get('START_DATE') or start_date_obj.strftime("%Y/%m/%d")
target_end_date = os.environ.get('END_DATE') or end_date_obj.strftime("%Y/%m/%d")

# 2. 크롬 드라이버 설정
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


def update_local_json(new_df, start_date_str, end_date_str, json_path="oms_data.json"):
    """
    ⚡ 대시보드 초고속 조회를 위한 정적 JSON 병합 생성
    - 기존 oms_data.json이 있으면 읽어와서 이번 수집 대상 기간만 갱신 후 합침
    - 브라우저 로딩 최적화를 위해 경량 포맷으로 변환
    """
    print(f"📦 [정적 JSON 생성] {json_path} 최신화 작업 시작...", flush=True)

    s_date = start_date_str.replace('/', '-')
    e_date = end_date_str.replace('/', '-')

    def parse_num(val):
        try:
            s = str(val).replace(',', '').strip()
            return float(s) if s else 0.0
        except:
            return 0.0

    def parse_date(val):
        s = str(val).split('T')[0].replace('/', '-').strip()
        return s if len(s) >= 8 else None

    # 신규 수집 데이터 규격화
    cleaned_rows = []
    for _, row in new_df.iterrows():
        deliv = parse_date(row.get("배송일자", ""))
        if not deliv:
            continue
        cleaned_rows.append({
            "order_date": parse_date(row.get("주문일자", "")),
            "delivery_date": deliv,
            "store_code": str(row.get("거래처코드", row.get("점포코드", ""))).strip(),
            "store_name": str(row.get("거래처명", row.get("점포명", ""))).strip(),
            "brand_name": str(row.get("브랜드명", row.get("브랜드", ""))).strip(),
            "distribution_center": str(row.get("distribution_center", row.get("물류센터", row.get("배송센터", "")))).strip(),
            "item_code": str(row.get("품목코드", "")).strip(),
            "item_name": str(row.get("품목명", "")).strip(),
            "qty": int(parse_num(row.get("수량", row.get("주문수량", 0)))),
            "price": parse_num(row.get("단가", row.get("공급단가", 0))),
            "total_amount": parse_num(row.get("금액", row.get("공급가액", 0)))
        })

    new_records_df = pd.DataFrame(cleaned_rows)

    # 기존 JSON 파일 로드 및 중복 기간 교체
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            existing_df = pd.DataFrame(existing_data)
            
            # 이번에 수집한 기간의 기존 데이터는 제외
            if not existing_df.empty and 'delivery_date' in existing_df.columns:
                existing_df = existing_df[~((existing_df['delivery_date'] >= s_date) & (existing_df['delivery_date'] <= e_date))]
                final_df = pd.concat([existing_df, new_records_df], ignore_index=True)
            else:
                final_df = new_records_df
        except Exception as e:
            print(f"⚠️ 기존 JSON 로드 실패, 신규 데이터로 대체: {e}", flush=True)
            final_df = new_records_df
    else:
        final_df = new_records_df

    # 배송일자 기준 정렬 후 JSON으로 저장
    if not final_df.empty and 'delivery_date' in final_df.columns:
        final_df = final_df.sort_values(by=['delivery_date', 'brand_name', 'store_name'])

    final_df.to_json(json_path, orient='records', force_ascii=False, indent=None)
    print(f"✨ [정적 JSON 완료] 총 {len(final_df):,}건이 {json_path}에 저장되었습니다.", flush=True)


def delete_existing_period_data(conn, start_date_str, end_date_str):
    s_date = start_date_str.replace('/', '-')
    e_date = end_date_str.replace('/', '-')
    print(f"🧹 [선-삭제] TiDB {s_date} ~ {e_date} 범위 삭제...", flush=True)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.oms_orders WHERE delivery_date >= %s::date AND delivery_date <= %s::date;", (s_date, e_date))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e


def ensure_indexes_safely(conn):
    index_tasks = [
        ("idx_oms_orders_delivery_date", "CREATE INDEX IF NOT EXISTS idx_oms_orders_delivery_date ON public.oms_orders (delivery_date)"),
        ("idx_oms_orders_brand_delivery", "CREATE INDEX IF NOT EXISTS idx_oms_orders_brand_delivery ON public.oms_orders (brand_name, delivery_date)"),
        ("idx_oms_orders_store_delivery", "CREATE INDEX IF NOT EXISTS idx_oms_orders_store_delivery ON public.oms_orders (store_name, delivery_date)")
    ]
    for idx_name, sql in index_tasks:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()


def sync_to_tidb(combined_df):
    if combined_df is None or combined_df.empty:
        return
    print(f"🚀 TiDB 백업 동기화 시작...", flush=True)
    conn = psycopg2.connect(TIDB_DATABASE_URL)
    try:
        delete_existing_period_data(conn, target_start_date, target_end_date)
        ensure_indexes_safely(conn)

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

            center_val = str(row.get("distribution_center", row.get("물류센터", row.get("배송센터", "")))).strip()
            records.append((
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
            ))

        batch_size = 1000
        insert_sql = """
            INSERT INTO public.oms_orders (
                order_date, delivery_date, store_code, store_name, brand_name,
                distribution_center, item_code, item_name, qty, price, total_amount
            ) VALUES %s
        """
        with conn.cursor() as cur:
            for i in range(0, len(records), batch_size):
                execute_values(cur, insert_sql, records[i:i + batch_size])
                conn.commit()
        print("🎉 TiDB 동기화 완료", flush=True)
    except Exception as e:
        conn.rollback()
        print(f"❌ TiDB 동기화 예외 (수집은 계속 진행): {e}", flush=True)
    finally:
        conn.close()


try:
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] OMS 자동 수집 시작 ({target_start_date} ~ {target_end_date})", flush=True)

    driver.get('https://admin.theborn.co.kr/oms-manager/login')
    time.sleep(2)
    driver.find_element(By.ID, 'companyCd').send_keys(OMS_COMPANY_CODE)
    driver.find_element(By.ID, 'userId').send_keys(OMS_ID)
    driver.find_element(By.ID, 'userPw').send_keys(OMS_PW + Keys.ENTER)
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

    list_of_files = glob.glob(os.path.join(download_dir, '*.xlsx')) or glob.glob(os.path.join(download_dir, '*.xls'))
    if not list_of_files:
        raise Exception("다운로드 파일 수신 실패")

    latest_file = max(list_of_files, key=os.path.getctime)
    new_df = pd.read_excel(latest_file, engine='openpyxl')
    print(f"📥 수신된 원본 데이터: {len(new_df):,}행", flush=True)

    # 1. 정적 JSON 최신화 (대시보드 0.01초 조회용)
    update_local_json(new_df, target_start_date, target_end_date, "oms_data.json")

    # 2. TiDB Cloud DB 동기화 (원격 보관용)
    sync_to_tidb(new_df)

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
