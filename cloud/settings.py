"""
Run-time settings for the AWS side (AD-9, AD-10).

The only reader of the CALLS_LOG_* environment variables, which Terraform sets
(resource names), and of the /calls-log/ SSM parameters (the secrets and
switches set out of band, infra/parameters.tf):

* load() reads both once per container and caches them for 15 minutes, so a
  rotated Web App secret or a new employee list reaches warm containers
  without a deploy (Story 5.4);
* sync_enabled() and lake_enabled() read the AWS Sync Switch and the data
  lake's switch fresh, once per Run, so the Owner can turn them on or off
  between Runs;
* configure_core() hands the pipeline core exactly three values: the Web App
  URL, the secret and the switch. Nothing else in config.py can be changed
  from here, so ocr_strict_engine, paddle_enable_mkldnn and
  post_9am_exception_limit stay as the PC has them.

Secrets, the Web App URL, employee names, the alert address and the lake's
person salt never appear in a repr or a log line. A parameter still holding Terraform's placeholder
("unset") counts as empty.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

from cloud import naming

log = logging.getLogger("calls-log.settings")

PARAMETER_PREFIX = "/calls-log/"
PLACEHOLDER = "unset"
SYNC_SWITCH = PARAMETER_PREFIX + "sync_enabled"
LAKE_SWITCH = PARAMETER_PREFIX + "lake_enabled"
_CACHED_PARAMETERS = ("webapp_url", "sync_secret", "name_prefixes", "intake_passphrase_hash",
                      "owner_passphrase_hash", "alert_email", "person_salt")
# Settings field -> environment variable (Terraform builds the function
# environment from one locals map with these names).
ENVIRONMENT = {
    "bucket": "CALLS_LOG_BUCKET",
    "table": "CALLS_LOG_TABLE",
    "queue_url": "CALLS_LOG_QUEUE_URL",
    "alerts_topic_arn": "CALLS_LOG_ALERTS_TOPIC_ARN",
    "runner_function": "CALLS_LOG_RUNNER_FUNCTION",   # the kill switch and the page's Owner tab
    "intake_function": "CALLS_LOG_INTAKE_FUNCTION",   # the kill switch's only
}


@dataclass(frozen=True)
class Settings:
    bucket: str = ""
    table: str = ""
    queue_url: str = ""
    alerts_topic_arn: str = ""
    runner_function: str = ""
    intake_function: str = ""
    # Private: kept out of repr, so a log line can't show them.
    webapp_url: str = field(default="", repr=False)
    sync_secret: str = field(default="", repr=False)
    name_prefixes: Tuple[str, ...] = field(default=(), repr=False)
    intake_passphrase_hash: str = field(default="", repr=False)
    owner_passphrase_hash: str = field(default="", repr=False)   # the page's Owner tab
    alert_email: str = field(default="", repr=False)
    person_salt: str = field(default="", repr=False)   # the data lake's person_key (cloud/lake.py)


CACHE_SECONDS = 900
_cache: Optional[Settings] = None
_cached_at = 0.0


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
        owner_passphrase_hash=_value(values.get("owner_passphrase_hash")),
        alert_email=_value(values.get("alert_email")),
        person_salt=_value(values.get("person_salt")),
    )


def from_environment(environ: Mapping[str, str] = os.environ) -> Settings:
    """Only the CALLS_LOG_* names, for a function that reads no parameter (the kill switch)."""
    return Settings(**{name: environ.get(var, "") for name, var in ENVIRONMENT.items()})


def load(ssm: Any = None, environ: Mapping[str, str] = os.environ, *, clock=time.monotonic) -> Settings:
    """The settings for this container: read, then cached for CACHE_SECONDS."""
    global _cache, _cached_at
    if _cache is None or clock() - _cached_at > CACHE_SECONDS:
        _cache = read(ssm or _ssm_client(), environ)
        _cached_at = clock()
        log.info("settings loaded: %r", _cache)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def _switch(name: str, ssm: Any = None) -> bool:
    response = (ssm or _ssm_client()).get_parameter(Name=name, WithDecryption=True)
    return response["Parameter"]["Value"].strip().lower() == "true"


def sync_enabled(ssm: Any = None) -> bool:
    """The AWS Sync Switch, read now. Only "true" turns it on; an error raises."""
    return _switch(SYNC_SWITCH, ssm)


def lake_enabled(ssm: Any = None) -> bool:
    """The data lake's switch (ADR 9), read now. Only "true" turns it on; an error raises."""
    return _switch(LAKE_SWITCH, ssm)


def configure_core(settings: Settings, *, sync_on: bool, config: Any = None) -> None:
    """Give the pipeline core the Web App URL, the secret and the switch for this Run."""
    if config is None:
        from config import CONFIG as config
    if settings.webapp_url:
        config.sheets_webapp_url = settings.webapp_url
    if settings.sync_secret:
        config.sheets_sync_secret = settings.sync_secret
    config.sheets_sync_enabled = sync_on
