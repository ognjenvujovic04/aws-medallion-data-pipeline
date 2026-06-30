# Lambdas

Silver-layer normalization functions live in [`silver/`](silver/). They read raw
data from the **bronze** prefix of the data lake and write normalized,
date-partitioned Parquet datasets to the **silver** prefix.

| Function file | CDK logical id | Handler | Source → dest |
| --- | --- | --- | --- |
| `silver/normalize_hn.py` | `HackerNewsNormalize` | `normalize_hn.handler` | `bronze/hackernews` → `silver` |
| `silver/normalize_twitter.py` | `TwitterNormalize` | `normalize_twitter.handler` | `bronze/twitter` → `silver` |

Both are defined in [`stacks/normalization_stack.py`](../stacks/normalization_stack.py)
as part of the `NormalizationStack`. Region: **eu-central-1**.

---

## Normalize Twitter (X) data

### 1. Prerequisites

- The dataset is uploaded to bronze — see
  [`data/bitcoin-tweets/README.md`](../data/bitcoin-tweets/README.md).
  Expected S3 path: `bronze/twitter/dataset=bitcoin-tweets/bitcoin_tweets.csv`.
- AWS CLI configured with credentials for the target account/region.

### 2. Deploy the stack

The Lambda is provisioned by CDK. Deploy (or re-deploy after code changes) the
normalization stack from the project root (`medallion-data-platform/`):

```powershell
cdk deploy NormalizationStack
```

### 3. Resolve the deployed function + bucket names

CloudFormation appends a hash to logical ids (the function is named
`TwitterNormalize<hash>`, not `TwitterNormalize`), so match on the name substring
instead of an exact logical id:

```powershell
$region = "eu-central-1"

$fn = aws lambda list-functions --region $region `
  --query "Functions[?contains(FunctionName,'TwitterNormalize')].FunctionName" `
  --output text

$bucket = aws cloudformation describe-stack-resources --stack-name StorageStack `
  --region $region `
  --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" `
  --output text

"fn     = $fn"
"bucket = $bucket"
```

Both must print a value before continuing.

### 4. Invoke the function

The handler reads its input from S3, so no event payload is required — an empty
event is enough to trigger a full run. `--cli-read-timeout 0` keeps the CLI from
giving up while a large run finishes:

```powershell
aws lambda invoke `
  --function-name $fn `
  --payload '{}' `
  --cli-binary-format raw-in-base64-out `
  --cli-read-timeout 0 `
  --region $region `
  response.json

# inspect the result summary returned by the handler
Get-Content response.json
```

> For the full ~1 GB run (several minutes) invoke **async** instead and watch the
> logs (step 5): add `--invocation-type Event` to the command above.

A successful run returns JSON like:

```json
{ "status": "ok", "rows": 2500000, "posts": 2487...,
  "users_today": ..., "users_total": ..., "post_types": { "tweet": ..., "retweet": ... } }
```

> The Twitter dataset (~1 GB / 2.5M rows) is streamed in 100k-row chunks. The
> function has a **15 min** timeout and **2048 MB** memory — comfortably within
> the free tier for a one-time run.

### 5. Watch the logs (optional)

The handler prints per-chunk progress. Tail the log group while it runs:

```powershell
aws logs tail "/aws/lambda/$fn" --follow --region $region
```

Look for `chunk=1..N rows=... cum_rows=... posts_written=...`, `users upserted`,
and a final `REPORT` line whose **`Max Memory Used` stays well under 2048 MB** —
that confirms the chunked streaming keeps memory flat.

### 6. Validate the silver output

Check that parquet landed in the spec-aligned layout — **posts partitioned by date,
users by platform**:

```powershell
# posts → year/month/day
aws s3 ls "s3://$bucket/silver/posts/" --recursive --human-readable --region $region

# users → platform=X
aws s3 ls "s3://$bucket/silver/users/platform=X/" --recursive --human-readable --region $region
```

Expect paths like `silver/posts/year=2021/month=02/day=05/xxxx.snappy.parquet`
and `silver/users/platform=X/xxxx.snappy.parquet`.

It worked if: the invoke returned `status: ok` with `rows`/`posts` close to the
input size, both prefixes above contain parquet, and the log `REPORT` showed
memory well under 2048 MB.

> Hacker News is ingested and normalized automatically on a schedule, so its
> normalize function does not need to be invoked manually.
