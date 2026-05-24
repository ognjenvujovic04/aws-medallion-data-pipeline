import json
from datetime import date, datetime, timedelta, timezone

import boto3
import urllib.request
import urllib.parse
import os


S3_BUCKET = os.environ["DATA_LAKE_BUCKET"]
S3_PREFIX = os.environ.get("BRONZE_PREFIX", "bronze/hackernews")

ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
HEADERS = {"User-Agent": "medallion-data-platform/1.0 (HN bronze ingest)"}

HTTP_TIMEOUT = 20
HITS_PER_PAGE = 1000
MAX_REQUESTS = 100

COUNTED_TAGS = ("story", "comment", "poll", "pollopt", "job", "ask_hn", "show_hn")

s3 = boto3.client("s3")


def resolve_target_date(event):
    if isinstance(event, dict) and event.get("date"):
        return date.fromisoformat(event["date"])
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def window_for_date(target_date):
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def fetch(window_start, upper_exclusive):
    params = {
        "numericFilters": f"created_at_i>={window_start},created_at_i<{upper_exclusive}",
        "hitsPerPage": HITS_PER_PAGE,
    }
    url = f"{ALGOLIA_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handler(event, context):
    target_date = resolve_target_date(event)
    window_start, window_end = window_for_date(target_date)
    print(
        f"HN bronze ingest (Algolia): target_date={target_date.isoformat()} "
        f"window=[{window_start},{window_end})"
    )

    hits = []
    seen = set()
    cursor = window_end
    for attempt in range(MAX_REQUESTS):
        payload = fetch(window_start, cursor)
        batch = payload.get("hits", [])
        print(
            f"req={attempt} cursor={cursor} batch={len(batch)} "
            f"nbHits={payload.get('nbHits')}"
        )
        if not batch:
            break

        new = [h for h in batch if h["objectID"] not in seen]
        for h in new:
            seen.add(h["objectID"])
        hits.extend(new)

        oldest = min(h["created_at_i"] for h in batch)
        if len(batch) < HITS_PER_PAGE or oldest <= window_start:
            break
        if not new:
            print(f"WARNING: full batch with no new items at cursor={cursor}; stopping")
            break

        cursor = oldest + 1
    else:
        print(f"WARNING: hit MAX_REQUESTS={MAX_REQUESTS} cap; results may be incomplete")

    print(f"collected {len(hits)} items total")

    yyyy = target_date.strftime("%Y")
    mm = target_date.strftime("%m")
    dd = target_date.strftime("%d")
    key = f"{S3_PREFIX}/year={yyyy}/month={mm}/day={dd}/items.jsonl"

    body = "\n".join(json.dumps(h, separators=(",", ":")) for h in hits).encode("utf-8")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/x-ndjson",
    )
    print(f"wrote s3://{S3_BUCKET}/{key} ({len(body)} bytes)")

    counts = {tag: 0 for tag in COUNTED_TAGS}
    for h in hits:
        for tag in h.get("_tags", []) or []:
            if tag in counts:
                counts[tag] += 1

    return {
        "status": "ok",
        "date": target_date.isoformat(),
        "items": len(hits),
        "counts_by_tag": counts,
        "s3_key": key,
    }