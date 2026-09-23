"""
The database-backed NFC keyring.

Which versions exist, which one new writes use, and which have been revoked —
all of it lives in the database so that revoking a leaked key is an API call
instead of a redeploy. The key material itself is wrapped by the KEK and is
useless without it.

When ``NFC_KEK`` is not configured this module stays out of the way entirely
and the ring is served from the environment, exactly as before. No existing
deployment changes behaviour on upgrade.
"""

import logging
import threading
import time
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import NFC_MAX_KEY_VERSION, settings
from app.core.key_wrapper import (
    EnvKekWrapper,
    KeyWrapper,
    KeyWrapperError,
    generate_key,
)
from app.db.models import NfcKey, NfcKeyEvent, NfcKeyringState

logger = logging.getLogger(__name__)

#: How long an assembled ring is reused before being read again.
#:
#: Login, refresh and ``/users/me`` are the most frequent authenticated calls in
#: the system; without this each would unwrap every key on every request. Sixty
#: seconds also bounds how long a revocation takes to stop being served, which
#: is well inside the minute the access token already allows.
_CACHE_TTL_SECONDS = 60

_cache_lock = threading.Lock()
_cached_ring: Optional[dict] = None
_cached_at: float = 0.0


def kek_wrapper() -> Optional[KeyWrapper]:
    """The configured wrapper, or None when no KEK is set."""
    kek = settings.NFC_KEK.strip()
    if not kek:
        return None
    return EnvKekWrapper(kek)


def last_known_keyring() -> Optional[dict]:
    """
    The most recent ring assembled from the database, however old.

    Used only when reading the database fails: serving a ring that was correct
    a moment ago is safe, whereas falling back to the environment would hand
    out whatever NFC_MASTER_KEY holds — a version that may have been revoked
    precisely because it leaked.
    """
    with _cache_lock:
        return _cached_ring


def invalidate_cache() -> None:
    """Drop the cached ring so the next read reflects a change immediately."""
    global _cached_ring, _cached_at
    with _cache_lock:
        _cached_ring = None
        _cached_at = 0.0


def load_keyring(db: Session) -> Optional[dict]:
    """
    Assemble ``{"keys": {version: hex}, "current": version | None}``.

    Returns None when no KEK is configured, which tells the caller to fall back
    to the environment.

    A key that cannot be unwrapped is skipped rather than raising: one damaged
    row must not take NFC away from every device. If nothing unwraps, the
    caller sees an empty ring and the failure is loud in the logs.
    """
    wrapper = kek_wrapper()
    if wrapper is None:
        return None

    global _cached_ring, _cached_at
    with _cache_lock:
        if _cached_ring is not None and time.monotonic() - _cached_at < _CACHE_TTL_SECONDS:
            return _cached_ring

    _auto_rotate_in_its_own_session()

    rows = db.query(NfcKey).filter(NfcKey.status == "live").all()
    keys: dict[int, str] = {}
    for row in rows:
        try:
            keys[row.version] = wrapper.unwrap(row.wrapped_key, row.version).hex()
        except KeyWrapperError as exc:
            logger.error(
                "NFC key version %s could not be unwrapped (kek_id on row: %s); "
                "it will not be served. %s",
                row.version,
                row.kek_id,
                exc,
            )

    state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).first()
    current = state.current_version if state else None
    if current is not None and current not in keys:
        logger.warning(
            "NFC current key version %s is not live or not unwrappable; "
            "devices can read but not write.",
            current,
        )
        current = None

    ring = {"keys": keys, "current": current}
    with _cache_lock:
        _cached_ring = ring
        _cached_at = time.monotonic()
    return ring


def ensure_initialised(db: Session) -> None:
    """
    Seed the table on first use, importing the environment's version 0.

    Version 0 is the key every chip in the field was written with. Importing it
    means the ``hwb-nfc-master-key:latest`` mount stops being able to silently
    change what version 0 *is* — a new secret version there would otherwise make
    every existing chip unreadable.

    The trade is that the KEK then guards every version, version 0 included, so
    losing it costs offline reads across the whole fleet rather than only for
    rotated chips. That is a decision taken deliberately, and it is why the KEK
    backup procedure is a precondition for enabling this.
    """
    wrapper = kek_wrapper()
    if wrapper is None:
        return

    if db.query(NfcKey).count() > 0:
        return

    master = settings.NFC_MASTER_KEY.strip()
    if not master:
        logger.warning(
            "NFC_KEK is configured but there is no NFC_MASTER_KEY to import; "
            "the keyring starts empty and NFC is unavailable until a key is "
            "created."
        )
        return

    try:
        material = bytes.fromhex(master)
    except ValueError:
        logger.error("NFC_MASTER_KEY is not hexadecimal; not importing it.")
        return

    _insert_key(db, wrapper, version=0, material=material)
    db.add(NfcKeyringState(id=1, current_version=0))
    _log_event(db, action="imported", version=0, actor_id=None, reason="initial import")
    db.commit()
    invalidate_cache()
    logger.info("Imported NFC_MASTER_KEY into the keyring as version 0.")


def rotate_to_new_version(
    db: Session,
    actor_id: Optional[str],
    reason: str,
) -> Optional[int]:
    """
    Create a new key version and make it current.

    Older versions keep being delivered, so chips written under them stay
    readable offline; only new writes use the new version. Chips migrate as
    they are rewritten.

    Returns the new version, or None when another instance advanced the pointer
    first. That is not an error: two Cloud Run instances reaching the same
    conclusion at the same time must end with one new current version, and the
    request that lost the race should be none the wiser.
    """
    wrapper = kek_wrapper()
    if wrapper is None:
        raise ValueError(
            "The keyring is served from the environment; configure NFC_KEK to "
            "manage key versions at runtime."
        )

    state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).first()
    if state is None:
        raise ValueError("The keyring has not been initialised yet.")

    previous = state.current_version
    new_version = _next_version(db)
    if new_version > NFC_MAX_KEY_VERSION:
        raise ValueError(
            f"Key version {NFC_MAX_KEY_VERSION} is the highest the one-byte payload header can "
            "carry. Retire old versions before rotating again."
        )

    _insert_key(db, wrapper, version=new_version, material=generate_key())
    _log_event(
        db, action="generated", version=new_version, actor_id=actor_id, reason=reason
    )

    advanced = (
        db.query(NfcKeyringState)
        .filter(
            NfcKeyringState.id == 1,
            NfcKeyringState.current_version == previous,
        )
        .update(
            {"current_version": new_version, "updated_at": _now()},
            synchronize_session=False,
        )
    )
    if not advanced:
        db.rollback()
        logger.info(
            "Another instance rotated the NFC keyring first; nothing to do."
        )
        return None

    _log_event(
        db,
        action="rotated",
        version=new_version,
        actor_id=actor_id,
        reason=f"current advanced from {previous}: {reason}",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(
            "Another instance rotated the NFC keyring first; nothing to do."
        )
        return None

    invalidate_cache()
    logger.info(
        "NFC keyring rotated: version %s is now current (was %s). %s",
        new_version,
        previous,
        reason,
    )
    return new_version


def _auto_rotate_in_its_own_session() -> None:
    """
    Run the rotation check on a session of its own.

    This is reached from the login and refresh paths, and rotation commits. On
    the request's session that commit would also persist whatever the endpoint
    had pending — the refresh endpoint, for one, has an unflushed token
    revocation at that point. Rotation is rare and login is not: it borrows a
    session rather than sharing one.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        maybe_auto_rotate(db)
    finally:
        db.close()


def maybe_auto_rotate(db: Session) -> Optional[int]:
    """
    Rotate when the current key is older than the configured period.

    Runs on the request path rather than on a schedule: the project has no
    scheduler, and an adopting organisation should not have to stand one up for
    key rotation to happen. The check only runs when the assembled ring is not
    cached, so at most once a minute per instance.

    Does nothing unless ``NFC_AUTO_ROTATE`` is on. That flag is the fleet gate:
    advancing the current version breaks reads on any device still running a
    build that predates the keyring, so it has to be a deliberate act by
    someone who has confirmed the fleet.

    Never raises. A failed rotation leaves the current key in place and is
    retried on the next uncached read; taking down login because a rotation
    could not be written would be a far worse trade.
    """
    if not settings.NFC_AUTO_ROTATE:
        return None

    try:
        state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).first()
        if state is None:
            return None

        current = (
            db.query(NfcKey)
            .filter(NfcKey.version == state.current_version)
            .first()
        )
        if current is None or current.created_at is None:
            return None

        age = _now() - _as_utc(current.created_at)
        if age.days < settings.NFC_ROTATION_PERIOD_DAYS:
            return None

        return rotate_to_new_version(
            db,
            actor_id=None,
            reason=(
                f"automatic rotation: version {state.current_version} reached "
                f"{age.days} days"
            ),
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Automatic NFC key rotation failed; keeping the current version."
        )
        return None


def revoke_version(
    db: Session,
    version: int,
    actor_id: Optional[str],
    reason: str,
) -> dict:
    """
    Stop serving ``version`` and, when it was current, replace it.

    Revoking means the key stops being delivered at all — for reading as well as
    writing. Anything less is useless against a leak, since the leaked key is
    precisely the one that reads.

    Chips written under the revoked version become **online-only** until they
    are rewritten. No data is lost: the chip UID is unencrypted, resolves the
    patient through the backend, and the next save migrates the chip.

    Returns a summary of what changed.
    """
    wrapper = kek_wrapper()
    if wrapper is None:
        raise ValueError(
            "The keyring is served from the environment; configure NFC_KEK to "
            "manage key versions at runtime."
        )

    row = db.query(NfcKey).filter(NfcKey.version == version).first()
    if row is None:
        raise LookupError(f"Key version {version} does not exist.")
    if row.status == "revoked":
        raise ValueError(f"Key version {version} is already revoked.")

    state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).first()
    was_current = state is not None and state.current_version == version

    row.status = "revoked"
    row.revoked_at = _now()
    row.revoked_by = actor_id
    row.revoke_reason = reason
    _log_event(db, action="revoked", version=version, actor_id=actor_id, reason=reason)

    new_version: Optional[int] = None
    if was_current:
        # Something has to be current or nothing can be written at all.
        new_version = _next_version(db)
        if new_version > NFC_MAX_KEY_VERSION:
            # Same ceiling as rotation: a version the one-byte payload header
            # cannot carry would leave the whole fleet without a write key.
            db.rollback()
            raise ValueError(
                f"Key version {NFC_MAX_KEY_VERSION} is the highest the one-byte "
                "payload header can carry; the current version cannot be revoked "
                "and replaced. Plan a re-keying of the fleet."
            )
        _insert_key(db, wrapper, version=new_version, material=generate_key())
        _log_event(
            db,
            action="generated",
            version=new_version,
            actor_id=actor_id,
            reason=f"replaces revoked version {version}",
        )

        # Conditional advance: if another instance moved the pointer first, its
        # move stands and this one is a no-op rather than an error.
        advanced = (
            db.query(NfcKeyringState)
            .filter(
                NfcKeyringState.id == 1,
                NfcKeyringState.current_version == version,
            )
            .update(
                {"current_version": new_version, "updated_at": _now()},
                synchronize_session=False,
            )
        )
        if advanced:
            _log_event(
                db,
                action="rotated",
                version=new_version,
                actor_id=actor_id,
                reason=f"current advanced from {version}",
            )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(
            "Another instance changed the keyring at the same time; retry."
        )

    invalidate_cache()
    logger.warning(
        "NFC key version %s revoked by %s (%s); replacement: %s",
        version,
        actor_id,
        reason,
        new_version,
    )
    return {
        "revoked_version": version,
        "replacement_version": new_version,
        "was_current": was_current,
    }


def keyring_status(db: Session) -> dict:
    """
    Versions and their state, with no key material.

    With no KEK configured the tables may not even exist yet — they are created
    by ``scripts/create_tables.py``, not at startup — so this reports the
    environment's view without querying them. Otherwise merging this feature
    would break the endpoint on every deployment until someone ran the script.
    """
    if kek_wrapper() is None:
        env_ring = settings.nfc_keyring()
        return {
            "source": "environment",
            "current_version": (
                settings.NFC_CURRENT_KEY_VERSION if env_ring else None
            ),
            "versions": [],
        }

    rows = db.query(NfcKey).order_by(NfcKey.version.asc()).all()
    state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).first()
    return {
        "source": "database",
        "current_version": state.current_version if state else None,
        "versions": [
            {
                "version": r.version,
                "status": r.status,
                "kek_id": r.kek_id,
                "created_at": r.created_at,
                "revoked_at": r.revoked_at,
                "revoke_reason": r.revoke_reason,
            }
            for r in rows
        ],
    }


def _insert_key(
    db: Session, wrapper: KeyWrapper, version: int, material: bytes
) -> None:
    db.add(
        NfcKey(
            version=version,
            wrapped_key=wrapper.wrap(material, version),
            kek_id=wrapper.kek_id,
            status="live",
        )
    )


def _next_version(db: Session) -> int:
    highest = db.query(NfcKey).order_by(NfcKey.version.desc()).first()
    return (highest.version + 1) if highest else 1


def _log_event(
    db: Session,
    action: str,
    version: int,
    actor_id: Optional[str],
    reason: Optional[str],
) -> None:
    db.add(
        NfcKeyEvent(
            action=action, version=version, actor_id=actor_id, reason=reason
        )
    )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _as_utc(value):
    """SQLite hands back naive datetimes; treat those as UTC."""
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
