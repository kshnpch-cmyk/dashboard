import os
import sys
import time
import json
import requests
import pandas as pd
from datetime import datetime

# 💡 GitHub Secrets 환경변수에서 키를 가져옵니다 (보안 처리)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kshnpch-cmyk.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

def sync_to_supabase(combined_df):
    if combined_df is None or combined_df.empty:
        print("⚠️ Supabase에 업로드할 데이터가 없습니다.")
        return

    print("🚀 Supabase 클라우드 DB로 데이터 전송을 시작합니다...")

    # 데이터 정제 및 결측치 처리
    df_clean = combined_df.fillna('').astype(str)
    
    records = []
    for _, row in df_clean.iterrows():
        def parse_num(val):
            try:
                s = str(val).replace(',', '').strip()
                return float(s) if s else 0
            except:
                return 0

        # 날짜 포맷 정제 (YYYY-MM-DD)
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

    # API 헤더 설정 (Secrets 환경변수 사용)
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    # 1,000건씩 분할 대량 전송 (Bulk Insert)
    batch_size = 1000
    total_records = len(records)
    
    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        res = requests.post(f"{SUPABASE_URL}/rest/v1/oms_orders", headers=headers, json=batch)
        
        if res.status_code in [200, 201]:
            print(f"✅ DB 동기화 진행 중: {min(i + batch_size, total_records)} / {total_records} 건 완료")
        else:
            print(f"❌ DB 동기화 오류 ({res.status_code}): {res.text}")

if __name__ == "__main__":
    # 스크립트 단독 테스트 또는 수집 데이터 로드 후 실행
    print("스크립트가 정상 등록되었습니다.")
