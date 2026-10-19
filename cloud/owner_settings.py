"""
The settings the upload page's Owner tab may change (Story 4.5 part B), and
the rules a new value must pass before it is written.

Only the five names in WRITABLE: the AWS Sync Switch, the two password
hashes, the alert address and the employee list. Never webapp_url or
sync_secret: whoever could write those could send every Day Sheet, and the
key to every Sheet, somewhere else. The bootstrap boundary and the intake's
policy name the same five (infra/bootstrap/main.tf, infra/intake.tf).
"""

from __future__ import annotations

import re
from typing import Any, List

from cloud import naming

WRITABLE = ("sync_enabled", "intake_passphrase_hash", "owner_passphrase_hash", "alert_email", "name_prefixes")
MAX_EMPLOYEES = 200
MAX_NAME = 100
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")   # the same check as tools/put_parameters.py
MAX_EMAIL = 254


class Invalid(Exception):
    """A value the Owner tab can't save: the reason in plain words, with a code the page translates."""

    def __init__(self, message: str, code: str, **args: Any):
        super().__init__(message)
        self.code, self.args_ = code, args


def employees(lines: Any) -> List[str]:
    """
    The employee list as the Web App keys it: one name per line, in order,
    each folded like norm_() (lower case, accents off, spaces collapsed).
    Refused: an empty list, a blank line, a duplicate, and a name that an
    earlier one would already match (the first key wins, so the later name
    could never be reached).
    """
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise Invalid("The employee list must be names, one per line.", "employees_invalid")
    if not lines:
        raise Invalid("The employee list is empty.", "employees_empty")
    if len(lines) > MAX_EMPLOYEES:
        raise Invalid(f"At most {MAX_EMPLOYEES} names.", "employees_many", max=MAX_EMPLOYEES)
    keys: List[str] = []
    for number, line in enumerate(lines, 1):
        key = naming.norm(line)
        if not key:
            raise Invalid(f"Line {number} is empty.", "employee_blank", line=number)
        if len(key) > MAX_NAME:
            raise Invalid(f"Line {number} is longer than {MAX_NAME} characters.", "employee_long",
                          line=number, max=MAX_NAME)
        if key in keys:
            raise Invalid(f"{key} is listed twice.", "employee_twice", name=key)
        first = naming.matching_prefix(key, keys)
        if first is not None:
            raise Invalid(f"{first} comes first and also matches {key}, so {key} would never be reached; "
                          "put the longer name first.", "prefix_clash", first=first, later=key)
        keys.append(key)
    return keys


def email(address: Any) -> str:
    """An address AWS can send the alerts to."""
    text = address.strip() if isinstance(address, str) else ""
    if not EMAIL.fullmatch(text) or len(text) > MAX_EMAIL:
        raise Invalid("That isn't an email address.", "bad_email")
    return text
