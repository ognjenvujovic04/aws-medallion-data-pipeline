import awswrangler as wr
import pandas as pd


def write_gold(df: pd.DataFrame, path: str, partition_cols: list) -> dict:
    if df.empty:
        return {"paths": [], "partitions_values": {}}
    return wr.s3.to_parquet(
        df=df,
        path=path,
        dataset=True,
        partition_cols=partition_cols,
        mode="overwrite_partitions",
        compression="snappy",
        index=False,
    )


def read_silver(path: str) -> pd.DataFrame:
    try:
        if not wr.s3.list_objects(path):
            return pd.DataFrame()
        return wr.s3.read_parquet(path=path, dataset=True)
    except (wr.exceptions.NoFilesFound, OSError):
        return pd.DataFrame()


def read_silver_filtered(path: str, partition_filter) -> pd.DataFrame:
    try:
        return wr.s3.read_parquet(path=path, dataset=True, partition_filter=partition_filter)
    except (wr.exceptions.NoFilesFound, OSError):
        return pd.DataFrame()


def gold_path(bucket: str, gold_prefix: str, table: str) -> str:
    return f"s3://{bucket}/{gold_prefix.strip('/')}/{table}/"
