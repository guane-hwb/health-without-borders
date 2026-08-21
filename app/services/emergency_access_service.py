"""
Persistence for break-glass (emergency access) audit entries.

Writes are append-only and idempotent: the client-generated ``client_event_id``
is the dedup key, so a retried batch stores each entry at most once. Each entry
is written inside its own SAVEPOINT, so a rare concurrent-insert race on one
entry cannot roll back the rest of the batch.
"""

import logging
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.phi_sanitizer import mask_id
from app.db.models import EmergencyAccessLog
from app.schemas.emergency_access import EmergencyAccessEntry

logger = logging.getLogger(__name__)


def store_emergency_access_entries(
    db: Session,
    entries: Iterable[EmergencyAccessEntry],
    organization_id: str,
) -> tuple[int, int]:
    """
    Persist emergency-access entries idempotently.

    Returns ``(stored, duplicates)`` — the number of entries newly written and
    the number ignored because their ``client_event_id`` was already present.
    """
    stored = 0
    duplicates = 0

    for entry in entries:
        savepoint = db.begin_nested()
        try:
            already = (
                db.query(EmergencyAccessLog.id)
                .filter(
                    EmergencyAccessLog.client_event_id == entry.client_event_id
                )
                .first()
            )
            if already is not None:
                savepoint.rollback()
                duplicates += 1
                continue

            db.add(
                EmergencyAccessLog(
                    client_event_id=entry.client_event_id,
                    patient_uid=entry.patient_uid,
                    patient_name=entry.patient_name,
                    user_id=entry.user_id,
                    organization_id=organization_id,
                    reason=entry.reason,
                    occurred_at=entry.occurred_at,
                )
            )
            db.flush()
            savepoint.commit()
            stored += 1
        except IntegrityError:
            # Concurrent insert of the same client_event_id — treat as duplicate.
            savepoint.rollback()
            duplicates += 1

    db.commit()

    logger.info(
        "Emergency access sync org_id=%s stored=%d duplicates=%d",
        mask_id(organization_id),
        stored,
        duplicates,
    )
    return stored, duplicates
