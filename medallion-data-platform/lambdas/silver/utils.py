import html
import re
import uuid
from datetime import datetime, timezone

import awswrangler as wr
import pandas as pd

PLATFORM_HACKER_NEWS = "HackerNews"
PLATFORM_X = "X"

USER_ID_NAMESPACE = uuid.UUID("6f0c9e1a-3b2d-5c4e-8a7f-1d2e3f405162")


USERS_COLUMNS = [
    "user_id",
    "username",
    # "platform"      -> partition column ("HackerNews" / "X")
    "karma_score",
    "is_verified",
    "followers_count",
    "created_at",
]

POSTS_COLUMNS = [
    "post_id",
    "author_username",
    "title",
    "content_text",
    "post_type",
    "score",
    "url",
    "parent_id",
    "created_at",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text):
    if text is None:
        return None
    text = re.sub(r"<p>", "\n\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def epoch_to_utc_iso(epoch_seconds):
    if epoch_seconds is None:
        return None
    dt = datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_iso_utc(value):

    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_user_id(platform, username):
    return str(uuid.uuid5(USER_ID_NAMESPACE, f"{platform}:{username}"))


def make_post_id(platform, *parts):
    key = ":".join([platform, *(str(p) for p in parts)])
    return str(uuid.uuid5(USER_ID_NAMESPACE, key))

def silver_root(bucket, silver_prefix):
    return f"s3://{bucket}/{silver_prefix.strip('/')}"


def users_path(bucket, silver_prefix):
    return f"{silver_root(bucket, silver_prefix)}/users/"


def posts_path(bucket, silver_prefix):
    return f"{silver_root(bucket, silver_prefix)}/posts/"

def write_dataset(df, path, partition_cols, mode="overwrite_partitions"):
    if df.empty:
        return {"paths": [], "partitions_values": {}}
    return wr.s3.to_parquet(
        df=df,
        path=path,
        dataset=True,
        partition_cols=partition_cols,
        mode=mode,
        compression="snappy",
        index=False,
    )


def read_dataset(path):
    try:
        if not wr.s3.list_objects(path):
            return pd.DataFrame()
        return wr.s3.read_parquet(path=path, dataset=True)
    except (wr.exceptions.NoFilesFound, OSError):
        return pd.DataFrame()


def upsert_users(new_users, platform, bucket, silver_prefix):
    """Merge new users for one platform into the silver users dataset.

    Reads the existing platform partition, concatenates the new rows, keeps the
    earliest row per username, and rewrites the platform partition. Returns the
    merged frame (platform-scoped).
    """
    path = users_path(bucket, silver_prefix)
    existing = read_dataset(path)
    if not existing.empty:
        existing = existing[existing.get("platform") == platform]
        existing = existing.drop(columns=["platform"], errors="ignore")

    combined = pd.concat([existing, new_users], ignore_index=True)
    if combined.empty:
        return combined

    combined = combined.sort_values("created_at").drop_duplicates(
        subset=["username"], keep="first"
    )
    combined["platform"] = platform
    combined["karma_score"] = combined["karma_score"].astype("Int64")
    combined["followers_count"] = combined["followers_count"].astype("Int64")
    combined["is_verified"] = combined["is_verified"].astype("boolean")
    write_dataset(combined, path, partition_cols=["platform"])
    return combined
