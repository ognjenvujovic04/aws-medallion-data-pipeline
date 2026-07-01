"""Send a Discord notification when a Lambda error alarm fires.

Triggered by SNS (CloudWatch Alarm → SNS Topic → this Lambda).
Only acts on ALARM state transitions — ignores OK / INSUFFICIENT_DATA.
"""

import json
import os
import urllib.request

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# Discord red colour
_RED = 15158332


def handler(event, context):
    for record in event["Records"]:
        message = json.loads(record["Sns"]["Message"])

        if message.get("NewStateValue") != "ALARM":
            continue

        alarm_name = message.get("AlarmName", "Unknown alarm")
        reason = message.get("NewStateReason", "")

        payload = json.dumps({
            "embeds": [{
                "title": f":red_circle: Lambda failure: {alarm_name}",
                "description": reason,
                "color": _RED,
            }]
        }).encode()

        req = urllib.request.Request(
            WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
