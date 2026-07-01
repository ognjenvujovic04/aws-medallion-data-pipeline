from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_assets as s3_assets,
)
from constructs import Construct

# Same AWS SDK for pandas layer used by GoldStack; pg8000 is bundled in it.
AWS_SDK_PANDAS_LAYER_ACCOUNT = "336392948345"
AWS_SDK_PANDAS_LAYER_VERSION = 29


class ServingStack(Stack):
    """Serving layer: a t3.micro EC2 host running Postgres + Superset in Docker.

    The host attaches to the existing ``postgres_sg`` (already reachable on 5432
    from ``lambda_sg``). This stack only *adds* human-access ingress (8088 Superset,
    22 SSH) scoped to ``admin_ip`` — the Lambda->Postgres:5432 path is untouched.

    The EC2 user-data pre-creates /opt/serving/{docker-compose.yml, schema.sql, .env}
    (the contract in ec2/user_data.sh) from the tracked ec2/ assets and the CDK
    context credentials, then runs the provisioning script (swap, Docker, compose up).

    The GoldToPostgres load Lambda is added to this same stack (commit 3) so it can
    read the instance's private IP directly as an env token, with no cross-stack export.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_lake: s3.IBucket,
        vpc: ec2.IVpc,
        lambda_sg: ec2.ISecurityGroup,
        postgres_sg: ec2.ISecurityGroup,
        db_name: str,
        db_user: str,
        db_password: str,
        admin_ip: str,
        key_name: str,
        gold_prefix: str = "gold",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not db_password:
            raise ValueError("db_password context is required (-c db_password=...)")
        if not admin_ip:
            raise ValueError("admin_ip context is required (-c admin_ip=...)")
        if not key_name:
            raise ValueError("key_name context is required (-c key_name=...)")

        ec2_assets = Path(__file__).resolve().parent.parent / "ec2"

        # --- human-access ingress: the only networking change (on the shared SG) ---
        # 5432-from-lambda_sg already exists in NetworkStack and is left untouched.
        admin_peer = ec2.Peer.ipv4(f"{admin_ip}/32")
        postgres_sg.add_ingress_rule(
            admin_peer, ec2.Port.tcp(8088), "Superset UI (admin IP)"
        )
        postgres_sg.add_ingress_rule(
            admin_peer, ec2.Port.tcp(22), "SSH (admin IP)"
        )

        # --- user-data: stage /opt/serving assets, then run the provisioning script ---
        # The runtime files (compose, schema, bootstrap) are shipped as S3 assets and
        # pulled down at boot rather than heredoc-inlined: inlining all of them (the
        # 15 KB bootstrap alone) blows the 16 KB EC2 user-data limit. Only the tiny
        # .env is written inline — it holds the db_password/secret key, which must not
        # land in the (world-of-account-readable) CDK assets bucket.
        compose_asset = s3_assets.Asset(
            self, "ComposeAsset", path=str(ec2_assets / "docker-compose.yml")
        )
        schema_asset = s3_assets.Asset(
            self, "SchemaAsset", path=str(ec2_assets / "schema.sql")
        )
        # superset-init compose service mounts this to build the dashboards by code.
        bootstrap_asset = s3_assets.Asset(
            self, "BootstrapAsset", path=str(ec2_assets / "superset_bootstrap.py")
        )
        # Reuse SUPERSET_SECRET_KEY = db_password for this teaching deployment (kept
        # out of git; passed via CDK context only). Superset admin == the db creds.
        provision = _lf((ec2_assets / "user_data.sh").read_text(encoding="utf-8"))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "mkdir -p /opt/serving",
            # AL2023 ships the AWS CLI; the host is in a public subnet so S3 is reachable.
            f"aws s3 cp {compose_asset.s3_object_url} /opt/serving/docker-compose.yml",
            f"aws s3 cp {schema_asset.s3_object_url} /opt/serving/schema.sql",
            f"aws s3 cp {bootstrap_asset.s3_object_url} /opt/serving/superset_bootstrap.py",
            _heredoc(
                "/opt/serving/.env",
                "\n".join(
                    [
                        f"DB_NAME={db_name}",
                        f"DB_USER={db_user}",
                        f"DB_PASSWORD={db_password}",
                        f"SUPERSET_SECRET_KEY={db_password}",
                        f"SUPERSET_ADMIN_USER={db_user}",
                        f"SUPERSET_ADMIN_PASSWORD={db_password}",
                    ]
                ),
            ),
            "chmod 600 /opt/serving/.env",
            # provisioning body (swap, Docker, compose up); drop its shebang line.
            _strip_shebang(provision),
        )

        instance = ec2.Instance(
            self,
            "ServingHost",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=postgres_sg,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "ServingKeyPair", key_name),
            associate_public_ip_address=True,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )

        # Let the instance role read the staged assets it pulls in user-data (scoped
        # to just these objects in the CDK assets bucket — least privilege).
        compose_asset.grant_read(instance.role)
        schema_asset.grant_read(instance.role)
        bootstrap_asset.grant_read(instance.role)

        # --- stable public address (Elastic IP) ---
        eip = ec2.CfnEIP(self, "ServingEip", domain="vpc")
        ec2.CfnEIPAssociation(
            self,
            "ServingEipAssoc",
            allocation_id=eip.attr_allocation_id,
            instance_id=instance.instance_id,
        )

        self.instance = instance

        # --- GoldToPostgres load Lambda (task H) ---
        # Mirrors the GoldStack lambda shape: private-isolated subnet behind lambda_sg,
        # reusing the pandas layer. Reaches the EC2 private IP on 5432; upserts gold
        # parquet into Postgres. Credentials via env vars (no Secrets Manager reach).
        load_lambdas = Path(__file__).resolve().parent.parent / "lambdas" / "load_pg"

        pandas_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "AwsSdkPandasLayer",
            (
                f"arn:aws:lambda:{self.region}:{AWS_SDK_PANDAS_LAYER_ACCOUNT}"
                f":layer:AWSSDKPandas-Python312:{AWS_SDK_PANDAS_LAYER_VERSION}"
            ),
        )

        load_fn = lambda_.Function(
            self,
            "GoldToPostgres",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="load_to_postgres.handler",
            code=lambda_.Code.from_asset(str(load_lambdas)),
            layers=[pandas_layer],
            timeout=Duration.minutes(10),
            memory_size=1024,
            environment={
                "DATA_LAKE_BUCKET": data_lake.bucket_name,
                "GOLD_PREFIX": gold_prefix,
                "DB_HOST": instance.instance_private_ip,
                "DB_PORT": "5432",
                "DB_NAME": db_name,
                "DB_USER": db_user,
                "DB_PASSWORD": db_password,
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[lambda_sg],
        )
        data_lake.grant_read(load_fn, f"{gold_prefix.strip('/')}/*")

        self.load_fn = load_fn

        CfnOutput(self, "ServingPublicIp", value=eip.ref, description="Elastic IP")
        CfnOutput(
            self,
            "SupersetUrl",
            value=f"http://{eip.ref}:8088",
            description="Superset UI",
        )


def _lf(text: str) -> str:
    """Normalize to LF so heredocs stay valid if the asset was checked out CRLF."""
    return text.replace("\r\n", "\n")


def _heredoc(path: str, body: str) -> str:
    """Write ``body`` to ``path`` via a quoted heredoc (no shell expansion)."""
    return f"cat > {path} <<'SERVING_EOF'\n{body}\nSERVING_EOF"


def _strip_shebang(script: str) -> str:
    lines = script.split("\n")
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    return "\n".join(lines)
