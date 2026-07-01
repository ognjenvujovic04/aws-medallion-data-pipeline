-- Gold metrics/KPI tables for the serving layer (PostgreSQL).
--
-- Auto-applied by the postgres container on first init: the file is mounted into
-- /docker-entrypoint-initdb.d, which the official postgres image runs once when the
-- data volume is empty. Idempotent (CREATE TABLE IF NOT EXISTS), so it is safe to
-- re-run manually too.
--
-- Each PRIMARY KEY matches the GoldToPostgres lambda's upsert conflict columns, so
-- repeated loads upsert in place instead of duplicating rows. Column lists mirror
-- the gold parquet produced by lambdas/gold/compute_metrics.py and compute_kpi.py.

CREATE TABLE IF NOT EXISTS daily_post_counts (
    date       DATE        NOT NULL,
    platform   VARCHAR(20) NOT NULL,
    post_type  VARCHAR(20) NOT NULL,
    count      BIGINT      NOT NULL,
    PRIMARY KEY (date, platform, post_type)
);

CREATE TABLE IF NOT EXISTS daily_users_metric (
    date         DATE        NOT NULL,
    platform     VARCHAR(20) NOT NULL,
    total_users  BIGINT      NOT NULL,
    new_users    BIGINT      NOT NULL,
    PRIMARY KEY (date, platform)
);

CREATE TABLE IF NOT EXISTS top_users_by_followers (
    date             DATE         NOT NULL,
    username         VARCHAR(255) NOT NULL,
    platform         VARCHAR(20)  NOT NULL,
    followers_count  BIGINT,
    PRIMARY KEY (date, username)
);

CREATE TABLE IF NOT EXISTS top_users_by_karma (
    date         DATE         NOT NULL,
    username     VARCHAR(255) NOT NULL,
    karma_score  BIGINT,
    rank_type    VARCHAR(20)  NOT NULL,
    PRIMARY KEY (date, rank_type, username)
);

CREATE TABLE IF NOT EXISTS top_posts_by_score (
    date       DATE         NOT NULL,
    post_id    VARCHAR(255) NOT NULL,
    title      TEXT,
    score      BIGINT,
    post_type  VARCHAR(20)  NOT NULL,
    PRIMARY KEY (date, post_type, post_id)
);

CREATE TABLE IF NOT EXISTS data_quality_kpi (
    date               DATE          NOT NULL,
    table_name         VARCHAR(255)  NOT NULL,
    total_rows         BIGINT        NOT NULL,
    total_cells        BIGINT        NOT NULL,
    non_null_cells     BIGINT        NOT NULL,
    quality_score_pct  NUMERIC(5,2)  NOT NULL,
    PRIMARY KEY (date, table_name)
);
