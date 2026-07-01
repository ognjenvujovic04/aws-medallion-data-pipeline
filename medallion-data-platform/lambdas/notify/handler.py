"""Send an email via SES when a Lambda error alarm fires.

Triggered by SNS (CloudWatch Alarm → SNS Topic → this Lambda).
Only acts on ALARM state transitions — ignores OK / INSUFFICIENT_DATA.
"""

import json
import os

import boto3

_SES = boto3.client("ses")
SENDER = os.environ["SENDER_EMAIL"]
RECIPIENT = os.environ["RECIPIENT_EMAIL"]


def handler(event, context):
    for record in event["Records"]:
        message = json.loads(record["Sns"]["Message"])

        if message.get("NewStateValue") != "ALARM":
            continue

        alarm_name = message.get("AlarmName", "Unknown alarm")
        reason = message.get("NewStateReason", "")

        _SES.send_email(
            Source=SENDER,
            Destination={"ToAddresses": [RECIPIENT]},
            Message={
                "Subject": {"Data": f"[ALARM] Lambda failure: {alarm_name}"},
                "Body": {"Text": {"Data": f"Alarm: {alarm_name}\n\nReason: {reason}"}},
            },
        )
