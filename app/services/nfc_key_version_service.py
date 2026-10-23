"""
Storage and aggregation of NFC key version sightings.

The point of this data is a single operational question: can key version N be
retired without leaving chips unreadable offline? Nothing else depends on it,
so a lost sighting is a delayed decision, never a clinical failure.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import NfcKey, NfcKeyVersionObservation, User
from app.schemas.nfc_key_version import (
    NfcKeyVersionCount,
    NfcKeyVersionEntry,
    NfcKeyVersionUsageResponse,
)

logger = logging.getLogger(__name__)

#: Roles a client may report. Anything else is normalised to 'patient' rather
#: than rejected, so a future device role never drops a batch on the floor.
_KNOWN_ROLES = {"patient", "guardian"}

#: Domain prefix, so this reference never equals an HMAC made for another use.
_DEVICE_REF_PREFIX = "hwb-nfc-sighting:v1:"


def device_ref(uid: str) -> str:
    """
    What the table stores instead of a chip UID: HMAC-SHA256, keyed with
    ``SECRET_KEY``, of the UID as the app compares it (hex digits, upper case).

    The same chip always gives the same reference, which is all the usage
    report needs (distinct chips), but the UID cannot be read back. Changing
    ``SECRET_KEY`` changes every reference: chips seen before and after count
    twice until the old sightings leave the window.
    """
    from app.services.retired_device_service import normalize_device_uid

    normalized = normalize_device_uid(uid) or uid.strip().upper()
    return hmac.new(
        settings.SECRET_KEY.encode(),
        (_DEVICE_REF_PREFIX + normalized).encode(),
        hashlib.sha256,
    ).hexdigest()


def _known_key_versions(db: Session) -> set[int]:
    """
    Versions that exist, live or revoked. A chip can only be on one of them; a
    revoked one still matters, since its chips are what a retirement strands.
    """
    from app.services.nfc_key_service import kek_wrapper

    if kek_wrapper() is not None:
        return {version for (version,) in db.query(NfcKey.version).all()}
    return set(settings.nfc_keyring())


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

    A sighting of a version that does not exist is dropped, not stored: no chip
    can be on it, and storing it would let anyone holding an account fill the
    report with versions no key was ever made for (security review N11). The
    batch is still accepted, so a device does not retry it forever. Sightings
    older than the retention period are deleted on the way.
    """
    known = _known_key_versions(db)
    stored = 0
    unknown = 0
    for entry in entries:
        if entry.key_version not in known:
            unknown += 1
            continue
        role = entry.device_role if entry.device_role in _KNOWN_ROLES else "patient"
        db.add(
            NfcKeyVersionObservation(
                device_ref=device_ref(entry.device_uid),
                device_role=role,
                key_version=entry.key_version,
                had_header=entry.had_header,
                observed_at=entry.observed_at,
                reported_by=reporter.id,
                organization_id=reporter.organization_id,
            )
        )
        stored += 1

    if unknown:
        logger.warning(
            "Dropped %d key-version sightings of versions that do not exist "
            "reporter_id=%s",
            unknown,
            reporter.id,
        )

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.NFC_KEY_SIGHTING_RETENTION_DAYS
    )
    db.query(NfcKeyVersionObservation).filter(
        NfcKeyVersionObservation.reported_at < cutoff
    ).delete(synchronize_session=False)

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

    The veto looks at **every** sighting, not only each chip's latest. Counted
    by latest sighting, made-up reports of chips "seen on the current version"
    would hide the real sightings of the old one and clear its veto (security
    review N11); a real sighting can only be outweighed, never erased. A chip
    that has genuinely migrated keeps its old version vetoed until that
    sighting leaves the window, which errs on the safe side.
    """
    rows = (
        db.query(NfcKeyVersionObservation)
        # Ordered by the server's own clock, not the client's. "Latest
        # sighting wins" decides which version a chip is attributed to, and a
        # device with a skewed clock could otherwise make a stale sighting
        # outrank a newer one reported from another device.
        .order_by(NfcKeyVersionObservation.reported_at.asc())
        .all()
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    all_versions: set[int] = set()
    seen_recently: set[int] = set()
    # device_ref -> (role, version, observed_at) of the latest sighting.
    latest: dict[str, tuple[str, int, datetime]] = {}
    for row in rows:
        all_versions.add(row.key_version)
        if _as_utc(row.observed_at) >= cutoff:
            seen_recently.add(row.key_version)
        latest[row.device_ref] = (
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
