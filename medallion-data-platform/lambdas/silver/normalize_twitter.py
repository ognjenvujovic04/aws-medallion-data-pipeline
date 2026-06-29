import json
import os

import awswrangler as wr
import pandas as pd

import utils

S3_BUCKET = os.environ["DATA_LAKE_BUCKET"]
TWITTER_PREFIX = os.environ.get("TWITTER_PREFIX", "bronze/twitter")
SILVER_PREFIX = os.environ.get("SILVER_PREFIX", "silver")

PLATFORM = utils.PLATFORM_X

# The dataset can be large; read it in chunks so the raw CSV is never fully
# materialized in memory -- we only keep the slim mapped records.
CHUNK_SIZE = 100_000

TRUE_STRINGS = {"true", "1", "yes", "t", "y"}


def twitter_source_path():
    return f"s3://{S3_BUCKET}/{TWITTER_PREFIX.strip('/')}/"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _to_bool(value):
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return cleaned.lower() in TRUE_STRINGS


def _to_int(value):
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def classify(is_retweet):
    return "retweet" if _to_bool(is_retweet) else "tweet"


def build_post(row):
    username = _clean(row.get("user_name"))
    content = utils.strip_html(_clean(row.get("text")))
    return {
        "post_id": utils.make_post_id(PLATFORM, username, row.get("date"), content),
        "author_username": username,
        "title": None,
        "content_text": content,
        "post_type": classify(row.get("is_retweet")),
        "score": None,
        "url": None,
        "parent_id": None,
        "created_at": utils.normalize_iso_utc(row.get("date")),
    }


def build_posts_frame(posts):
    df = pd.DataFrame(posts, columns=utils.POSTS_COLUMNS)
    df = df[df["created_at"].notna()]
    df["year"] = df["created_at"].str.slice(0, 4)
    df["month"] = df["created_at"].str.slice(5, 7)
    df["day"] = df["created_at"].str.slice(8, 10)
    df["score"] = df["score"].astype("Int64")
    df = df.drop_duplicates(subset=["post_id"], keep="last")
    return df


def update_users(acc, row):
    username = _clean(row.get("user_name"))
    if not username:
        return
    created_at = utils.normalize_iso_utc(row.get("user_created"))
    current = acc.get(username)
    if current is None or (
        created_at is not None
        and (current["created_at"] is None or created_at < current["created_at"])
    ):
        acc[username] = {
            "created_at": created_at,
            "followers_count": _to_int(row.get("user_followers")),
            "is_verified": _to_bool(row.get("user_verified")),
        }


def build_users_frame(acc):
    rows = [
        {
            "user_id": utils.make_user_id(PLATFORM, username),
            "username": username,
            "karma_score": None,
            "is_verified": info["is_verified"],
            "followers_count": info["followers_count"],
            "created_at": info["created_at"],
        }
        for username, info in acc.items()
    ]
    df = pd.DataFrame(rows, columns=utils.USERS_COLUMNS)
    df["karma_score"] = df["karma_score"].astype("Int64")
    df["followers_count"] = df["followers_count"].astype("Int64")
    df["is_verified"] = df["is_verified"].astype("boolean")
    return df


def handler(event, context):
    path = twitter_source_path()
    print(f"X silver normalize: src={path}")

    posts_records = []
    users_acc = {}
    total_rows = 0
    chunks = 0

    try:
        for chunk in wr.s3.read_csv(path=path, dataset=False, chunksize=CHUNK_SIZE):
            chunks += 1
            records = chunk.to_dict("records")
            total_rows += len(records)
            for row in records:
                posts_records.append(build_post(row))
                update_users(users_acc, row)
            print(f"chunk={chunks} rows={len(records)} cum_rows={total_rows}", flush=True)
    except wr.exceptions.NoFilesFound:
        print(f"no twitter CSV at {path}")
        return {"status": "empty", "posts": 0, "users": 0}

    if not posts_records:
        return {"status": "empty", "posts": 0, "users": 0}

    posts_df = build_posts_frame(posts_records)
    del posts_records
    print(f"posts_df rows={len(posts_df)} dtypes={posts_df.dtypes.to_dict()}", flush=True)

    print("writing posts parquet...", flush=True)
    utils.write_dataset(
        posts_df, utils.posts_path(S3_BUCKET, SILVER_PREFIX),
        partition_cols=["year", "month", "day"],
    )
    print("posts written; upserting users...", flush=True)

    users_df = build_users_frame(users_acc)
    print(f"users_df rows={len(users_df)}", flush=True)
    merged_users = utils.upsert_users(users_df, PLATFORM, S3_BUCKET, SILVER_PREFIX)
    print("users upserted", flush=True)

    result = {
        "status": "ok",
        "rows": total_rows,
        "posts": int(len(posts_df)),
        "users_today": int(len(users_df)),
        "users_total": int(len(merged_users)),
        "post_types": posts_df["post_type"].value_counts().to_dict(),
    }
    print(json.dumps(result, default=str))
    return result
