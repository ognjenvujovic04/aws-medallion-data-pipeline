import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import boto3
import pandas as pd

import utils

S3_BUCKET = os.environ["DATA_LAKE_BUCKET"]
BRONZE_PREFIX = os.environ.get("BRONZE_PREFIX", "bronze/hackernews")
SILVER_PREFIX = os.environ.get("SILVER_PREFIX", "silver")

PLATFORM = utils.PLATFORM_HACKER_NEWS

TAG_TO_TYPE = [
    ("comment", "comment"),
    ("pollopt", "poll"),
    ("poll", "poll"),
    ("job", "job"),
    ("ask_hn", "ask"),
    ("show_hn", "story"),
    ("story", "story"),
]

s3 = boto3.client("s3")

HN_USER_URL = "https://hacker-news.firebaseio.com/v0/user/{}.json"
_KARMA_WORKERS = 20
_KARMA_TIMEOUT = 10


def _fetch_one_karma(username):
    url = HN_USER_URL.format(urllib.parse.quote(username, safe=""))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "medallion-data-platform/1.0"}
        )
        with urllib.request.urlopen(req, timeout=_KARMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                return username, data.get("karma")
    except Exception:
        pass
    return username, None


def fetch_karma_scores(usernames):
    scores = {}
    with ThreadPoolExecutor(max_workers=_KARMA_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_karma, u): u for u in usernames}
        for future in as_completed(futures):
            username, karma = future.result()
            scores[username] = karma
    fetched = sum(v is not None for v in scores.values())
    print(f"karma fetched: {fetched}/{len(scores)} users")
    return scores


def resolve_target_date(event):
    if isinstance(event, dict) and event.get("date"):
        return date.fromisoformat(event["date"])
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def bronze_key(target_date):
    return (
        f"{BRONZE_PREFIX.strip('/')}/year={target_date:%Y}"
        f"/month={target_date:%m}/day={target_date:%d}/items.jsonl"
    )


def read_bronze_hits(key):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        print(f"no bronze object at s3://{S3_BUCKET}/{key}")
        return []
    body = obj["Body"].read().decode("utf-8")
    hits = [json.loads(line) for line in body.splitlines() if line.strip()]
    del body
    return hits


def classify(hit):
    tags = hit.get("_tags") or []
    for tag, post_type in TAG_TO_TYPE:
        if tag in tags:
            return post_type
    return "story"


def build_post(hit):
    post_type = classify(hit)
    title = utils.strip_html(hit.get("title"))

    if post_type == "comment":
        content = utils.strip_html(hit.get("comment_text"))
    else:
        parts = [p for p in (title, utils.strip_html(hit.get("story_text"))) if p]
        content = "\n\n".join(parts) if parts else None

    score = hit.get("points")
    parent = hit.get("parent_id")

    return {
        "post_id": str(hit["objectID"]),
        "author_username": hit.get("author"),
        "title": title if post_type != "comment" else None,
        "content_text": content,
        "post_type": post_type,
        "score": int(score) if score is not None else None,
        "url": hit.get("url"),
        "parent_id": str(parent) if parent is not None else None,
        "created_at": utils.epoch_to_utc_iso(hit.get("created_at_i")),
    }


def build_posts_frame(posts):
    df = pd.DataFrame(posts, columns=utils.POSTS_COLUMNS)
    df["year"] = df["created_at"].str.slice(0, 4)
    df["month"] = df["created_at"].str.slice(5, 7)
    df["day"] = df["created_at"].str.slice(8, 10)
    df["score"] = df["score"].astype("Int64")
    df = df[df["year"].notna()]
    df = df.drop_duplicates(subset=["post_id"], keep="last")
    return df


def build_users_frame(hits):
    earliest = {}
    for hit in hits:
        author = hit.get("author")
        created = utils.epoch_to_utc_iso(hit.get("created_at_i"))
        if not author or not created:
            continue
        if author not in earliest or created < earliest[author]:
            earliest[author] = created

    karma_scores = fetch_karma_scores(list(earliest.keys()))

    rows = [
        {
            "user_id": utils.make_user_id(PLATFORM, author),
            "username": author,
            "karma_score": karma_scores.get(author),
            "is_verified": None,
            "followers_count": None,
            "created_at": created,
        }
        for author, created in earliest.items()
    ]
    df = pd.DataFrame(rows, columns=utils.USERS_COLUMNS)
    df["karma_score"] = df["karma_score"].astype("Int64")
    df["followers_count"] = df["followers_count"].astype("Int64")
    df["is_verified"] = df["is_verified"].astype("boolean")
    return df


def handler(event, context):
    target_date = resolve_target_date(event)
    key = bronze_key(target_date)
    print(f"HN silver normalize: date={target_date.isoformat()} src=s3://{S3_BUCKET}/{key}")

    hits = read_bronze_hits(key)
    if not hits:
        return {"status": "empty", "date": target_date.isoformat(), "posts": 0, "users": 0}

    raw_hits = len(hits)
    print(f"hits={raw_hits}", flush=True)
    users_df = build_users_frame(hits)
    print(f"users_df rows={len(users_df)}", flush=True)

    posts_records = [build_post(h) for h in hits]
    del hits
    posts_df = build_posts_frame(posts_records)
    del posts_records
    print(f"posts_df rows={len(posts_df)} dtypes={posts_df.dtypes.to_dict()}", flush=True)

    print("writing posts parquet...", flush=True)
    utils.write_dataset(
        posts_df, utils.posts_path(S3_BUCKET, SILVER_PREFIX),
        partition_cols=["year", "month", "day"],
    )
    print("posts written; upserting users...", flush=True)

    merged_users = utils.upsert_users(users_df, PLATFORM, S3_BUCKET, SILVER_PREFIX)
    print("users upserted", flush=True)

    result = {
        "status": "ok",
        "date": target_date.isoformat(),
        "raw_hits": raw_hits,
        "posts": int(len(posts_df)),
        "users_today": int(len(users_df)),
        "users_total": int(len(merged_users)),
        "post_types": posts_df["post_type"].value_counts().to_dict(),
    }
    print(json.dumps(result, default=str))
    return result
