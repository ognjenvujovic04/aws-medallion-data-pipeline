# Serving host — Postgres + Superset (EC2)

The serving layer runs on a single **t3.micro** EC2 instance provisioned by
[`stacks/serving_stack.py`](../stacks/serving_stack.py) (`ServingStack`). Two Docker
containers run on it (see [`docker-compose.yml`](docker-compose.yml)):

- **`postgres:16`** — the analytics store. Holds the 6 gold tables (schema in
  [`schema.sql`](schema.sql), auto-applied on first init). Reachable in-VPC on `5432`
  by the `GoldToPostgres` Lambda; **not** exposed to the public internet.
- **`apache/superset:3.1.3`** — the dashboards UI on port `8088`.
- **`superset-init`** — a one-shot container (same Superset image) that runs
  [`superset_bootstrap.py`](superset_bootstrap.py) to provision the datasource, datasets,
  charts, and 4 dashboards by code, then exits. Idempotent; see §3.

The EC2 user-data ([`user_data.sh`](user_data.sh)) provisions the host (swap, Docker,
compose) after `ServingStack` stages `/opt/serving/{docker-compose.yml, schema.sql, .env}`.
Human access (Superset `8088`, SSH `22`) is restricted to the `admin_ip` you pass as CDK
context; `5432` stays lambda-only.

Region: **eu-central-1**. All credentials come from CDK context (`db_name`, `db_user`,
`db_password`) — the Superset admin login and secret key reuse `db_user`/`db_password`.

---

## 1. Deploy and reach the host

Deploy `ServingStack` (see [`../lambdas/README.md`](../lambdas/README.md#serving-layer--goldtopostgres--superset)
for the full context flags). After deploy, the stack outputs the **Elastic IP** and the
**Superset URL**:

```powershell
aws cloudformation describe-stacks --stack-name ServingStack --region eu-central-1 `
  --query "Stacks[0].Outputs" --output table
```

- **Superset:** `http://<ELASTIC_IP>:8088`
- **SSH:** `ssh -i <your-key>.pem ec2-user@<ELASTIC_IP>`

> First boot takes a few minutes (image pulls + Superset init). If the UI 502s, wait and
> retry. To watch progress: `ssh` in, then `sudo docker compose -f /opt/serving/docker-compose.yml ps`
> and `... logs -f superset`.

## 2. Log in to Superset

Open `http://<ELASTIC_IP>:8088` and log in with the admin credentials
(**username = `db_user`**, **password = `db_password`** — i.e. the same values you passed
as CDK context; defaults `superset` / *your chosen password*).

## 3. Datasource + dashboards are provisioned by code

You do **not** click these together in the UI. The `superset-init` compose service runs
[`superset_bootstrap.py`](superset_bootstrap.py) once Superset is serving and creates,
idempotently, over the Superset REST API:

- the **PostgreSQL datasource** (`Gold (Postgres)`, URI
  `postgresql://<db_user>:<db_password>@postgres:5432/<db_name>` — the Superset container
  reaches Postgres by its compose service name `postgres`),
- one **dataset** per gold table (`public.<table>`; columns are introspected), and
- the **8 charts** and **4 dashboards** described below.

So after `cdk deploy ServingStack` and a `GoldToPostgres` load, the dashboards already
exist in the UI — nothing to assemble by hand. To (re-)run it manually on the host:

```bash
cd /opt/serving
sudo docker compose up -d superset-init          # re-provision; safe to repeat (idempotent)
sudo docker compose logs superset-init           # look for "bootstrap complete: ..."
```

> The datasets exist immediately; the charts render data once `GoldToPostgres` has upserted
> the gold parquet — see [`../lambdas/README.md`](../lambdas/README.md#serving-layer--goldtopostgres--superset).

## 4. What the code builds (the 4 dashboards)

The chart specs below are the source of truth encoded in `superset_bootstrap.py`; edit the
script (not the UI) to change them, then re-run step 3.

### Dashboard 1 — Posts & Content

| Chart | Dataset | Type | Config |
| --- | --- | --- | --- |
| Posts per day by type | `daily_post_counts` | Bar (stacked) | X = `date`, Y = `SUM(count)`, series = `post_type` |
| Top posts by score | `top_posts_by_score` | Table | Columns `date, post_type, title, score`; sort `score` desc |

### Dashboard 2 — User Growth

| Chart | Dataset | Type | Config |
| --- | --- | --- | --- |
| Total users over time | `daily_users_metric` | Line (time-series) | X = `date`, Y = `SUM(total_users)`, series = `platform` |
| New users per day | `daily_users_metric` | Bar | X = `date`, Y = `SUM(new_users)`, series = `platform` |

### Dashboard 3 — Leaderboards

| Chart | Dataset | Type | Config |
| --- | --- | --- | --- |
| Top users by followers (X) | `top_users_by_followers` | Bar | Dimension = `username`, metric = `MAX(followers_count)`; row limit 10 |
| Top / bottom users by karma (HN) | `top_users_by_karma` | Bar | Dimension = `username`, metric = `MAX(karma_score)`, split by `rank_type` |

### Dashboard 4 — Data Quality (KPI)

| Chart | Dataset | Type | Config |
| --- | --- | --- | --- |
| Quality score over time | `data_quality_kpi` | Line | X = `date`, Y = `AVG(quality_score_pct)`, series = `table_name` |
| Latest quality by table | `data_quality_kpi` | Table | `MAX(quality_score_pct)` per `table_name` |

> Charts use `time_range: "No filter"` so historical gold dates aren't hidden by Superset's
> default "Last week" window. Add per-chart date filters in the UI if you want a "latest" view.

---

## 5. Teardown

When grading/demo is done, tear the whole serving layer down so the instance **and** the
Elastic IP stop billing (an unassociated EIP is still charged):

```powershell
cdk destroy ServingStack -c db_password=<pw> -c admin_ip=<ip> -c key_name=<key>
```
