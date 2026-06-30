#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.storage_stack import StorageStack
from stacks.ingestion_stack import IngestionStack
from stacks.normalization_stack import NormalizationStack
from stacks.gold_stack import GoldStack

app = cdk.App()
env = cdk.Environment(region="eu-central-1")

storage = StorageStack(app, "StorageStack", env=env)

IngestionStack(
    app,
    "IngestionStack",
    data_lake=storage.data_lake,
    env=env,
)

NormalizationStack(
    app,
    "NormalizationStack",
    data_lake=storage.data_lake,
    env=env,
)

GoldStack(
    app,
    "GoldStack",
    data_lake=storage.data_lake,
    env=env,
)

app.synth()
