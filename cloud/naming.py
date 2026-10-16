"""
Folder names, in one place (AD-8). Upload, processing, resync and import all
call this module, so they can never disagree about whose day a Folder is.

* A Person-Day is a folder name such as "Nguyen Van A & 2026-06-01". Its key
  is the NFC form, with the name and date read by main.parse_name_date (the
  PC's own parser) and the date checked against the calendar.
* An employee name is allowed when it matches a NAME_TO_ID key the way the
  Web App matches it: norm_() of the name equals a key or starts with it, and
  the first matching key wins (apps_script/WebAppSync.gs, matchId_).
* S3 keys: inbox/<uploadId>/<file>, work/<uploadId>/<file>.json and
  results/<Person-Day>.xlsx, with uploadId <UTC yyyymmddTHHMMSSZ>-<8 hex>.

Standard library only, apart from main (imported when a name is parsed).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

# JavaScript's \s and String.trim() set, which norm_() uses. Python's \s and
# str.strip() differ: they also match U+001C-U+001F and U+0085, and miss U+FEFF.
_JS_SPACE = "[\t\n\x0b\x0c\r    -     　﻿]+"
_COMBINING = re.compile("[̀-ͯ]")   # only this block, as in norm_()
_OUTPUT_STEM = re.compile(r"(?P<base>.+? & \d{4}-\d{1,2}-\d{1,2})(?:\((?P<dup>\d+)\))?")
_UPLOAD_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")


@dataclass(frozen=True)
class PersonDay:
    name: str   # NFC
    date: str   # yyyy-mm-dd, a real calendar date

    @property
    def key(self) -> str:
        return f"{self.name} & {self.date}"


def parse(folder_name: str) -> Optional[PersonDay]:
    """The Person-Day of a folder name, or None if it has no valid name and date."""
    from main import parse_name_date   # the PC's own parser, so both sides read names alike

    parsed = parse_name_date(unicodedata.normalize("NFC", folder_name))
    if parsed is None:
        return None
    name, date = parsed
    try:
        _dt.date.fromisoformat(date)
    except ValueError:   # 2026-02-30, 2026-13-01
        return None
    return PersonDay(name, date)


def parse_output_stem(stem: str) -> Optional[Tuple[PersonDay, int]]:
    """
    (Person-Day, copy number) of a PC output file name without ".xlsx":
    "<name> & <date>" is copy 0, "<name> & <date>(n)" is copy n. Anything else,
    including text after the date, is not a Day Sheet name.
    """
    m = _OUTPUT_STEM.fullmatch(unicodedata.normalize("NFC", stem))
    if not m:
        return None
    day = parse(m.group("base"))
    if day is None:
        return None
    return day, int(m.group("dup") or 0)


def norm(text) -> str:
    """norm_() from WebAppSync.gs: lower case, accents off, đ->d, spaces collapsed."""
    s = unicodedata.normalize("NFD", str(text or "").lower())
    s = _COMBINING.sub("", s).replace("đ", "d")
    return re.sub(_JS_SPACE, " ", s).strip(" ")


def matching_prefix(name: str, prefixes: Iterable[str]) -> Optional[str]:
    """The NAME_TO_ID key the Web App would pick for this name, or None."""
    k = norm(name)
    for key in prefixes:   # keys are compared as stored, like matchId_()
        if k == key or k.startswith(key):
            return key
    return None


def prefixes_from_json(text: str) -> list[str]:
    """The /calls-log/name_prefixes value: a JSON array of strings, in order."""
    value = json.loads(text)
    if not isinstance(value, list) or not all(isinstance(k, str) and k for k in value):
        raise ValueError("name_prefixes must be a JSON array of non-empty strings")
    return value


def new_upload_id(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return f"{now.astimezone(_dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def is_upload_id(text: str) -> bool:
    return bool(_UPLOAD_ID.fullmatch(text))


def inbox_key(upload_id: str, file_name: str) -> str:
    return f"inbox/{upload_id}/{file_name}"


def work_key(upload_id: str, file_name: str) -> str:
    return f"work/{upload_id}/{file_name}.json"


def result_key(day: PersonDay) -> str:
    return f"results/{day.key}.xlsx"
