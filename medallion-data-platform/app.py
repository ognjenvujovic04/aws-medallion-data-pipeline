#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.storage_stack import StorageStack

app = cdk.App()
env = cdk.Environment(region="eu-central-1")

storage = StorageStack(app, "StorageStack", env=env)

app.synth()
