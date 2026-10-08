"""
Patient access ledger: who opened or changed which record, when, and how.

Audit findings: be-v2-lecturas-de-historias-sin-registro-de-acceso;
(2026-10-02) be-oct26-bitacora-emergencia-sin-lectura (the emergency ledger was
written but nobody could read it).
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.phi_sanitizer import mask_id
from app.db.models import (
    EmergencyAccessLog,
    Patient,
    PatientAccessLog,
    RetiredDeviceUid,
)

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


def list_emergency_access(
    db: Session,
    *,
    patient: Patient,
    actor_organization_id: Optional[str] = None,
    limit: int = MAX_ACCESS_LOG_ROWS,
) -> list[EmergencyAccessLog]:
    """
    Break-glass openings of one patient's record, newest received first.

    The device records them offline by bracelet UID, so this takes the
    patient's current bracelet and every bracelet it retired. With
    ``actor_organization_id``, only entries synced by that organization's
    users (an org admin audits their own staff).
    """
    uids = {patient.device_uid} | {
        uid
        for (uid,) in db.query(RetiredDeviceUid.device_uid).filter(
            RetiredDeviceUid.patient_id == patient.id,
            RetiredDeviceUid.device_role == "patient",
        )
    }
    query = db.query(EmergencyAccessLog).filter(EmergencyAccessLog.patient_uid.in_(uids))
    if actor_organization_id is not None:
        query = query.filter(EmergencyAccessLog.organization_id == actor_organization_id)
    return (
        query.order_by(EmergencyAccessLog.received_at.desc(), EmergencyAccessLog.id)
        .limit(min(limit, MAX_ACCESS_LOG_ROWS))
        .all()
    )
