from pathlib import Path

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_sns as sns,
)
from constructs import Construct


class NotificationStack(Stack):
    """Email notifications via SES for Lambda failures.

    Creates one CloudWatch Alarm per monitored function (Errors >= 1 in a
    1-minute window). All alarms publish to a single SNS topic; the
    EmailNotify Lambda subscribes to that topic and sends an email via SES.

    Pass monitored_functions as a dict of human-readable label -> IFunction so
    alarm names are predictable (e.g. "HackerNewsIngest Errors").

    Requires -c notification_email=<email> at deploy time.
    Both sender and recipient are set to the same address.
    The address must be verified in SES before deploying.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        monitored_functions: dict,
        sender_email: str,
        recipient_email: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        topic = sns.Topic(self, "LambdaFailureTopic", display_name="Lambda Failures")

        notify_fn = lambda_.Function(
            self,
            "EmailNotify",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(Path(__file__).resolve().parent.parent / "lambdas" / "notify")
            ),
            timeout=Duration.seconds(30),
            environment={
                "SENDER_EMAIL": sender_email,
                "RECIPIENT_EMAIL": recipient_email,
            },
        )

        notify_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail"],
                resources=["*"],
            )
        )

        notify_fn.add_event_source(lambda_events.SnsEventSource(topic))

        for label, fn in monitored_functions.items():
            alarm = cw.Alarm(
                self,
                f"{label}ErrorAlarm",
                alarm_name=f"{label} Errors",
                metric=fn.metric_errors(period=Duration.minutes(1)),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"{label} threw at least one error",
            )
            alarm.add_alarm_action(cw_actions.SnsAction(topic))


        self.topic = topic
