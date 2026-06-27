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
            memory_size=1024,
            environment={
                "DATA_LAKE_BUCKET": data_lake.bucket_name,
                "BRONZE_PREFIX": bronze_prefix,
                "SILVER_PREFIX": silver_prefix,
            },
        )

        data_lake.grant_read(hn_normalize_fn, f"{bronze_prefix.strip('/')}/*")
        data_lake.grant_read_write(hn_normalize_fn, f"{silver_prefix.strip('/')}/*")

        self.hn_normalize_fn = hn_normalize_fn
