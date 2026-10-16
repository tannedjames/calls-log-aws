"""
Set the real values of the /calls-log/ parameters in AWS (Story 2.4).

Terraform creates the parameters with placeholders (infra/parameters.tf) and
never sees the real values; this tool sets them, out of band, from the
Owner's PC after `aws login`:

    python tools/put_parameters.py --from-private-files
        webapp_url and sync_secret from secrets_local.py, and name_prefixes
        (the NAME_TO_ID keys of apps_script/Employees.gs, in file order, as a
        JSON array)
    python tools/put_parameters.py --alert-email ADDRESS
        alert_email, and subscribes ADDRESS to the calls-log-alerts topic;
        AWS then emails ADDRESS a link to confirm
    python tools/put_parameters.py --sync-enabled true|false
        the AWS Sync Switch (AD-10)

Values go to the AWS CLI through a temporary file, never the command line,
and are never printed: the output names the parameters only.

Exit code 0 = done, 1 = the AWS CLI failed, 2 = bad usage or a private file
is missing (nothing is written then). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGION = "ap-southeast-2"
PREFIX = "/calls-log/"
TOPIC = "calls-log-alerts"


class CliError(Exception):
    """The AWS CLI failed; the message never contains a value."""


def read_private_files(secrets: Path, employees: Path) -> dict[str, str]:
    """webapp_url, sync_secret and name_prefixes from the PC's private files."""
    missing = [str(p) for p in (secrets, employees) if not p.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    text = secrets.read_text(encoding="utf-8")
    values = {}
    for param, var in (("webapp_url", "SHEETS_WEBAPP_URL"), ("sync_secret", "SHEETS_SYNC_SECRET")):
        m = re.search(rf'^{var}\s*=\s*["\']([^"\']+)["\']', text, re.M)
        if not m:
            raise ValueError(f"{var} not found in {secrets.name}")
        values[param] = m.group(1)
    block = re.search(r"var NAME_TO_ID\s*=\s*\{(.*?)\};", employees.read_text(encoding="utf-8"), re.S)
    keys = re.findall(r"'([^']+)'\s*:", block.group(1)) if block else []
    if not keys:
        raise ValueError(f"no NAME_TO_ID keys found in {employees.name}")
    # Order matters: the Web App matches by prefix and the first key wins.
    values["name_prefixes"] = json.dumps(list(dict.fromkeys(keys)), ensure_ascii=False)
    return values


def aws(args: list[str], payload: dict, *, secret: str = "", run=subprocess.run) -> str:
    """Run `aws <args> --cli-input-json file://...` with payload in a private temp file."""
    folder = tempfile.mkdtemp(prefix="calls-log-")
    path = Path(folder) / "input.json"
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(path, 0o600)
        cmd = ["aws", *args, "--cli-input-json", f"file://{path}", "--region", REGION, "--output", "text"]
        result = run(cmd, capture_output=True, text=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if secret:
            err = err.replace(secret, "***")
        raise CliError(f"aws {' '.join(args)} failed: {err}")
    return (result.stdout or "").strip()


def put_parameter(name: str, value: str, *, run=subprocess.run) -> None:
    aws(["ssm", "put-parameter"],
        {"Name": PREFIX + name, "Value": value, "Type": "SecureString", "Overwrite": True},
        secret=value, run=run)
    print(f"set {PREFIX}{name}")


def account_id(*, run=subprocess.run) -> str:
    result = run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                 capture_output=True, text=True)
    if result.returncode != 0:
        raise CliError(f"aws sts get-caller-identity failed: {(result.stderr or '').strip()}")
    return result.stdout.strip()


def subscribe(email: str, *, run=subprocess.run) -> None:
    topic = f"arn:aws:sns:{REGION}:{account_id(run=run)}:{TOPIC}"
    aws(["sns", "subscribe"],
        {"TopicArn": topic, "Protocol": "email", "Endpoint": email},
        secret=email, run=run)
    print(f"subscribed the alert address to {TOPIC}: confirm it from the email AWS sends")


def main(argv: list[str] | None = None, *, run=subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-private-files", action="store_true",
                    help="set webapp_url, sync_secret and name_prefixes from the private files")
    ap.add_argument("--secrets", type=Path, default=ROOT / "secrets_local.py")
    ap.add_argument("--employees", type=Path, default=ROOT / "apps_script" / "Employees.gs")
    ap.add_argument("--alert-email", metavar="ADDRESS")
    ap.add_argument("--sync-enabled", choices=["true", "false"])
    args = ap.parse_args(argv)

    if not (args.from_private_files or args.alert_email or args.sync_enabled):
        ap.print_usage(sys.stderr)
        print("nothing to set: give --from-private-files, --alert-email or --sync-enabled", file=sys.stderr)
        return 2
    if args.alert_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", args.alert_email):
        print("--alert-email doesn't look like an email address", file=sys.stderr)
        return 2

    updates: dict[str, str] = {}
    if args.from_private_files:
        try:
            updates.update(read_private_files(args.secrets, args.employees))
        except FileNotFoundError as e:
            print(f"private file missing, nothing written: {e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"{e}; nothing written", file=sys.stderr)
            return 2
    if args.alert_email:
        updates["alert_email"] = args.alert_email
    if args.sync_enabled:
        updates["sync_enabled"] = args.sync_enabled

    try:
        for name, value in updates.items():
            put_parameter(name, value, run=run)
            if name == "name_prefixes":   # a count lets the Owner check it without showing a name
                print(f"  {len(json.loads(value))} names, in Employees.gs order")
        if args.alert_email:
            subscribe(args.alert_email, run=run)
    except CliError as e:
        message = str(e)
        for value in updates.values():   # whatever the CLI echoed, no value is shown
            message = message.replace(value, "***")
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
