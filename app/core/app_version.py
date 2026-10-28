"""
Which app build a client runs, from the ``X-App-Version`` request header.

Security review finding N5: rotating or revoking an NFC key version is only
safe once every device runs a keyring-aware build, and nothing recorded which
build each device runs. The app sends ``X-App-Version: 1.2.3+45 (android)``
(build name, optional build number, optional platform), and login and refresh
log it with the account. Every device that is online refreshes at least once
an hour, so the logs show the fleet.

The header is client-controlled and only informative: it grants nothing, and
a value that does not match the format is logged as ``invalid`` rather than
copied into the log line.
"""

import re
from typing import Optional

from fastapi import Request

HEADER = "X-App-Version"

_FORMAT = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}(\+\d{1,9})?( \((android|ios|web)\))?")


def app_version(request: Request) -> str:
    """The client's ``X-App-Version``, ``none`` if absent, ``invalid`` if malformed."""
    raw: Optional[str] = request.headers.get(HEADER)
    if raw is None:
        return "none"
    value = raw.strip()
    return value if _FORMAT.fullmatch(value) else "invalid"
