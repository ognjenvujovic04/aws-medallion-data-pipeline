from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_ec2 as ec2,
    aws_s3 as s3,
)
from constructs import Construct


class ServingStack(Stack):
    """Serving layer: a t3.medium EC2 host running Postgres + Superset in Docker.

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
        # Reuse SUPERSET_SECRET_KEY = db_password for this teaching deployment (kept
        # out of git; passed via CDK context only). Superset admin == the db creds.
        compose = _lf((ec2_assets / "docker-compose.yml").read_text(encoding="utf-8"))
        schema = _lf((ec2_assets / "schema.sql").read_text(encoding="utf-8"))
        provision = _lf((ec2_assets / "user_data.sh").read_text(encoding="utf-8"))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "mkdir -p /opt/serving",
            _heredoc("/opt/serving/docker-compose.yml", compose),
            _heredoc("/opt/serving/schema.sql", schema),
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
                ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM
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

        # --- stable public address (Elastic IP) ---
        eip = ec2.CfnEIP(self, "ServingEip", domain="vpc")
        ec2.CfnEIPAssociation(
            self,
            "ServingEipAssoc",
            allocation_id=eip.attr_allocation_id,
            instance_id=instance.instance_id,
        )

        self.instance = instance

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
