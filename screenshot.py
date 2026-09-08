import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime
from playwright.sync_api import sync_playwright

# 💡 GitHub Secrets 환경변수 불러오기
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kshnpch-cmyk.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

OMS_ID = os.environ.get("OMS_ID", "사용자_아이디")   # Secrets 미설정 시 기본값
OMS_PW = os.environ.get("OMS_PW", "사용자_비밀번호")

START_DATE = os.environ.get("START_DATE", "")
END_DATE = os.environ.get("END_DATE", "")

# 1. 더본 OMS 어드민 크롤링 및 데이터 수집 함수
def download_oms_data():
    print("🌐 더본 OMS 어드민(admin.theborn.co.kr) 접속 및 자동 수집을 시작합니다...")
    
    with sync_playwright() as p:
        # 브라우저 실행
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            # 1) OMS 어드민 로그인 페이지 접속
            page.goto("https://admin.theborn.co.kr", timeout=60000)
            page.wait_for_load_state("networkidle")

            # 2) 로그인 수행 (아이디/비밀번호 입력)
            page.fill("input[name='id']", OMS_ID)
            page.fill("input[name='password']", OMS_PW)
            page.click("button[type='submit'], .btn_login")  # 로그인 버튼 클릭
            page.wait_for_load_state("networkidle")
            time.sleep(3)

            # 3) 주문 현황/내역 메뉴 이동 (OMS 메뉴 URL로 직접 이동)
            print("📦 주문 내역 메뉴로 이동 중...")
            page.goto("https://admin.theborn.co.kr/order/list", timeout=60000) # 실제 주문 메뉴 URL
            page.wait_for_load_state("networkidle")

            # 4) 날짜 조건 입력 (파라미터 전달받은 경우)
            if START_DATE and END_DATE:
                print(f"📅 조회 기간 설정: {START_DATE} ~ {END_DATE}")
                page.fill("input[name='start_date']", START_DATE)
                page.fill("input[name='end_date']", END_DATE)
                page.click(".btn_search, button:has-text('조회')")
                time.sleep(3)

            # 5) 엑셀 다운로드 수행
            print("📥 엑셀 데이터 다운로드 시도 중...")
            with page.expect_download() as download_info:
                page.click("button:has-text('엑셀'), .btn_excel") # 엑셀 다운로드 버튼
            
            download = download_info.value
            download_path = os.path.join(os.getcwd(), "temp_oms.xlsx")
            download.save_as(download_path)
            print(f"✅ 엑셀 파일 저장 완료: {download_path}")

            browser.close()

            # 6) 다운로드한 엑셀 파일을 Pandas Dataframe으로 읽기
            df = pd.read_excel(download_path)
            return df

        except Exception as e:
            print(f"❌ OMS 수집 중 오류 발생: {e}")
            browser.close()
            return None


# 2. Supabase 클라우드 DB 데이터 업로드 함수
def sync_to_supabase(combined_df):
    if combined_df is None or combined_df.empty:
        print("⚠️ Supabase에 업로드할 데이터가 없습니다.")
        return

    print("🚀 Supabase 클라우드 DB로 동기화를 시작합니다...")

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
    
    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        res = requests.post(f"{SUPABASE_URL}/rest/v1/oms_orders", headers=headers, json=batch)
        
        if res.status_code in [200, 201]:
            print(f"✅ DB 동기화 완료: {min(i + batch_size, total_records)} / {total_records} 건")
        else:
            print(f"❌ DB 동기화 오류 ({res.status_code}): {res.text}")

# 3. 메인 실행부
if __name__ == "__main__":
    df_oms = download_oms_data()
    if df_oms is not None and not df_oms.empty:
        sync_to_supabase(df_oms)
    else:
        print("⚠️ 데이터 수집에 실패하여 DB 동기화를 중단합니다.")
