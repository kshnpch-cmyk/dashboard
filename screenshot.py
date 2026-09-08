# 💡 11. 대시보드(index.html) 초고속 로드용 2차원 배열 JSON 생성
    json_file_path = "dashboard_data.json"
    current_version = 1

    if os.path.exists(json_file_path):
        try:
            with open(json_file_path, "r", encoding="utf-8") as f:
                old_json = json.load(f)
                current_version = old_json.get("version", 0) + 1
        except Exception:
            current_version = 1

    df_clean = combined_df.fillna('').astype(str)
    headers = df_clean.columns.tolist()
    data_matrix = [headers] + df_clean.values.tolist()

    json_data = {
        "version": current_version,
        "updated_at": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "total_rows": len(combined_df),
        "data": data_matrix
    }

    with open(json_file_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    json_size_mb = round(os.path.getsize(json_file_path) / (1024 * 1024), 2)
    print(f"📄 대시보드 연동용 'dashboard_data.json' 최적화 완료 (버전: v{current_version} / 용량: {json_size_mb} MB)")
