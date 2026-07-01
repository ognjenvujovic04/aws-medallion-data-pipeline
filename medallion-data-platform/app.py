#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.storage_stack import StorageStack
from stacks.ingestion_stack import IngestionStack
from stacks.normalization_stack import NormalizationStack
from stacks.gold_stack import GoldStack
from stacks.serving_stack import ServingStack
from stacks.notification_stack import NotificationStack

app = cdk.App()
env = cdk.Environment(region="eu-central-1")

network = NetworkStack(app, "NetworkStack", env=env)
storage = StorageStack(app, "StorageStack", env=env)

ingestion = IngestionStack(
    app,
    "IngestionStack",
    data_lake=storage.data_lake,
    env=env,
)

normalization = NormalizationStack(
    app,
    "NormalizationStack",
    data_lake=storage.data_lake,
    vpc=network.vpc,
    lambda_sg=network.lambda_sg,
    env=env,
)

gold = GoldStack(
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
serving = None
if app.node.try_get_context("db_password"):
    serving = ServingStack(
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

# NotificationStack requires -c discord_webhook=<url>. Like ServingStack it is
# skipped when the flag is absent so the rest of the app still synths cleanly.
notification_email = app.node.try_get_context("notification_email")
if notification_email:
    monitored = {
        "HackerNewsIngest": ingestion.hacker_news_fn,
        "HackerNewsNormalize": normalization.hn_normalize_fn,
        "TwitterNormalize": normalization.twitter_normalize_fn,
        "ComputeMetrics": gold.compute_metrics_fn,
        "ComputeKpi": gold.compute_kpi_fn,
    }
    if serving:
        monitored["GoldToPostgres"] = serving.load_fn
    NotificationStack(
        app,
        "NotificationStack",
        monitored_functions=monitored,
        sender_email=notification_email,
        recipient_email=notification_email,
        env=env,
    )

app.synth()
