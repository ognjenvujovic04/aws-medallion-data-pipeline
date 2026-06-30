import json
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import gold_utils

S3_BUCKET = os.environ["DATA_LAKE_BUCKET"]
SILVER_PREFIX = os.environ.get("SILVER_PREFIX", "silver")
GOLD_PREFIX = os.environ.get("GOLD_PREFIX", "gold")

PLATFORM_HN = "HackerNews"
PLATFORM_X = "X"

HN_POST_TYPES = {"story", "comment", "job", "ask", "poll"}


def resolve_target_date(event) -> date:
    if isinstance(event, dict) and event.get("date"):
        return date.fromisoformat(event["date"])
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def read_posts(target_date: date) -> pd.DataFrame:
    path = f"s3://{S3_BUCKET}/{SILVER_PREFIX}/posts/"
    yyyy, mm, dd = f"{target_date:%Y}", f"{target_date:%m}", f"{target_date:%d}"
    return gold_utils.read_silver_filtered(
        path,
        partition_filter=lambda x: x["year"] == yyyy and x["month"] == mm and x["day"] == dd,
    )


def read_users(platform: str) -> pd.DataFrame:
    path = f"s3://{S3_BUCKET}/{SILVER_PREFIX}/users/"
    return gold_utils.read_silver_filtered(
        path,
        partition_filter=lambda x: x["platform"] == platform,
    )


def count_new_users(users_df: pd.DataFrame, target_date: date) -> int:
    if users_df.empty or "created_at" not in users_df.columns:
        return 0
    prefix = target_date.isoformat()
    return int((users_df["created_at"].str.startswith(prefix)).sum())


def metric_daily_post_counts(posts_df: pd.DataFrame, target_date: date) -> pd.DataFrame:
    date_str = target_date.isoformat()
    if posts_df.empty:
        return pd.DataFrame(columns=["date", "platform", "post_type", "count"])
    hn_posts = posts_df[posts_df["post_type"].isin(HN_POST_TYPES)]
    counts = (
        hn_posts.groupby("post_type", dropna=False)
        .size()
        .reset_index(name="count")
    )
    counts["date"] = date_str
    counts["platform"] = PLATFORM_HN
    counts["count"] = counts["count"].astype("int64")
    return counts[["date", "platform", "post_type", "count"]]


def metric_daily_users(hn_users: pd.DataFrame, x_users: pd.DataFrame, target_date: date) -> pd.DataFrame:
    date_str = target_date.isoformat()
    rows = [
        {
            "date": date_str,
            "platform": PLATFORM_HN,
            "total_users": len(hn_users),
            "new_users": count_new_users(hn_users, target_date),
        },
        {
            "date": date_str,
            "platform": PLATFORM_X,
            "total_users": len(x_users),
            "new_users": count_new_users(x_users, target_date),
        },
    ]
    df = pd.DataFrame(rows)
    df["total_users"] = df["total_users"].astype("int64")
    df["new_users"] = df["new_users"].astype("int64")
    return df


def metric_top_users_by_followers(x_users: pd.DataFrame, target_date: date) -> pd.DataFrame:
    date_str = target_date.isoformat()
    if x_users.empty or "followers_count" not in x_users.columns:
        return pd.DataFrame(columns=["date", "username", "platform", "followers_count"])
    top = x_users.dropna(subset=["followers_count"]
                         ).nlargest(10, "followers_count")
    result = top[["username", "followers_count"]].copy()
    result["date"] = date_str
    result["platform"] = PLATFORM_X
    result["followers_count"] = result["followers_count"].astype("Int64")
    return result[["date", "username", "platform", "followers_count"]]


def metric_top_users_by_karma(hn_users: pd.DataFrame, target_date: date) -> pd.DataFrame:
    date_str = target_date.isoformat()
    if hn_users.empty or "karma_score" not in hn_users.columns:
        return pd.DataFrame(columns=["date", "username", "karma_score", "rank_type"])
    valid = hn_users.dropna(subset=["karma_score"])
    highest = valid.nlargest(10, "karma_score")[
        ["username", "karma_score"]].copy()
    highest["rank_type"] = "highest"
    lowest = valid.nsmallest(10, "karma_score")[
        ["username", "karma_score"]].copy()
    lowest["rank_type"] = "lowest"
    df = pd.concat([highest, lowest], ignore_index=True)
    df["date"] = date_str
    df["karma_score"] = df["karma_score"].astype("Int64")
    return df[["date", "username", "karma_score", "rank_type"]]


def metric_top_posts_by_score(posts_df: pd.DataFrame, target_date: date) -> pd.DataFrame:
    date_str = target_date.isoformat()
    if posts_df.empty:
        return pd.DataFrame(columns=["date", "post_id", "title", "score", "post_type"])
    valid = posts_df.dropna(subset=["score"])
    jobs = valid[valid["post_type"] == "job"].nlargest(10, "score")
    stories = valid[valid["post_type"] == "story"].nlargest(10, "score")
    df = pd.concat([jobs, stories], ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=["date", "post_id", "title", "score", "post_type"])
    result = df[["post_id", "title", "score", "post_type"]].copy()
    result["date"] = date_str
    result["score"] = result["score"].astype("Int64")
    return result[["date", "post_id", "title", "score", "post_type"]]


def handler(event, context):
    target_date = resolve_target_date(event)
    print(f"gold compute_metrics: date={target_date.isoformat()}")

    posts_df = read_posts(target_date)
    hn_users = read_users(PLATFORM_HN)
    x_users = read_users(PLATFORM_X)
    print(
        f"silver read: posts={len(posts_df)} hn_users={len(hn_users)} x_users={len(x_users)}")

    date_str = target_date.isoformat()

    tables = {
        "daily_post_counts": (
            metric_daily_post_counts(posts_df, target_date),
            ["date"],
        ),
        "daily_users_metric": (
            metric_daily_users(hn_users, x_users, target_date),
            ["platform", "date"],
        ),
        "top_users_by_followers": (
            metric_top_users_by_followers(x_users, target_date),
            ["date"],
        ),
        "top_users_by_karma": (
            metric_top_users_by_karma(hn_users, target_date),
            ["rank_type", "date"],
        ),
        "top_posts_by_score": (
            metric_top_posts_by_score(posts_df, target_date),
            ["post_type", "date"],
        ),
    }

    written = {}
    for table_name, (df, partition_cols) in tables.items():
        path = gold_utils.gold_path(S3_BUCKET, GOLD_PREFIX, table_name)
        result = gold_utils.write_gold(df, path, partition_cols)
        written[table_name] = len(df)
        print(
            f"wrote {table_name}: rows={len(df)} paths={len(result.get('paths', []))}")

    return {
        "status": "ok",
        "date": date_str,
        "tables": written,
    }
