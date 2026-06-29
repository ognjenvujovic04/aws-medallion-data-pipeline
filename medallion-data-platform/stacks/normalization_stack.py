from pathlib import Path

from aws_cdk import (
    Duration,
    Stack,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

AWS_SDK_PANDAS_LAYER_ACCOUNT = "336392948345"
AWS_SDK_PANDAS_LAYER_VERSION = 29


class NormalizationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_lake: s3.IBucket,
        bronze_prefix: str = "bronze/hackernews",
        twitter_prefix: str = "bronze/twitter",
        silver_prefix: str = "silver",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        silver_lambdas = Path(__file__).resolve().parent.parent / "lambdas" / "silver"

        pandas_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "AwsSdkPandasLayer",
            (
                f"arn:aws:lambda:{self.region}:{AWS_SDK_PANDAS_LAYER_ACCOUNT}"
                f":layer:AWSSDKPandas-Python312:{AWS_SDK_PANDAS_LAYER_VERSION}"
            ),
        )

        hn_normalize_fn = lambda_.Function(
            self,
            "HackerNewsNormalize",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="normalize_hn.handler",
            code=lambda_.Code.from_asset(str(silver_lambdas)),
            layers=[pandas_layer],
            timeout=Duration.minutes(5),
            # awswrangler layer (~400MB at import) + 11k+ hits in pandas spikes
            # past 1GB and gets OOM-killed (Runtime.ExitError). 2048MB is still
            # trivially within free tier: ~30 GB-sec/day << 400,000 GB-sec/month.
            memory_size=2048,
            environment={
                "DATA_LAKE_BUCKET": data_lake.bucket_name,
                "BRONZE_PREFIX": bronze_prefix,
                "SILVER_PREFIX": silver_prefix,
            },
        )

        data_lake.grant_read(hn_normalize_fn, f"{bronze_prefix.strip('/')}/*")
        data_lake.grant_read_write(hn_normalize_fn, f"{silver_prefix.strip('/')}/*")

        twitter_normalize_fn = lambda_.Function(
            self,
            "TwitterNormalize",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="normalize_twitter.handler",
            code=lambda_.Code.from_asset(str(silver_lambdas)),
            layers=[pandas_layer],
            # Twitter dataset can be large; stream the CSV in chunks. 15 min /
            # 2048MB stays trivially within free tier for a one-time dataset.
            timeout=Duration.minutes(15),
            memory_size=2048,
            environment={
                "DATA_LAKE_BUCKET": data_lake.bucket_name,
                "TWITTER_PREFIX": twitter_prefix,
                "SILVER_PREFIX": silver_prefix,
            },
        )

        data_lake.grant_read(twitter_normalize_fn, f"{twitter_prefix.strip('/')}/*")
        data_lake.grant_read_write(twitter_normalize_fn, f"{silver_prefix.strip('/')}/*")

        self.hn_normalize_fn = hn_normalize_fn
        self.twitter_normalize_fn = twitter_normalize_fn
