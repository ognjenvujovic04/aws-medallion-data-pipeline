from pathlib import Path

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_sns as sns,
)
from constructs import Construct


class NotificationStack(Stack):
    """Discord notifications for Lambda failures.

    Creates one CloudWatch Alarm per monitored function (Errors >= 1 in a
    1-minute window). All alarms publish to a single SNS topic; the
    DiscordNotify Lambda subscribes to that topic and POSTs to the webhook.

    Pass monitored_functions as a dict of human-readable label -> IFunction so
    alarm names are predictable (e.g. "HackerNewsIngest Errors").

    Requires -c discord_webhook=<webhook_url> at deploy time.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        monitored_functions: dict,
        discord_webhook_url: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        topic = sns.Topic(self, "LambdaFailureTopic", display_name="Lambda Failures")

        notify_fn = lambda_.Function(
            self,
            "DiscordNotify",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(Path(__file__).resolve().parent.parent / "lambdas" / "notify")
            ),
            timeout=Duration.seconds(30),
            environment={"DISCORD_WEBHOOK_URL": discord_webhook_url},
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
