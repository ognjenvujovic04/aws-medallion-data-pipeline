from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
    aws_sns as sns,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct

# Transient errors worth retrying; genuine code failures (States.TaskFailed from an
# unhandled exception) are left to fail fast and surface via the failure alarm.
_RETRYABLE = [
    "Lambda.ServiceException",
    "Lambda.TooManyRequestsException",
    "Lambda.SdkClientException",
    "States.Timeout",
]


class StepFuncStack(Stack):
    """Daily pipeline orchestration.

    Chains the existing lambdas into one Step Functions state machine:

        HackerNewsIngest -> HackerNewsNormalize
          -> Parallel[ComputeMetrics, ComputeKpi]
          -> GoldToPostgres            (only when ServingStack is deployed)

    An EventBridge rule starts the machine daily (replacing the raw-collector
    schedule that used to live in IngestionStack). Each task carries a short
    exponential Retry for transient Lambda errors. On an unrecoverable failure the
    execution fails, which trips a CloudWatch alarm on the machine's ExecutionsFailed
    metric; that alarm publishes to the shared notification SNS topic (same
    alarm -> SNS -> SES email path the per-lambda alarms use), when one is provided.

    Lambdas default their target date to "yesterday" (UTC) when the input carries no
    ``date``. Every task discards its response and passes the input through unchanged,
    so a single ``{"date": "..."}`` (or empty input) drives the whole chain.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hn_ingest_fn: lambda_.IFunction,
        hn_normalize_fn: lambda_.IFunction,
        compute_metrics_fn: lambda_.IFunction,
        compute_kpi_fn: lambda_.IFunction,
        load_fn: lambda_.IFunction | None = None,
        failure_topic: sns.ITopic | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def task(state_id: str, fn: lambda_.IFunction) -> tasks.LambdaInvoke:
            t = tasks.LambdaInvoke(
                self,
                state_id,
                lambda_function=fn,
                payload_response_only=True,
                result_path=sfn.JsonPath.DISCARD,
            )
            t.add_retry(
                errors=_RETRYABLE,
                interval=Duration.seconds(5),
                max_attempts=2,
                backoff_rate=2.0,
            )
            return t

        ingest = task("HackerNewsIngest", hn_ingest_fn)
        normalize = task("HackerNewsNormalize", hn_normalize_fn)

        gold = (
            sfn.Parallel(self, "Gold", result_path=sfn.JsonPath.DISCARD)
            .branch(task("ComputeMetrics", compute_metrics_fn))
            .branch(task("ComputeKpi", compute_kpi_fn))
        )
        gold.add_retry(
            errors=_RETRYABLE,
            interval=Duration.seconds(5),
            max_attempts=2,
            backoff_rate=2.0,
        )

        definition = ingest.next(normalize).next(gold)
        if load_fn is not None:
            definition = definition.next(task("GoldToPostgres", load_fn))

        state_machine = sfn.StateMachine(
            self,
            "Pipeline",
            state_machine_name="MedallionDailyPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(1),
        )
        self.state_machine = state_machine

        # --- daily trigger (moved here from IngestionStack) ---
        events.Rule(
            self,
            "DailySchedule",
            schedule=events.Schedule.cron(minute="0", hour="1"),
            targets=[targets.SfnStateMachine(state_machine)],
        )

        # --- pipeline-level failure alarm -> shared notification topic ---
        if failure_topic is not None:
            alarm = cw.Alarm(
                self,
                "PipelineFailedAlarm",
                alarm_name="MedallionDailyPipeline Failed",
                metric=state_machine.metric_failed(period=Duration.minutes(1)),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description="The daily medallion pipeline execution failed",
            )
            alarm.add_alarm_action(cw_actions.SnsAction(failure_topic))
