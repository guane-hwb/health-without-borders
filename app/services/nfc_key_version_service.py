"""
Storage and aggregation of NFC key version sightings.

The point of this data is a single operational question: can key version N be
retired without leaving chips unreadable offline? Nothing else depends on it,
so a lost sighting is a delayed decision, never a clinical failure.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.db.models import NfcKeyVersionObservation, User
from app.schemas.nfc_key_version import (
    NfcKeyVersionCount,
    NfcKeyVersionEntry,
    NfcKeyVersionUsageResponse,
)

logger = logging.getLogger(__name__)

#: Roles a client may report. Anything else is normalised to 'patient' rather
#: than rejected, so a future device role never drops a batch on the floor.
_KNOWN_ROLES = {"patient", "guardian"}


def store_key_version_observations(
    db: Session,
    entries: Iterable[NfcKeyVersionEntry],
    reporter: User,
) -> int:
    """
    Append every sighting in ``entries``. Returns how many rows were written.

    Deliberately append-only and not de-duplicated: the device already collapses
    repeat reads of one chip into a single pending row, and the gap between
    sightings here is what a retention period must be sized from. Collapsing
    them server-side would throw that away.
    """
    stored = 0
    for entry in entries:
        role = entry.device_role if entry.device_role in _KNOWN_ROLES else "patient"
        db.add(
            NfcKeyVersionObservation(
                device_uid=entry.device_uid,
                device_role=role,
                key_version=entry.key_version,
                had_header=entry.had_header,
                observed_at=entry.observed_at,
                reported_by=reporter.id,
                organization_id=reporter.organization_id,
            )
        )
        stored += 1

    db.commit()
    return stored


def summarize_key_version_usage(
    db: Session,
    window_days: int = 90,
) -> NfcKeyVersionUsageResponse:
    """
    Report which key versions are still in circulation.

    Each chip is attributed to the version of its **most recent** sighting,
    because that is the version it is on now: a chip seen on version 0 in March
    and version 1 in June has migrated, and counting it under both would
    overstate what a retirement would break.

    ``retirable_versions`` holds versions with no sighting inside the window.
    This is the interlock: it can block a retirement, it cannot authorise one.
    A version can be absent from telemetry simply because those patients have
    not come back.
    """
    rows = (
        db.query(NfcKeyVersionObservation)
        .order_by(NfcKeyVersionObservation.observed_at.asc())
        .all()
    )

    # device_uid -> (role, version, observed_at) of the latest sighting.
    latest: dict[str, tuple[str, int, datetime]] = {}
    for row in rows:
        latest[row.device_uid] = (
            row.device_role,
            row.key_version,
            row.observed_at,
        )

    counts: dict[tuple[int, str], dict] = {}
    for role, version, observed_at in latest.values():
        bucket = counts.setdefault(
            (version, role), {"devices": 0, "last_seen_at": None}
        )
        bucket["devices"] += 1
        current = bucket["last_seen_at"]
        if current is None or _as_utc(observed_at) > _as_utc(current):
            bucket["last_seen_at"] = observed_at

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    seen_recently = {
        version
        for (version, _role), bucket in counts.items()
        if bucket["last_seen_at"] is not None
        and _as_utc(bucket["last_seen_at"]) >= cutoff
    }
    all_versions = {version for version, _role in counts}

    return NfcKeyVersionUsageResponse(
        window_days=window_days,
        counts=[
            NfcKeyVersionCount(
                key_version=version,
                device_role=role,
                devices=bucket["devices"],
                last_seen_at=bucket["last_seen_at"],
            )
            for (version, role), bucket in sorted(counts.items())
        ],
        retirable_versions=sorted(all_versions - seen_recently),
    )


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
