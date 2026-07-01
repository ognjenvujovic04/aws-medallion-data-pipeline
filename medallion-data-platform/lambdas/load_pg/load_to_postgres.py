"""Load gold parquet into the serving Postgres via upsert.

Reads each gold table from S3 (written by lambdas/gold/compute_metrics.py and
compute_kpi.py) and upserts it into the matching Postgres table on the EC2 serving
host. Idempotent: each table upserts on its natural key, so re-invoking after a new
gold run refreshes rows in place instead of duplicating them.

Runs in the private-isolated subnet behind lambda_sg, which is allowed to reach the
EC2 postgres_sg on 5432. Credentials arrive as env vars (no Secrets Manager: the
isolated subnet reaches S3 only, via the gateway endpoint). awswrangler and its
bundled pg8000 driver come from the AWS SDK for pandas layer.

Flat import style (deployment root is this handler's dir), matching the other lambdas.
"""

import os

import awswrangler as wr
import pg8000  # DBAPI driver bundled in the pandas layer

# table name -> upsert conflict columns (must match schema.sql PRIMARY KEYs).
TABLES = {
    "daily_post_counts": ["date", "platform", "post_type"],
    "daily_users_metric": ["date", "platform"],
    "top_users_by_followers": ["date", "username"],
    "top_users_by_karma": ["date", "rank_type", "username"],
    "top_posts_by_score": ["date", "post_type", "post_id"],
    "data_quality_kpi": ["date", "table_name"],
}


def _connect():
    # awswrangler's to_sql requires a DBAPI pg8000 connection (pg8000.connect /
    # pg8000.legacy.Connection), NOT pg8000.native.Connection.
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def handler(event, context):
    bucket = os.environ["DATA_LAKE_BUCKET"]
    gold_prefix = os.environ.get("GOLD_PREFIX", "gold").strip("/")

    summary = {}
    con = _connect()
    try:
        for table, conflict_cols in TABLES.items():
            path = f"s3://{bucket}/{gold_prefix}/{table}/"
            try:
                df = wr.s3.read_parquet(path, dataset=True)
            except (wr.exceptions.NoFilesFound, FileNotFoundError):
                summary[table] = "no gold parquet found — skipped"
                continue

            if df.empty:
                summary[table] = "empty — skipped"
                continue

            wr.postgresql.to_sql(
                df=df,
                con=con,
                table=table,
                schema="public",
                mode="upsert",
                upsert_conflict_columns=conflict_cols,
                use_column_names=True,
            )
            summary[table] = int(len(df))
    finally:
        con.close()

    return {"loaded": summary}
