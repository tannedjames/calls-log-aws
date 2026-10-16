"""
Story 5.4: which secret does the live Web App accept? Checked without writing
anything, from the Owner's PC, before and after rotating it:

    python tools/check_secret.py          # secrets_local.py's secret: must be ACCEPTED
    python tools/check_secret.py --old    # asks for the old secret: must be REFUSED

The probe is a sync for the made-up name "zz-secret-check", which matches no
employee. The Web App checks the secret first ("bad secret") and the name
second ("unknown name"), so the probe never reaches a spreadsheet either way.
The secret is never printed; the URL comes from config.py (secrets_local.py).

Exit code 0 = as expected, 1 = not as expected or unreachable, 2 = no Web App
URL or secret configured.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROBE_NAME = "zz-secret-check"


def probe(url: str, secret: str, *, opener=urllib.request.urlopen, timeout: float = 30) -> str:
    """"refused", "accepted", or "unexpected: <why>" for one secret."""
    body = json.dumps({"secret": secret, "name": PROBE_NAME, "date": "2000-01-01", "sessions": []}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(req, timeout=timeout) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"unexpected: the Web App didn't answer ({type(e).__name__})"
    error = str(answer.get("error") or "")
    if error == "bad secret":
        return "refused"
    if error.startswith("unknown name"):
        return "accepted"
    return "unexpected: " + ("it wrote something" if answer.get("ok") else "another error")


def main(argv: Optional[List[str]] = None, *, opener=urllib.request.urlopen, ask=getpass.getpass,
         config=None) -> int:
    ap = argparse.ArgumentParser(description="Which secret the live Web App accepts (calls-log, Story 5.4).")
    ap.add_argument("--old", action="store_true", help="ask for the old secret; it must be refused")
    args = ap.parse_args(argv)
    if config is None:
        from config import CONFIG as config
    if not config.sheets_webapp_url:
        print("no Web App URL configured (secrets_local.py)", file=sys.stderr)
        return 2
    if args.old:
        secret, want = ask("Old secret (not shown): "), "refused"
    else:
        secret, want = config.sheets_sync_secret, "accepted"
    if not secret:
        print("no secret to check", file=sys.stderr)
        return 2
    got = probe(config.sheets_webapp_url, secret, opener=opener)
    which = "the old secret" if args.old else "secrets_local.py's secret"
    print(f"{which}: {got.upper() if got in ('accepted', 'refused') else got} (expected {want.upper()})")
    return 0 if got == want else 1


if __name__ == "__main__":
    sys.exit(main())
