#!/usr/bin/env python3
"""Provision the Superset datasource, datasets, charts, and 4 dashboards by code.

This replaces the manual "click through the Superset UI" runbook: it drives
Superset's REST API to create, idempotently, everything the spec's serving layer
needs on top of the 6 gold tables in Postgres:

  - one PostgreSQL database connection (the compose ``postgres`` service),
  - one dataset per gold table (``public.<table>``; columns are introspected),
  - the charts listed per dashboard below,
  - the 4 dashboards (Posts & Content, User Growth, Leaderboards, Data Quality).

It is meant to run once on boot as the ``superset-init`` compose service (see
docker-compose.yml), but it is safe to re-run: every object is looked up by name
first and only created if missing. It talks to Superset purely over HTTP
(``requests`` ships in the apache/superset image), so it needs no Superset code.

Config comes from env (compose injects these from /opt/serving/.env):
    SUPERSET_URL              default http://superset:8088
    SUPERSET_ADMIN_USER       Superset admin login (== DB_USER in this project)
    SUPERSET_ADMIN_PASSWORD   Superset admin password (== DB_PASSWORD)
    DB_NAME, DB_USER, DB_PASSWORD   Postgres connection the datasource points at
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests

URL = os.environ.get("SUPERSET_URL", "http://superset:8088").rstrip("/")
ADMIN_USER = os.environ.get("SUPERSET_ADMIN_USER", "superset")
ADMIN_PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "social_medias")
DB_USER = os.environ.get("DB_USER", "superset")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

DATABASE_NAME = "Gold (Postgres)"
# postgres is the compose service name; Superset reaches it on the compose network.
SQLALCHEMY_URI = f"postgresql://{DB_USER}:{DB_PASSWORD}@postgres:5432/{DB_NAME}"

# The 6 gold tables (schema.sql). Each becomes a Superset dataset in public schema.
GOLD_TABLES = [
    "daily_post_counts",
    "daily_users_metric",
    "top_users_by_followers",
    "top_users_by_karma",
    "top_posts_by_score",
    "data_quality_kpi",
]


class Superset:
    """Thin authenticated REST client (JWT + CSRF + session cookies)."""

    def __init__(self) -> None:
        self.s = requests.Session()

    def wait_until_ready(self, attempts: int = 90, delay: float = 5.0) -> None:
        for i in range(attempts):
            try:
                r = self.s.get(f"{URL}/health", timeout=5)
                if r.status_code == 200:
                    print(f"superset healthy after {i * delay:.0f}s", flush=True)
                    return
            except requests.RequestException:
                pass
            time.sleep(delay)
        raise SystemExit(f"superset not reachable at {URL} after {attempts * delay:.0f}s")

    def login(self) -> None:
        r = self.s.post(
            f"{URL}/api/v1/security/login",
            json={
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
                "provider": "db",
                "refresh": True,
            },
            timeout=30,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        self.s.headers.update({"Authorization": f"Bearer {token}", "Referer": URL})
        # CSRF token (required for POST/PUT); the cookie it sets rides on the session.
        c = self.s.get(f"{URL}/api/v1/security/csrf_token/", timeout=30)
        c.raise_for_status()
        self.s.headers.update({"X-CSRFToken": c.json()["result"]})

    # --- low-level helpers -------------------------------------------------
    def get(self, path: str, **kw: Any) -> requests.Response:
        return self.s.get(f"{URL}{path}", timeout=60, **kw)

    def post(self, path: str, payload: dict) -> dict:
        r = self.s.post(f"{URL}{path}", json=payload, timeout=60)
        if r.status_code not in (200, 201):
            raise SystemExit(f"POST {path} -> {r.status_code}: {r.text}")
        return r.json()

    def put(self, path: str, payload: dict) -> dict:
        r = self.s.put(f"{URL}{path}", json=payload, timeout=60)
        if r.status_code not in (200, 201):
            raise SystemExit(f"PUT {path} -> {r.status_code}: {r.text}")
        return r.json()

    def find_id(self, resource: str, col: str, value: str) -> int | None:
        """Return the id of the first row where ``col == value``, else None.

        Filters with the ``eq`` operator where the column allows it (database,
        dataset). Some resources reject ``eq`` on their title column — notably
        the dashboard API on ``dashboard_title``, which 400s — so on a 400 we
        fall back to listing the resource and matching ``col`` client-side.
        """
        q = {"filters": [{"col": col, "opr": "eq", "value": value}]}
        r = self.get(f"/api/v1/{resource}/", params={"q": json.dumps(q)})
        if r.status_code == 400:
            r = self.get(f"/api/v1/{resource}/", params={"q": json.dumps({"page_size": 100})})
            r.raise_for_status()
            for row in r.json().get("result") or []:
                if row.get(col) == value:
                    return row["id"]
            return None
        r.raise_for_status()
        data = r.json().get("result") or []
        return data[0]["id"] if data else None


def ensure_database(sup: Superset) -> int:
    existing = sup.find_id("database", "database_name", DATABASE_NAME)
    if existing:
        print(f"database exists (id={existing})", flush=True)
        return existing
    res = sup.post(
        "/api/v1/database/",
        {
            "database_name": DATABASE_NAME,
            "sqlalchemy_uri": SQLALCHEMY_URI,
            "expose_in_sqllab": True,
        },
    )
    print(f"database created (id={res['id']})", flush=True)
    return res["id"]


def ensure_dataset(sup: Superset, db_id: int, table: str) -> tuple[int, dict[str, dict]]:
    """Create the dataset if missing; return (id, {column_name: column dict})."""
    ds_id = sup.find_id("dataset", "table_name", table)
    if not ds_id:
        res = sup.post(
            "/api/v1/dataset/",
            {"database": db_id, "schema": "public", "table_name": table},
        )
        ds_id = res["id"]
        print(f"dataset {table} created (id={ds_id})", flush=True)
    else:
        print(f"dataset {table} exists (id={ds_id})", flush=True)
    # Introspected columns give adhoc metrics their full column reference.
    detail = sup.get(f"/api/v1/dataset/{ds_id}").json()["result"]
    cols = {c["column_name"]: c for c in detail.get("columns", [])}
    return ds_id, cols


# --- chart param builders --------------------------------------------------
def col_ref(cols: dict[str, dict], name: str) -> dict:
    c = cols.get(name, {})
    return {"column_name": name, "id": c.get("id"), "type": c.get("type")}


def metric(cols: dict[str, dict], agg: str, name: str) -> dict:
    return {
        "expressionType": "SIMPLE",
        "column": col_ref(cols, name),
        "aggregate": agg,
        "label": f"{agg}({name})",
        "optionName": f"metric_{agg}_{name}".lower(),
    }


def ts_params(viz: str, ds_id: int, x: str, metrics: list, groupby: list, stacked=False) -> dict:
    p = {
        "datasource": f"{ds_id}__table",
        "viz_type": viz,
        "x_axis": x,
        "time_grain_sqla": "P1D",
        "time_range": "No filter",  # gold spans historical dates; don't hide them
        "metrics": metrics,
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 10000,
        "order_desc": True,
        "color_scheme": "supersetColors",
        "show_legend": True,
        "rich_tooltip": True,
        "x_axis_sort_asc": True,
        "x_axis_sort_series": "name",
    }
    if stacked:
        p["stack"] = "Stack"
    return p


def dist_bar_params(ds_id: int, groupby: list, metrics: list, breakdown: list, row_limit: int) -> dict:
    return {
        "datasource": f"{ds_id}__table",
        "viz_type": "dist_bar",
        "metrics": metrics,
        "groupby": groupby,
        "columns": breakdown,
        "row_limit": row_limit,
        "order_desc": True,
        "time_range": "No filter",
        "adhoc_filters": [],
        "color_scheme": "supersetColors",
        "show_legend": True,
        "y_axis_format": "SMART_NUMBER",
    }


def table_raw_params(ds_id: int, columns: list, order_col: str, row_limit: int) -> dict:
    return {
        "datasource": f"{ds_id}__table",
        "viz_type": "table",
        "query_mode": "raw",
        "all_columns": columns,
        "order_by_cols": [json.dumps([order_col, False])],  # False = descending
        "row_limit": row_limit,
        "time_range": "No filter",
        "adhoc_filters": [],
        "table_timestamp_format": "smart_date",
    }


def table_agg_params(ds_id: int, groupby: list, metrics: list, row_limit: int) -> dict:
    return {
        "datasource": f"{ds_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": groupby,
        "metrics": metrics,
        "row_limit": row_limit,
        "order_desc": True,
        "time_range": "No filter",
        "adhoc_filters": [],
        "table_timestamp_format": "smart_date",
    }


def ensure_chart(sup: Superset, name: str, viz: str, ds_id: int, params: dict, dash_id: int) -> dict:
    """Create the chart if missing (linked to the dashboard); return {id, uuid, name}."""
    params = {**params, "slice_name": name}
    chart_id = sup.find_id("chart", "slice_name", name)
    if chart_id:
        print(f"chart '{name}' exists (id={chart_id})", flush=True)
        sup.put(f"/api/v1/chart/{chart_id}", {"dashboards": [dash_id]})
    else:
        res = sup.post(
            "/api/v1/chart/",
            {
                "slice_name": name,
                "viz_type": viz,
                "datasource_id": ds_id,
                "datasource_type": "table",
                "params": json.dumps(params),
                "dashboards": [dash_id],
            },
        )
        chart_id = res["id"]
        print(f"chart '{name}' created (id={chart_id})", flush=True)
    uuid = sup.get(f"/api/v1/chart/{chart_id}").json()["result"].get("uuid", f"c{chart_id}")
    return {"id": chart_id, "uuid": uuid, "name": name}


def build_position(charts: list[dict]) -> dict:
    """Two charts per row, half-width each, over Superset's 12-col grid."""
    pos: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
    }
    rows = []
    for i in range(0, len(charts), 2):
        row_id = f"ROW-{i // 2}"
        child_ids = []
        for c in charts[i : i + 2]:
            cid = f"CHART-{c['uuid']}"
            pos[cid] = {
                "type": "CHART",
                "id": cid,
                "children": [],
                "meta": {"chartId": c["id"], "width": 6, "height": 50, "sliceName": c["name"]},
                "parents": ["ROOT_ID", "GRID_ID", row_id],
            }
            child_ids.append(cid)
        pos[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": child_ids,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
        }
        rows.append(row_id)
    pos["GRID_ID"]["children"] = rows
    return pos


def ensure_dashboard(sup: Superset, title: str) -> int:
    dash_id = sup.find_id("dashboard", "dashboard_title", title)
    if dash_id:
        print(f"dashboard '{title}' exists (id={dash_id})", flush=True)
        return dash_id
    res = sup.post("/api/v1/dashboard/", {"dashboard_title": title, "published": True})
    print(f"dashboard '{title}' created (id={res['id']})", flush=True)
    return res["id"]


def main() -> None:
    if not ADMIN_PASSWORD:
        raise SystemExit("SUPERSET_ADMIN_PASSWORD is required")

    sup = Superset()
    sup.wait_until_ready()
    sup.login()

    db_id = ensure_database(sup)
    ds: dict[str, tuple[int, dict]] = {}
    for t in GOLD_TABLES:
        ds[t] = ensure_dataset(sup, db_id, t)

    def dsid(t: str) -> int:
        return ds[t][0]

    def cols(t: str) -> dict:
        return ds[t][1]

    # --- Dashboard 1: Posts & Content -------------------------------------
    d1 = ensure_dashboard(sup, "Posts & Content")
    c = [
        ensure_chart(
            sup, "Posts per day by type", "echarts_timeseries_bar", dsid("daily_post_counts"),
            ts_params("echarts_timeseries_bar", dsid("daily_post_counts"), "date",
                      [metric(cols("daily_post_counts"), "SUM", "count")], ["post_type"], stacked=True),
            d1,
        ),
        ensure_chart(
            sup, "Top posts by score", "table", dsid("top_posts_by_score"),
            table_raw_params(dsid("top_posts_by_score"), ["date", "post_type", "title", "score"], "score", 50),
            d1,
        ),
    ]
    sup.put(f"/api/v1/dashboard/{d1}", {"position_json": json.dumps(build_position(c)), "published": True})

    # --- Dashboard 2: User Growth -----------------------------------------
    d2 = ensure_dashboard(sup, "User Growth")
    c = [
        ensure_chart(
            sup, "Total users over time", "echarts_timeseries_line", dsid("daily_users_metric"),
            ts_params("echarts_timeseries_line", dsid("daily_users_metric"), "date",
                      [metric(cols("daily_users_metric"), "SUM", "total_users")], ["platform"]),
            d2,
        ),
        ensure_chart(
            sup, "New users per day", "echarts_timeseries_bar", dsid("daily_users_metric"),
            ts_params("echarts_timeseries_bar", dsid("daily_users_metric"), "date",
                      [metric(cols("daily_users_metric"), "SUM", "new_users")], ["platform"]),
            d2,
        ),
    ]
    sup.put(f"/api/v1/dashboard/{d2}", {"position_json": json.dumps(build_position(c)), "published": True})

    # --- Dashboard 3: Leaderboards ----------------------------------------
    d3 = ensure_dashboard(sup, "Leaderboards")
    c = [
        ensure_chart(
            sup, "Top users by followers (X)", "dist_bar", dsid("top_users_by_followers"),
            dist_bar_params(dsid("top_users_by_followers"), ["username"],
                            [metric(cols("top_users_by_followers"), "MAX", "followers_count")], [], 10),
            d3,
        ),
        ensure_chart(
            sup, "Top / bottom users by karma (HN)", "dist_bar", dsid("top_users_by_karma"),
            dist_bar_params(dsid("top_users_by_karma"), ["username"],
                            [metric(cols("top_users_by_karma"), "MAX", "karma_score")], ["rank_type"], 20),
            d3,
        ),
    ]
    sup.put(f"/api/v1/dashboard/{d3}", {"position_json": json.dumps(build_position(c)), "published": True})

    # --- Dashboard 4: Data Quality (KPI) ----------------------------------
    d4 = ensure_dashboard(sup, "Data Quality")
    c = [
        ensure_chart(
            sup, "Quality score over time", "echarts_timeseries_line", dsid("data_quality_kpi"),
            ts_params("echarts_timeseries_line", dsid("data_quality_kpi"), "date",
                      [metric(cols("data_quality_kpi"), "AVG", "quality_score_pct")], ["table_name"]),
            d4,
        ),
        ensure_chart(
            sup, "Latest quality by table", "table", dsid("data_quality_kpi"),
            table_agg_params(dsid("data_quality_kpi"), ["table_name"],
                             [metric(cols("data_quality_kpi"), "MAX", "quality_score_pct")], 100),
            d4,
        ),
    ]
    sup.put(f"/api/v1/dashboard/{d4}", {"position_json": json.dumps(build_position(c)), "published": True})

    print("bootstrap complete: 1 database, 6 datasets, 8 charts, 4 dashboards", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any failure to compose logs
        print(f"bootstrap failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
