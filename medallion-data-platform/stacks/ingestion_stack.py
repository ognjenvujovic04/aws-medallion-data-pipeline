from pathlib import Path

from aws_cdk import (
    Duration,
    Stack,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct


class IngestionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_lake: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambdas_root = Path(__file__).resolve().parent.parent / "lambdas"

        hacker_news_fn = lambda_.Function(
            self,
            "HackerNewsIngest",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(lambdas_root / "hn")),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "DATA_LAKE_BUCKET": data_lake.bucket_name,
                "BRONZE_PREFIX": "bronze/hackernews",
            },
        )

        data_lake.grant_put(hacker_news_fn, "bronze/hackernews/*")

        # Exposed so StepFuncStack can orchestrate this collector as the first
        # pipeline step. The daily schedule lives in StepFuncStack now (it starts
        # the whole state machine), not on this raw collector.
        self.hacker_news_fn = hacker_news_fn
