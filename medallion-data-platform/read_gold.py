import awswrangler as wr

BUCKET = "storagestack-datalakee54831b2-gt9g91xsocxv"
GOLD = f"s3://{BUCKET}/gold"

TABLES = [
    "daily_post_counts",
    "daily_users_metric",
    "top_users_by_followers",
    "top_users_by_karma",
    "top_posts_by_score",
    "data_quality_kpi",
]

for table in TABLES:
    print(f"\n{'='*60}")
    print(f"  {table}")
    print('='*60)
    try:
        df = wr.s3.read_parquet(f"{GOLD}/{table}/", dataset=True)
        if df.empty:
            print("  (no data)")
        else:
            print(df.to_string(index=False))
    except Exception as e:
        print(f"  (error: {e})")