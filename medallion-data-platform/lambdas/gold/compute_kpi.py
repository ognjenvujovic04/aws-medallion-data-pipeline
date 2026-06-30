import json
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import gold_utils

S3_BUCKET = os.environ["DATA_LAKE_BUCKET"]
SILVER_PREFIX = os.environ.get("SILVER_PREFIX", "silver")
GOLD_PREFIX = os.environ.get("GOLD_PREFIX", "gold")


def resolve_target_date(event) -> date:
    if isinstance(event, dict) and event.get("date"):
        return date.fromisoformat(event["date"])
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def quality_score(df: pd.DataFrame, table_name: str, date_str: str) -> dict:
    if df.empty:
        return {
            "date": date_str,
            "table_name": table_name,
            "total_rows": 0,
            "total_cells": 0,
            "non_null_cells": 0,
            "quality_score_pct": 0.0,
        }
    total_rows = len(df)
    total_cells = df.size
    non_null_cells = int(df.notna().sum().sum())
    pct = round((non_null_cells / total_cells) * 100, 2) if total_cells > 0 else 0.0
    return {
        "date": date_str,
        "table_name": table_name,
        "total_rows": total_rows,
        "total_cells": total_cells,
        "non_null_cells": non_null_cells,
        "quality_score_pct": pct,
    }


def handler(event, context):
    target_date = resolve_target_date(event)
    date_str = target_date.isoformat()
    print(f"gold compute_kpi: date={date_str}")

    yyyy, mm, dd = f"{target_date:%Y}", f"{target_date:%m}", f"{target_date:%d}"

    posts_df = gold_utils.read_silver_filtered(
        f"s3://{S3_BUCKET}/{SILVER_PREFIX}/posts/",
        partition_filter=lambda x: x["year"] == yyyy and x["month"] == mm and x["day"] == dd,
    )
    hn_users_df = gold_utils.read_silver_filtered(
        f"s3://{S3_BUCKET}/{SILVER_PREFIX}/users/",
        partition_filter=lambda x: x["platform"] == "HackerNews",
    )
    x_users_df = gold_utils.read_silver_filtered(
        f"s3://{S3_BUCKET}/{SILVER_PREFIX}/users/",
        partition_filter=lambda x: x["platform"] == "X",
    )

    rows = [
        quality_score(posts_df, "silver_posts", date_str),
        quality_score(hn_users_df, "silver_users_hackernews", date_str),
        quality_score(x_users_df, "silver_users_x", date_str),
    ]

    kpi_df = pd.DataFrame(rows)
    kpi_df["total_rows"] = kpi_df["total_rows"].astype("int64")
    kpi_df["total_cells"] = kpi_df["total_cells"].astype("int64")
    kpi_df["non_null_cells"] = kpi_df["non_null_cells"].astype("int64")
    kpi_df["quality_score_pct"] = kpi_df["quality_score_pct"].astype("float64")

    path = gold_utils.gold_path(S3_BUCKET, GOLD_PREFIX, "data_quality_kpi")
    gold_utils.write_gold(kpi_df, path, partition_cols=["date"])
    print(json.dumps(rows, default=str))

    return {
        "status": "ok",
        "date": date_str,
        "kpis": rows,
    }
