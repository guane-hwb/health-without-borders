"""
Startup preparation for the NFC keyring.

Kept out of ``main.py`` so it can be exercised directly: this code decides
whether the service is allowed to start, and a check that decides that should
not be reachable only by booting the whole application.
"""

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.nfc_key_service import ensure_initialised, load_keyring

logger = logging.getLogger(__name__)


def prepare_nfc_keyring(db: Session) -> None:
    """
    Seed the keyring and prove the configured KEK is the right one.

    Two things happen once, in order: the environment's version 0 is imported so
    the ring lives entirely in the database, and the current version is
    unwrapped. A wrong KEK would otherwise surface as devices quietly receiving
    an empty ring and NFC simply not working — far worse than refusing to start.

    Raises ``RuntimeError`` when the ring cannot be served. Does nothing at all
    when ``NFC_KEK`` is unset, which is how every deployment behaves until the
    KEK is deliberately enabled.
    """
    if not settings.NFC_KEK.strip():
        return

    # With a KEK the ring lives in the database; these variables are validated
    # but never served, which the rotation runbook for environment mode would
    # otherwise suggest they are.
    ignored = sorted(v for v in settings.nfc_keyring() if v != 0)
    if ignored or settings.NFC_CURRENT_KEY_VERSION != 0:
        logger.warning(
            "NFC_KEK is set: NFC_KEY_V%s and NFC_CURRENT_KEY_VERSION are ignored; "
            "manage versions with /patients/nfc-keys/rotate and /revoke.",
            ",".join(str(v) for v in ignored) or "<none>",
        )

    ensure_initialised(db)
    # load_keyring only returns None without a KEK, already excluded above.
    ring = load_keyring(db) or {"keys": {}, "current": None}

    if not ring["keys"]:
        raise RuntimeError(
            "NFC_KEK is configured but no key could be unwrapped. Either the "
            "KEK is not the one that wrapped these rows, or the keyring is "
            "empty. Devices would receive no NFC key."
        )

    if ring["current"] is None:
        raise RuntimeError(
            "The NFC keyring has live keys but no usable current version: "
            "devices could read but not write. Check nfc_keyring_state."
        )

    logger.info(
        "NFC keyring ready: versions %s, current %s.",
        sorted(ring["keys"]),
        ring["current"],
    )


def prepare_nfc_keyring_at_startup() -> None:
    """Open a session and run :func:`prepare_nfc_keyring`."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        prepare_nfc_keyring(db)
    finally:
        db.close()
