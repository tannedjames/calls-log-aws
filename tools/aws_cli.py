"""
AWS from the Owner's PC through the AWS CLI (signed in with `aws login`), so
the tools need no boto3. Standard library only.

    Client("dynamodb").update_item(TableName=..., Key=..., ...)
        runs `aws dynamodb update-item --cli-input-json file://...` and
        returns its JSON output, like a boto3 client would; a failure raises
        AwsError, whose .response has botocore's shape, so cloud/status.py
        tells a refused condition from any other error the same way.

Input goes through a private temporary JSON file, never the command line,
and is written as ASCII (non-ASCII as \\u escapes), so the CLI reads names
the same whatever the Windows code page. Output is read as UTF-8.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

REGION = "ap-southeast-2"
_CODE = re.compile(r"An error occurred \(([\w.]+)\)")

Runner = Callable[..., Any]


class AwsError(Exception):
    """The AWS CLI failed. .response["Error"]["Code"] is AWS's error code, or "CliError"."""

    def __init__(self, what: str, code: str, message: str):
        super().__init__(f"{what} failed ({code}): {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


def _hidden(text: str, hide: Iterable[str]) -> str:
    for value in hide:
        if value:
            text = text.replace(value, "***")
    return text


def run_cli(args: list[str], payload: Optional[Dict[str, Any]] = None, *, run: Runner = subprocess.run,
            hide: Iterable[str] = ()) -> str:
    """`aws <args>` in the project's region; payload, if any, as --cli-input-json. Returns stdout."""
    folder = tempfile.mkdtemp(prefix="calls-log-")
    try:
        cmd = ["aws", *args, "--region", REGION]
        if payload is not None:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(payload), encoding="ascii")
            os.chmod(path, 0o600)
            cmd += ["--cli-input-json", f"file://{path}"]
        result = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if result.returncode != 0:
        err = _hidden((result.stderr or "").strip(), hide)
        m = _CODE.search(err)
        raise AwsError("aws " + " ".join(args[:2]), m.group(1) if m else "CliError", err[:500])
    return result.stdout or ""


def call(service: str, operation: str, payload: Optional[Dict[str, Any]] = None, *,
         run: Runner = subprocess.run, hide: Iterable[str] = ()) -> Dict[str, Any]:
    """One API call; the CLI's JSON output as a dict ({} when it prints nothing)."""
    out = run_cli([service, operation, "--output", "json"], payload, run=run, hide=hide)
    return json.loads(out) if out.strip() else {}


class Client:
    """A boto3-like client: client.put_item(**kwargs) calls `aws <service> put-item`."""

    def __init__(self, service: str, run: Runner = subprocess.run):
        self.service, self.run = service, run

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        operation = name.replace("_", "-")
        return lambda **kwargs: call(self.service, operation, kwargs, run=self.run)


def account_id(*, run: Runner = subprocess.run) -> str:
    return call("sts", "get-caller-identity", run=run)["Account"]
