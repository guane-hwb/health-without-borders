"""
Patient access ledger: who opened or changed which record, when, and how.

Audit finding: be-v2-lecturas-de-historias-sin-registro-de-acceso.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.phi_sanitizer import mask_id
from app.db.models import PatientAccessLog

logger = logging.getLogger(__name__)

SCAN = "scan"
SEARCH = "search"
SYNC = "sync"

#: Upper bound for one query of the ledger.
MAX_ACCESS_LOG_ROWS = 500


def record_patient_access(
    db: Session,
    *,
    patient_id: str,
    actor_id: str,
    organization_id: str,
    channel: str,
    guardian_factor: bool = False,
    reason: Optional[str] = None,
) -> None:
    """
    Append one access and commit.

    Callers write it BEFORE returning the record and let a failure propagate:
    an access that cannot be recorded is not served (fail closed).
    """
    db.add(
        PatientAccessLog(
            patient_id=patient_id,
            actor_id=actor_id,
            organization_id=organization_id,
            channel=channel,
            guardian_factor=guardian_factor,
            reason=reason,
        )
    )
    db.commit()
    logger.info(
        "Patient access recorded channel=%s actor_id=%s patient_ref=%s guardian_factor=%s",
        channel,
        actor_id,
        mask_id(patient_id),
        guardian_factor,
    )


def list_patient_access(
    db: Session,
    *,
    patient_id: str,
    actor_organization_id: Optional[str] = None,
    limit: int = MAX_ACCESS_LOG_ROWS,
) -> list[PatientAccessLog]:
    """
    Accesses to one patient, newest first.

    ``actor_organization_id`` restricts the result to accesses made on behalf
    of that organization (an org admin audits their own staff), using the
    organization recorded at the time of each access.
    """
    query = db.query(PatientAccessLog).filter(PatientAccessLog.patient_id == patient_id)
    if actor_organization_id is not None:
        query = query.filter(PatientAccessLog.organization_id == actor_organization_id)
    return (
        query.order_by(PatientAccessLog.accessed_at.desc(), PatientAccessLog.id)
        .limit(min(limit, MAX_ACCESS_LOG_ROWS))
        .all()
    )
