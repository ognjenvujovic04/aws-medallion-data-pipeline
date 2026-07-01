# Medallion data platform — deployment guide

A medallion-architecture data platform on AWS (bronze → silver → gold → serving) built
with **AWS CDK in Python**. All resources deploy to **`eu-central-1`**.

This is the entry point for deploying the whole thing. For the deep runbooks:

- **[`lambdas/README.md`](lambdas/README.md)** — invoking the normalize / gold / load lambdas
  and validating their output.
- **[`ec2/README.md`](ec2/README.md)** — the serving host runtime (Postgres + Superset) and
  the provisioned dashboards.

---

## One-time setup

Run everything from the `medallion-data-platform/` directory (the CDK app root), **not** the
git root.

```powershell
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt -r requirements-dev.txt

# once per account/region, if you've never used CDK here
cdk bootstrap aws://<ACCOUNT_ID>/eu-central-1
```

You also need the **AWS CLI** configured with credentials for the target account/region
(`aws configure`).

---

## The stacks

`app.py` wires the stacks below. `ServingStack` and `NotificationStack` appear only when their
`-c` flags are supplied; everything else synthesizes with no extra flags:

```powershell
cdk ls        # NetworkStack StorageStack IngestionStack NormalizationStack GoldStack StepFuncStack
              # (ServingStack appears only when -c db_password=... is passed;
              #  NotificationStack only when -c notification_email=... is passed)
cdk synth
cdk diff <StackName>
```

| Stack | Layer | What it creates |
| --- | --- | --- |
| `NetworkStack` | — | VPC, subnets, `lambda_sg` / `postgres_sg` |
| `StorageStack` | — | the single versioned S3 data-lake bucket |
| `IngestionStack` | bronze | `HackerNewsIngest` lambda (scheduled by `StepFuncStack`, not here) |
| `NormalizationStack` | silver | `HackerNewsNormalize` + `TwitterNormalize` lambdas |
| `GoldStack` | gold | `ComputeMetrics` + `ComputeKpi` lambdas |
| `ServingStack` | serving | EC2 host (Postgres + Superset) + `GoldToPostgres` lambda |
| `NotificationStack` | — | per-lambda error alarms → SNS → SES email (`EmailNotify`) |
| `StepFuncStack` | — | daily state machine orchestrating the whole pipeline |

`StepFuncStack` runs the **daily schedule**: a state machine chaining `HackerNewsIngest` →
`HackerNewsNormalize` → parallel `ComputeMetrics`/`ComputeKpi` → `GoldToPostgres`. The
`GoldToPostgres` step is included only when `ServingStack` is deployed, and a pipeline-level
failure alarm is wired to the notification topic only when `NotificationStack` is deployed.
`ServingStack` (`-c db_password=...`) and `NotificationStack` (`-c notification_email=...`) are
only instantiated when their flags are supplied, so the rest of the app still `cdk ls` /
`cdk synth` cleanly without those secrets.

---

## Deploy — full pipeline

Deploy in dependency order. The infra stacks are independent of each other and can each be
deployed on their own; the lambdas inside them are invoked afterward (see the runbooks).

```powershell
# 1. foundation
cdk deploy NetworkStack StorageStack

# 2. bronze / silver / gold lambdas
cdk deploy IngestionStack NormalizationStack GoldStack

# 3. daily orchestration (state machine + schedule)
cdk deploy StepFuncStack
```

`StepFuncStack` runs the daily pipeline end-to-end on a schedule (01:00 UTC): ingest → normalize
→ gold metrics/KPI → (if `ServingStack` is deployed) load to Postgres. The Twitter dataset
is a one-time manual backfill — upload it to bronze
([`data/bitcoin-tweets/README.md`](data/bitcoin-tweets/README.md)) and invoke `TwitterNormalize`
([`lambdas/README.md`](lambdas/README.md#normalize-twitter-x-data)).

### 3. Serving stack (new)

`ServingStack` needs three deploy-time context values passed with `-c`:

- **`db_password`** — choose a strong password (reused for the Superset admin login + secret
  key). **Never commit it.**
- **`admin_ip`** — your public IP, for Superset (`8088`) + SSH (`22`) ingress.
  Get it with `(Invoke-RestMethod "https://ifconfig.me/ip").Trim()`.
- **`key_name`** — an **existing** EC2 key pair name in `eu-central-1` (create one once with
  `aws ec2 create-key-pair` if needed).

```powershell
cdk deploy ServingStack `
  -c db_password=<CHOOSE_STRONG_PW> `
  -c admin_ip=<YOUR_PUBLIC_IP> `
  -c key_name=<EXISTING_EC2_KEYPAIR>
```

> `db_name` (default `social_medias`) and `db_user` (default `superset`) can be overridden
> with `-c db_name=... -c db_user=...`.

First boot takes a few minutes (image pulls + Superset init on a 1 GB t3.micro with swap).

### 4. Load gold → Postgres and open Superset

Run the gold lambdas (if not already), then load into Postgres:

```powershell
# ComputeMetrics + ComputeKpi, then GoldToPostgres — see the runbook for the exact commands
```

Full invoke/validate steps are in
[`lambdas/README.md`](lambdas/README.md#serving-layer--goldtopostgres--superset). Once
`GoldToPostgres` finishes, the 4 dashboards (provisioned by code) render data — open
`http://<ELASTIC_IP>:8088`. The Elastic IP and Superset URL are `ServingStack` outputs:

```powershell
aws cloudformation describe-stacks --stack-name ServingStack --region eu-central-1 `
  --query "Stacks[0].Outputs" --output table
```

See [`ec2/README.md`](ec2/README.md) for the Superset login, the dashboard specs, and how the
`superset-init` bootstrap works.

---

## Teardown

`cdk destroy <StackName>` for any stack. **Tear down `ServingStack` when you're done** — an
idle Elastic IP still bills:

```powershell
cdk destroy ServingStack -c db_password=<pw> -c admin_ip=<ip> -c key_name=<key>
```

---

## Useful commands

- `cdk ls` — list all stacks
- `cdk synth` — emit the CloudFormation templates
- `cdk diff <StackName>` — compare a deployed stack with local state
- `pytest` — run the test suite
