from aws_cdk import Stack, aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "MedallionVpc",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)],
        )

        self.lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSG",
            vpc=self.vpc,
            description="Private-subnet Lambdas: Normalize, Gold, Load-to-Postgres",
            allow_all_outbound=False,
        )
        self.lambda_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS to S3 via gateway endpoint",
        )

        self.postgres_sg = ec2.SecurityGroup(
            self,
            "PostgresSG",
            vpc=self.vpc,
            description="EC2 Postgres/Superset - inbound 5432 from LambdaSG only",
            allow_all_outbound=True,
        )

        self.lambda_sg.connections.allow_to(
            self.postgres_sg,
            ec2.Port.tcp(5432),
            "Postgres on EC2",
        )

        self.hn_ingest_sg = ec2.SecurityGroup(
            self,
            "HnIngestSG",
            vpc=self.vpc,
            description="HN Ingestion Lambda - outbound HTTPS to HN/Algolia API and S3",
            allow_all_outbound=False,
        )
        self.hn_ingest_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS to HN/Algolia API and S3",
        )
