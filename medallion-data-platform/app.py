#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.storage_stack import StorageStack
from stacks.ingestion_stack import IngestionStack
from stacks.normalization_stack import NormalizationStack
from stacks.gold_stack import GoldStack

app = cdk.App()
env = cdk.Environment(region="eu-central-1")

network = NetworkStack(app, "NetworkStack", env=env)
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
    vpc=network.vpc,
    lambda_sg=network.lambda_sg,
    env=env,
)

GoldStack(
    app,
    "GoldStack",
    data_lake=storage.data_lake,
    vpc=network.vpc,
    lambda_sg=network.lambda_sg,
    env=env,
)

app.synth()
