import requests
import json

# 💡 Supabase 클라우드 DB 접속 설정
SUPABASE_URL = "https://<your-project-ref>.supabase.co"  # Data API 탭의 Project URL 입력
SUPABASE_KEY = "sb_secret_Jwuf0..."                   # Secret Key (파이썬 봇 전용 쓰기 권한)

# OMS 수집 데이터 정제 및 DB 저장
def sync_to_supabase(combined_df):
    if combined_df.empty:
        print("⚠️ 업로드할 데이터가 없습니다.")
        return

    # DataFrame 결측치 및 데이터 타입 정제
    df_clean = combined_df.fillna('').astype(str)
    
    # Supabase 컬럼 매핑 구조로 변환
    records = []
    for _, row in df_clean.iterrows():
        # 수량, 단가, 금액 숫자 변환
        def parse_num(val):
            try:
                return float(str(val).replace(',', '').strip())
            except:
                return 0

        record = {
            "order_date": str(row.get("주문일자", "")).split("T")[0] or None,
            "delivery_date": str(row.get("배송일자", "")).split("T")[0] or None,
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

    # Supabase REST API 호출 (Bulk Insert)
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    # API 전송 (대용량인 경우 1,000건씩 배치 전송)
    batch_size = 1000
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        res = requests.post(f"{SUPABASE_URL}/rest/v1/oms_orders", headers=headers, json=batch)
        if res.status_code in [200, 201]:
            print(f"✅ Supabase DB 동기화 성공: {i + len(batch)} / {len(records)} 건 완료")
        else:
            print(f"❌ DB 동기화 실패 ({res.status_code}): {res.text}")

# 봇 실행 완료 시점에 호출
sync_to_supabase(combined_df)
