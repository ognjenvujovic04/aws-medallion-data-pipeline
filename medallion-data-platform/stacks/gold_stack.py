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


class GoldStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_lake: s3.IBucket,
        silver_prefix: str = "silver",
        gold_prefix: str = "gold",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        gold_lambdas = Path(__file__).resolve().parent.parent / "lambdas" / "gold"

        pandas_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "AwsSdkPandasLayer",
            (
                f"arn:aws:lambda:{self.region}:{AWS_SDK_PANDAS_LAYER_ACCOUNT}"
                f":layer:AWSSDKPandas-Python312:{AWS_SDK_PANDAS_LAYER_VERSION}"
            ),
        )

        env = {
            "DATA_LAKE_BUCKET": data_lake.bucket_name,
            "SILVER_PREFIX": silver_prefix,
            "GOLD_PREFIX": gold_prefix,
        }

        compute_metrics_fn = lambda_.Function(
            self,
            "ComputeMetrics",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="compute_metrics.handler",
            code=lambda_.Code.from_asset(str(gold_lambdas)),
            layers=[pandas_layer],
            timeout=Duration.minutes(10),
            memory_size=1024,
            environment=env,
        )

        compute_kpi_fn = lambda_.Function(
            self,
            "ComputeKpi",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="compute_kpi.handler",
            code=lambda_.Code.from_asset(str(gold_lambdas)),
            layers=[pandas_layer],
            timeout=Duration.minutes(5),
            memory_size=1024,
            environment=env,
        )

        data_lake.grant_read(compute_metrics_fn, f"{silver_prefix.strip('/')}/*")
        data_lake.grant_read_write(compute_metrics_fn, f"{gold_prefix.strip('/')}/*")

        data_lake.grant_read(compute_kpi_fn, f"{silver_prefix.strip('/')}/*")
        data_lake.grant_read_write(compute_kpi_fn, f"{gold_prefix.strip('/')}/*")

        self.compute_metrics_fn = compute_metrics_fn
        self.compute_kpi_fn = compute_kpi_fn
