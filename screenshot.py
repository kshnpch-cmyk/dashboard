import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime
from playwright.sync_api import sync_playwright

# 💡 GitHub Secrets 환경변수 수신 (보안 정보)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kshnpch-cmyk.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

OMS_COMPANY_CODE = os.environ.get("OMS_COMPANY_CODE", "")
OMS_ID = os.environ.get("OMS_ID", "")
OMS_PW = os.environ.get("OMS_PW", "")

START_DATE = os.environ.get("START_DATE", "")
END_DATE = os.environ.get("END_DATE", "")


def download_oms_data():
    """더본 OMS 어드민(admin.theborn.co.kr) 접속, 로그인 및 엑셀 다운로드"""
    print("🌐 더본 OMS 어드민 접속 및 데이터 수집을 시작합니다...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            # 1) OMS 어드민 로그인 페이지 접속
            page.goto("https://admin.theborn.co.kr", timeout=60000)
            page.wait_for_load_state("networkidle")

            # 2) 회사코드, 아이디, 비밀번호 입력 및 로그인
            print("🔑 로그인 정보 입력 중...")
            if page.locator("input[name='company_code']").is_visible():
                page.fill("input[name='company_code']", OMS_COMPANY_CODE)

            page.fill("input[name='id']", OMS_ID)
            page.fill("input[name='password']", OMS_PW)

            # 로그인 버튼 클릭 (일반적인 버튼 선택자 적용)
            page.click("button[type='submit'], .btn_login, #btnLogin")
            page.wait_for_load_state("networkidle")
            time.sleep(3)

            # 3) 주문 현황 메뉴로 이동
            print("📦 주문 내역 메뉴로 이동 중...")
            page.goto("https://admin.theborn.co.kr/order/list", timeout=60000)
            page.wait_for_load_state("networkidle")

            # 4) 날짜 파라미터가 들어온 경우 기간 세팅
            if START_DATE and END_DATE:
                print(f"📅 조회 기간 설정: {START_DATE} ~ {END_DATE}")
                if page.locator("input[name='start_date']").is_visible():
                    page.fill("input[name='start_date']", START_DATE)
                    page.fill("input[name='end_date']", END_DATE)
                    page.click(".btn_search, button:has-text('조회')")
                    time.sleep(3)

            # 5) 엑셀 다운로드 수행
            print("📥 엑셀 데이터 다운로드 시도 중...")
            with page.expect_download() as download_info:
                page.click("button:has-text('엑셀'), .btn_excel, #btnExcel")

            download = download_info.value
            download_path = os.path.join(os.getcwd(), "temp_oms.xlsx")
            download.save_as(download_path)
            print(f"✅ 엑셀 파일 저장 완료: {download_path}")

            browser.close()

            # 6) 다운로드한 엑셀 파일을 Pandas Dataframe으로 로드
            df = pd.read_excel(download_path)
            return df

        except Exception as e:
            print(f"❌ OMS 수집 중 오류 발생: {e}")
            browser.close()
            return None


def sync_to_supabase(combined_df):
    """수집된 OMS 데이터를 Supabase 클라우드 DB(oms_orders)로 Bulk Insert"""
    if combined_df is None or combined_df.empty:
        print("⚠️ Supabase에 업로드할 데이터가 없습니다.")
        return

    print("🚀 Supabase 클라우드 DB로 데이터 전송을 시작합니다...")

    # 결측치 정제 및 문자열 변환
    df_clean = combined_df.fillna("").astype(str)
    records = []

    for _, row in df_clean.iterrows():

        def parse_num(val):
            try:
                s = str(val).replace(",", "").strip()
                return float(s) if s else 0
            except:
                return 0

        def parse_date(val):
            s = str(val).split("T")[0].replace("/", "-").strip()
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
            "total_amount": parse_num(
                row.get("금액", row.get("공급가액", 0))
            ),
        }
        records.append(record)

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    # 1,000건씩 배치 분할 전송
    batch_size = 1000
    total_records = len(records)

    for i in range(0, total_records, batch_size):
        batch = records[i : i + batch_size]
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/oms_orders", headers=headers, json=batch
        )

        if res.status_code in [200, 201]:
            print(
                f"✅ DB 동기화 완료: {min(i + batch_size, total_records)} / {total_records} 건"
            )
        else:
            print(f"❌ DB 동기화 오류 ({res.status_code}): {res.text}")


if __name__ == "__main__":
    df_oms = download_oms_data()
    if df_oms is not None and not df_oms.empty:
        sync_to_supabase(df_oms)
    else:
        print("⚠️ 데이터 수집에 실패하여 DB 동기화를 중단합니다.")
