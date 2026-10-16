"""
The list of retired devices the app checks offline.

A retired patient or guardian device (lost, damaged or replaced) still holds
its data, and offline the app opens a record from the devices alone. Online,
`/scan` answers `410 device_retired`; offline, the app only knows a device was
retired if it has this list, which it downloads on every sync.

The list carries a hash of each UID, not the UID, so the phone does not keep a
readable list of every device ever retired. The hash is a pseudonym, not a
secret: the prefix is public and NFC UIDs are short, so a determined party can
recover a UID from its hash. What it hides is nothing more than which hardware
tags HWB stopped using; the list holds no patient identifier.

The hash must match the app's byte for byte. Both sides normalize the UID the
same way (`normalizeNfcUid` in the app: hex digits only, upper case), so
``04:a1:b2`` and ``04A1B2`` are one device. The shared test vectors are in
``tests/contract/fixtures/retired_device_hashes.json``.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import RetiredDeviceUid

RETIRED_DEVICE_HASH_PREFIX = "hwb-retired-device:v1:"

# `retired_at` is the time the sync's transaction started, not when it
# committed. A retirement committed just after a list was served can therefore
# carry a time before that list's `server_time`; reading back a margin before
# `since` keeps it from being skipped. The app stores hashes as a set, so the
# overlap only repeats a few of them.
SINCE_OVERLAP = timedelta(minutes=10)

_NOT_HEX = re.compile(r"[^0-9A-Fa-f]")


def normalize_device_uid(uid: Optional[str]) -> str:
    """The UID as the app compares it: hex digits only, upper case."""
    return _NOT_HEX.sub("", uid or "").upper()


def retired_device_hash(uid: Optional[str]) -> Optional[str]:
    """sha256 of the prefix plus the normalized UID; None when nothing is left."""
    normalized = normalize_device_uid(uid)
    if not normalized:
        return None
    return hashlib.sha256(
        (RETIRED_DEVICE_HASH_PREFIX + normalized).encode("ascii")
    ).hexdigest()


def list_retired_device_hashes(
    db: Session, since: Optional[datetime] = None
) -> tuple[list[str], datetime]:
    """
    Hashes of the devices retired after ``since`` (all of them without it),
    and the server time to pass back as ``since`` next time.
    """
    # Taken before the query: a retirement committed while it runs is newer
    # than this time, so the next call still returns it.
    server_time = datetime.now(timezone.utc)
    query = db.query(RetiredDeviceUid.device_uid).distinct()
    if since is not None:
        # A value without an offset is read as UTC. SQLite ignores the zone of
        # a bound value, so it has to be UTC already.
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        floor = since.astimezone(timezone.utc) - SINCE_OVERLAP
        query = query.filter(RetiredDeviceUid.retired_at > floor)
    hashes = {retired_device_hash(uid) for (uid,) in query.all()}
    hashes.discard(None)
    return sorted(hashes), server_time
