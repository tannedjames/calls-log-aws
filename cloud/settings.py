"""
Run-time settings for the AWS side (AD-9, AD-10).

The only reader of the CALLS_LOG_* environment variables, which Terraform sets
(resource names), and of the /calls-log/ SSM parameters (the secrets and
switches set out of band, infra/parameters.tf):

* load() reads both once per container and caches them;
* sync_enabled() reads the AWS Sync Switch fresh, once per Run, so the Owner
  can turn it on or off between Runs;
* configure_core() hands the pipeline core exactly three values: the Web App
  URL, the secret and the switch. Nothing else in config.py can be changed
  from here, so ocr_strict_engine, paddle_enable_mkldnn and
  post_9am_exception_limit stay as the PC has them.

Secrets, the Web App URL, employee names and the alert address never appear in
a repr or a log line. A parameter still holding Terraform's placeholder
("unset") counts as empty.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

from cloud import naming

log = logging.getLogger("calls-log.settings")

PARAMETER_PREFIX = "/calls-log/"
PLACEHOLDER = "unset"
SYNC_SWITCH = PARAMETER_PREFIX + "sync_enabled"
_CACHED_PARAMETERS = ("webapp_url", "sync_secret", "name_prefixes", "intake_passphrase_hash", "alert_email")
# Settings field -> environment variable (Terraform builds the function
# environment from one locals map with these names).
ENVIRONMENT = {
    "bucket": "CALLS_LOG_BUCKET",
    "table": "CALLS_LOG_TABLE",
    "queue_url": "CALLS_LOG_QUEUE_URL",
    "alerts_topic_arn": "CALLS_LOG_ALERTS_TOPIC_ARN",
}


@dataclass(frozen=True)
class Settings:
    bucket: str = ""
    table: str = ""
    queue_url: str = ""
    alerts_topic_arn: str = ""
    # Private: kept out of repr, so a log line can't show them.
    webapp_url: str = field(default="", repr=False)
    sync_secret: str = field(default="", repr=False)
    name_prefixes: Tuple[str, ...] = field(default=(), repr=False)
    intake_passphrase_hash: str = field(default="", repr=False)
    alert_email: str = field(default="", repr=False)


_cache: Optional[Settings] = None


def _ssm_client():
    import boto3   # in the runner image; not needed on the PC

    return boto3.client("ssm")


def _value(raw: Optional[str]) -> str:
    return "" if raw is None or raw.strip() == PLACEHOLDER else raw


def read(ssm: Any, environ: Mapping[str, str] = os.environ) -> Settings:
    """Read the environment and the cached parameters now (no cache)."""
    names = [PARAMETER_PREFIX + n for n in _CACHED_PARAMETERS]
    response = ssm.get_parameters(Names=names, WithDecryption=True)
    values = {p["Name"][len(PARAMETER_PREFIX):]: p["Value"] for p in response.get("Parameters", [])}
    missing = [n for n in response.get("InvalidParameters", [])]
    if missing:
        log.warning("SSM parameters missing, treated as empty: %s", ", ".join(missing))
    prefixes = _value(values.get("name_prefixes"))
    return Settings(
        **{name: environ.get(var, "") for name, var in ENVIRONMENT.items()},
        webapp_url=_value(values.get("webapp_url")),
        sync_secret=_value(values.get("sync_secret")),
        name_prefixes=tuple(naming.prefixes_from_json(prefixes)) if prefixes else (),
        intake_passphrase_hash=_value(values.get("intake_passphrase_hash")),
        alert_email=_value(values.get("alert_email")),
    )


def load(ssm: Any = None, environ: Mapping[str, str] = os.environ) -> Settings:
    """The settings for this container: read once, then cached."""
    global _cache
    if _cache is None:
        _cache = read(ssm or _ssm_client(), environ)
        log.info("settings loaded: %r", _cache)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def sync_enabled(ssm: Any = None) -> bool:
    """The AWS Sync Switch, read now. Only "true" turns it on; an error raises."""
    response = (ssm or _ssm_client()).get_parameter(Name=SYNC_SWITCH, WithDecryption=True)
    return response["Parameter"]["Value"].strip().lower() == "true"


def configure_core(settings: Settings, *, sync_on: bool, config: Any = None) -> None:
    """Give the pipeline core the Web App URL, the secret and the switch for this Run."""
    if config is None:
        from config import CONFIG as config
    if settings.webapp_url:
        config.sheets_webapp_url = settings.webapp_url
    if settings.sync_secret:
        config.sheets_sync_secret = settings.sync_secret
    config.sheets_sync_enabled = sync_on
