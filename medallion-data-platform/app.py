#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.storage_stack import StorageStack
from stacks.ingestion_stack import IngestionStack
from stacks.normalization_stack import NormalizationStack
from stacks.gold_stack import GoldStack
from stacks.serving_stack import ServingStack

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

# ServingStack needs deploy-time secrets (db_password/admin_ip/key_name) passed via
# CDK context. Only instantiate it when db_password is supplied so the other stacks
# still synth (e.g. `cdk ls`, `cdk synth GoldStack`) without those flags.
if app.node.try_get_context("db_password"):
    ServingStack(
        app,
        "ServingStack",
        data_lake=storage.data_lake,
        vpc=network.vpc,
        lambda_sg=network.lambda_sg,
        postgres_sg=network.postgres_sg,
        db_name=app.node.try_get_context("db_name") or "social_medias",
        db_user=app.node.try_get_context("db_user") or "superset",
        db_password=app.node.try_get_context("db_password"),
        admin_ip=app.node.try_get_context("admin_ip"),   # required: -c admin_ip=...
        key_name=app.node.try_get_context("key_name"),   # required: -c key_name=...
        env=env,
    )

app.synth()
