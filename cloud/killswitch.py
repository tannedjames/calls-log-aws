"""
The kill switch (Story 3.7, AD-11). A guardrail alarm (infra/guardrails.tf)
invokes this function when the runner crashes in a loop, a message reaches
the dead-letter queue, or both Run slots stay busy for too long. It turns the
runner's queue trigger off, so no new Run starts, and emails the Owner. Runs
already going finish; queued Folders wait in the queue (kept 14 days).

The upload page's alarms (calls-log-intake-*, Epic 4) instead set the
page's reserved concurrency to 0: the public URL then answers 429 and costs
nothing. Uploads already in S3 and queued Folders are not affected.

Turning either back on is a runbook step (docs/TECHNICAL.md, Runbook), never
automatic: Terraform ignores the trigger's enabled flag and the page's
reserved concurrency, so a deploy doesn't turn them back on either.

Reads only the CALLS_LOG_* environment: no parameter, no secret, no data.
The clients are passed in, so tests can fake Lambda and SNS.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from cloud import settings as settings_mod

log = logging.getLogger("calls-log.killswitch")

INTAKE_ALARMS = "calls-log-intake-"   # the upload page's alarms: they stop the page, not the runner
# Trigger states in which the runner can still be started (or soon will be).
_RUNNING = ("Enabled", "Enabling", "Updating", "Creating")
_REASON_LIMIT = 500


def alarm_of(event: Dict[str, Any]) -> Dict[str, str]:
    """Name, state and reason of the CloudWatch alarm that invoked the function."""
    data = event.get("alarmData") or {}
    state = data.get("state") or {}
    return {"name": str(data.get("alarmName") or event.get("alarmArn") or "unknown alarm"),
            "state": str(state.get("value") or ""),
            "reason": str(state.get("reason") or "")[:_REASON_LIMIT]}


def stop_runner(lambda_client: Any, function_name: str) -> List[Dict[str, str]]:
    """
    Turn off every queue trigger of the runner that can still start a Run.
    Returns one {"uuid", "was"} per trigger turned off; one already off is left alone.
    """
    stopped: List[Dict[str, str]] = []
    kwargs: Dict[str, Any] = {"FunctionName": function_name}
    while True:
        page = lambda_client.list_event_source_mappings(**kwargs)
        for mapping in page.get("EventSourceMappings", []):
            if mapping.get("State") in _RUNNING:
                lambda_client.update_event_source_mapping(UUID=mapping["UUID"], Enabled=False)
                stopped.append({"uuid": mapping["UUID"], "was": mapping["State"]})
        if not page.get("NextMarker"):
            return stopped
        kwargs["Marker"] = page["NextMarker"]


def stop_intake(lambda_client: Any, function_name: str) -> int:
    """Throttle the upload page to nothing; returns the reserved concurrency it had (-1: none)."""
    try:
        before = lambda_client.get_function_concurrency(FunctionName=function_name)
        was = int(before.get("ReservedConcurrentExecutions", -1))
    except Exception:   # reading is only for the email; the stop below is what matters
        was = -1
    lambda_client.put_function_concurrency(FunctionName=function_name, ReservedConcurrentExecutions=0)
    return was


def _email(sns: Any, topic: str, subject: str, lines: List[str]) -> None:
    if not topic:
        log.error("no alert topic is configured; the Owner is not emailed")
        return
    sns.publish(TopicArn=topic, Subject=subject, Message="\n".join(lines))


def respond(alarm: Dict[str, str], current: settings_mod.Settings,
            lambda_client: Any, sns: Any) -> Dict[str, Any]:
    """Stop the runner for an alarm in ALARM state and tell the Owner what happened."""
    if alarm["state"] != "ALARM":   # alarm actions fire on ALARM only; anything else is ignored
        log.info(json.dumps({"killSwitch": "ignored", "alarm": alarm["name"], "state": alarm["state"]}))
        return {"stopped": [], "ignored": True}
    if alarm["name"].startswith(INTAKE_ALARMS):
        return _respond_intake(alarm, current, lambda_client, sns)
    if not current.runner_function:
        raise RuntimeError("CALLS_LOG_RUNNER_FUNCTION is not set")

    why = [f"The guardrail alarm {alarm['name']} fired.", f"Reason: {alarm['reason'] or 'not given'}", ""]
    runbook = ("Find the cause first, then turn the trigger back on: docs/TECHNICAL.md, "
               "Runbook, \"Turning the runner back on\".")
    try:
        stopped = stop_runner(lambda_client, current.runner_function)
    except Exception as e:
        log.error(json.dumps({"killSwitch": "failed", "alarm": alarm["name"], "error": type(e).__name__}))
        _email(sns, current.alerts_topic_arn, "calls-log: the kill switch could NOT stop the runner", why + [
            f"Turning the runner's queue trigger off failed ({type(e).__name__}). Lambda retries "
            "this twice; if no \"runner is stopped\" email follows, turn it off by hand:",
            "docs/TECHNICAL.md, Runbook, \"Stopping the runner by hand\"."])
        raise

    log.info(json.dumps({"killSwitch": "stopped", "alarm": alarm["name"], "triggers": stopped}))
    if stopped:
        done = (f"The kill switch turned the runner's queue trigger off ({len(stopped)} trigger(s)). "
                "No new Run starts. Runs already going finish, and queued Folders wait in the "
                "queue for up to 14 days.")
    else:
        done = "The runner's queue trigger was already off, so nothing changed. No new Run starts."
    _email(sns, current.alerts_topic_arn, "calls-log: the runner is stopped (kill switch)",
           why + [done, "", runbook])
    return {"stopped": stopped}


def _respond_intake(alarm: Dict[str, str], current: settings_mod.Settings,
                    lambda_client: Any, sns: Any) -> Dict[str, Any]:
    if not current.intake_function:
        raise RuntimeError("CALLS_LOG_INTAKE_FUNCTION is not set")
    why = [f"The guardrail alarm {alarm['name']} fired.", f"Reason: {alarm['reason'] or 'not given'}", ""]
    try:
        was = stop_intake(lambda_client, current.intake_function)
    except Exception as e:
        log.error(json.dumps({"killSwitch": "failed", "alarm": alarm["name"], "error": type(e).__name__}))
        _email(sns, current.alerts_topic_arn, "calls-log: the kill switch could NOT stop the upload page", why + [
            f"Setting the upload page's reserved concurrency to 0 failed ({type(e).__name__}). Lambda "
            "retries this twice; if no \"upload page is off\" email follows, turn it off by hand:",
            "docs/TECHNICAL.md, Runbook, \"Stopping the upload page by hand\"."])
        raise
    log.info(json.dumps({"killSwitch": "stopped", "alarm": alarm["name"], "intakeWas": was}))
    _email(sns, current.alerts_topic_arn, "calls-log: the upload page is off (kill switch)", why + [
        "The upload page got far more requests than people make, so the kill switch set its reserved "
        "concurrency to 0: the page now answers 429 and costs nothing. Uploads already made and queued "
        "Folders carry on.", "",
        "Find the cause first (someone has the link?), then turn it back on: docs/TECHNICAL.md, "
        "Runbook, \"Turning the upload page back on\"."])
    return {"stopped": [], "intake": {"function": current.intake_function, "was": was}}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logging.getLogger().setLevel(logging.INFO)
    import boto3   # in the runner image; not needed on the PC

    return respond(alarm_of(event), settings_mod.from_environment(),
                   boto3.client("lambda"), boto3.client("sns"))
