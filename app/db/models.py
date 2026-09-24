import enum  # stdlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Organization(Base):
    """
    Represents a specific clinical entity (e.g., an NGO or Clinic).
    Serves as the root boundary for the Multi-Tenant architecture.
    """
    __tablename__ = "organizations"
    
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    users = relationship("User", back_populates="organization")
    patients = relationship("Patient", back_populates="organization")

class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    org_admin  = "org_admin"
    doctor     = "doctor"
    nurse      = "nurse"


class User(Base):
    """
    System users (Admins, Doctors, Nurses). 
    Strictly bound to an Organization.
    """
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    
    # Foreign Key: Binds user to an organization securely
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False) 
    
    full_name = Column(String, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    # Allowed roles: "superadmin", "org_admin", "doctor", "nurse"
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.doctor)
    is_active = Column(Boolean, default=True)
    token_version = Column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment=(
            "Stamped into every token as 'tv'. Incrementing it invalidates every "
            "token issued before (deactivation, revoke-sessions, refresh reuse)."
        ),
    )
    created_at = Column(
        DateTime(timezone=True), nullable=True,
        # No server default on purpose: rows that predate the column stay NULL
        # instead of getting the migration time, which would invalidate every
        # token issued before the deploy.
        default=lambda: datetime.now(timezone.utc),
        comment="Account creation time; NULL for accounts created before September 2026",
    )

    # Relationships
    organization = relationship("Organization", back_populates="users")


class Patient(Base):
    """
    Main Patient Entity.
    Stores relational columns for quick lookups and raw JSON for full clinical history.
    Implements Multi-Tenancy via organization_id.
    
    Relational columns mirror the most-queried RDA elements so the database 
    can filter without scanning JSON. The full_record_json column remains the 
    authoritative source for the complete patient payload.

    Server-generated PK. The frontend-generated ID is kept for correlation
    but the server controls the ID space with a per-org uniqueness constraint.
    """
    __tablename__ = "patients"

    # 1. SERVER-GENERATED PRIMARY KEY
    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
        comment="Server-generated UUID4 — the authoritative patient ID"
    )

    # Frontend-generated ID kept for sync correlation
    # Unique per organization so two orgs can independently register
    # the same frontend-generated ID without collision.
    frontend_patient_id = Column(
        String, nullable=False, index=True,
        comment="Frontend-generated UUID sent during first sync"
    )

    # Foreign Key: Binds patient to the organization that registered them
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    
    # 2. THE HARDWARE ID (NFC Bracelet / Barcode)
    device_uid = Column(String, unique=True, index=True, nullable=False) 

    # --- RDA Identification (Res. 866/2021 Elems. 2.1, 2.2) ---
    document_type = Column(String(5), index=True, nullable=True,
                           comment="Tipo de documento — CC, CE, PA, RC, TI, PT, etc.")
    document_number = Column(String, index=True, nullable=True,
                             comment="Número de documento de identidad del paciente")
    
    # --- Demographics ---
    first_name = Column(String, index=True)
    last_name = Column(String, index=True, comment="Primer apellido (Elem. 3.1)")
    second_last_name = Column(String, nullable=True, comment="Segundo apellido (Elem. 3.2)")
    birth_date = Column(Date)
    biological_sex = Column(String(2), nullable=True, comment="Sexo biológico M/F/I (Elem. 5)")
    blood_type = Column(String(5))

    # --- Nationality (Elems. 1.1, 1.2) — critical for migrant population ---
    nationality_code = Column(String(3), index=True, nullable=True,
                              comment="Código ISO 3166-1 alfa-3 del país de nacionalidad; "
                                      "el bundle FHIR lo convierte a numérico")

    # --- Guardian ---
    guardian_name = Column(String)
    guardian_phone = Column(String)
    guardian2_name = Column(String, nullable=True, comment="Nombre del segundo guardián (opcional)")
    guardian2_phone = Column(String, nullable=True, comment="Teléfono del segundo guardián (opcional)")
    
    # --- Raw Full JSON Storage (authoritative clinical payload) ---
    full_record_json = Column(JSON) 
    
    # --- FHIR Sync Tracking ---
    # Track synced encounters by UUID instead of count
    synced_encounter_ids = Column(
        JSON, default=list, nullable=False, server_default="[]",
        comment="List of encounterIdentifier UUIDs already sent to FHIR Store"
    )

    # Background data hash for RDA-Paciente delta detection
    background_data_hash = Column(
        String(64), nullable=True,
        comment="SHA-256 hash of background data fields (patientInfo, guardianInfo, allergies, etc.)"
    )

    rda_paciente_sent = Column(
        Boolean, default=False, nullable=False, server_default="false",
        comment="Whether the RDA-Paciente bundle has been sent at least once"
    )
    
    # Audit Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="patients")

    # Composite unique constraint — one frontend_patient_id per org
    __table_args__ = (
        UniqueConstraint(
            "frontend_patient_id", "organization_id",
            name="uq_patient_frontend_id_org",
        ),
        Index("ix_patient_org_frontend", "organization_id", "frontend_patient_id"),
    )

    def __repr__(self):
        return f"<Patient(id={self.id}, frontend_id={self.frontend_patient_id}, doc={self.document_type}-{self.document_number}, org={self.organization_id})>"


class NfcKey(Base):
    """
    An NFC key version, with its material wrapped by the KEK.

    Keys live here rather than in the environment so that creating and revoking
    a version is a runtime operation instead of a redeploy. Before this table,
    a leaked key kept working for months while a rotation was deployed twice
    and chips migrated on their own.

    ``wrapped_key`` is never usable on its own: it is AES-256-GCM sealed under
    the KEK, with the version number as associated data so a row cannot be
    moved to another version. ``kek_id`` records which KEK sealed it, so
    replacing the KEK later is a row-by-row re-wrap rather than guesswork.
    """

    __tablename__ = "nfc_keys"

    version = Column(
        Integer, primary_key=True,
        comment="Key version, stamped into the NFC payload header",
    )
    wrapped_key = Column(
        String, nullable=False,
        comment="Base64 of nonce+ciphertext+tag, sealed under the KEK",
    )
    kek_id = Column(
        String, nullable=False,
        comment="Fingerprint of the KEK that sealed this row — not the secret",
    )
    status = Column(
        String, nullable=False, server_default="live", index=True,
        comment="'live' (delivered to devices) or 'revoked' (never delivered)",
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(String, nullable=True)


class NfcKeyringState(Base):
    """
    Single row holding which key version new writes use.

    Kept apart from :class:`NfcKey` so advancing the pointer is one conditional
    update: two Cloud Run instances acting at the same time must end with one
    new version and one advance, not two.
    """

    __tablename__ = "nfc_keyring_state"

    id = Column(Integer, primary_key=True, default=1)
    current_version = Column(Integer, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NfcKeyEvent(Base):
    """
    Append-only log of what was done to the keyring, by whom and why.

    Covers all three operations, not only revocation: reconstructing an
    incident needs to know when a version appeared as much as when it stopped
    being served, and an adopting organisation needs it for its own compliance.
    """

    __tablename__ = "nfc_key_events"

    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    action = Column(
        String, nullable=False, index=True,
        comment="'generated', 'rotated', 'revoked' or 'imported'",
    )
    version = Column(Integer, nullable=False, index=True)
    actor_id = Column(String, ForeignKey("users.id"), nullable=True)
    reason = Column(String, nullable=True)
    at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NfcKeyVersionObservation(Base):
    """
    Append-only record of which NFC key version a chip was seen on.

    A chip carries no readable hint of which key encrypted it — the version is
    only known once a device decrypts it — so this is the only way to learn how
    far a key rotation has actually drained. Retiring a version makes every
    chip still on it unreadable offline, and without this table that count is
    unknowable.

    **Append-only on purpose.** The device keeps one row per chip (its current
    state); the server keeps every sighting. The gap between consecutive
    sightings of the same UID is what a retention period has to be sized from,
    and an upsert here would destroy exactly that.

    ``device_role`` separates bracelets from guardian cards. Both are written
    with the same keyring, and guardian cards are rewritten less often, so a
    version cannot be retired safely by counting bracelets alone.

    No patient identifier, no clinical data and no key material is stored here.
    """

    __tablename__ = "nfc_key_version_observations"

    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    device_uid = Column(
        String, index=True, nullable=False,
        comment="Hardware tag UID observed — not joined to any patient here",
    )
    device_role = Column(
        String, nullable=False, server_default="patient",
        comment="'patient' bracelet or 'guardian' card",
    )
    key_version = Column(
        Integer, index=True, nullable=False,
        comment="NFC key version that decrypted this chip",
    )
    had_header = Column(
        Boolean, nullable=False, server_default="false",
        comment="Whether the payload carried a version header (version >= 1)",
    )
    observed_at = Column(
        DateTime(timezone=True), nullable=False,
        comment="When the device read the chip (client clock)",
    )
    reported_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        comment="When the server received it — may lag by days for a brigade "
                "that was offline",
    )
    reported_by = Column(
        String, ForeignKey("users.id"), index=True, nullable=True,
        comment="User whose device reported the sighting",
    )
    organization_id = Column(
        String, ForeignKey("organizations.id"), index=True, nullable=True,
        comment="Retained for traceability only, never for access control",
    )


class RetiredDeviceUid(Base):
    """
    Append-only ledger of NFC device UIDs retired from a patient record.

    When a lost, damaged or replaced bracelet (patient) or card (guardian) is
    re-labeled, the record keeps a single UID per device (updated in place — no
    duplicate patient record is ever created). The previous UID is recorded here
    so that:

      - a later scan of the retired bracelet can be answered distinctly
        ("this tag was retired and no longer belongs to HWB") instead of the
        generic 404 that looks like a blank or unknown chip, and
      - there is an auditable trail of which UID belonged to which patient, in
        which role (patient bracelet vs guardian card), when it was retired, by
        whom, and why.

    Rows are never updated or deleted — one row per retirement event. ``device_uid``
    is intentionally NOT unique: it is a historical ledger, not a live binding.
    """
    __tablename__ = "retired_device_uids"

    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    device_uid = Column(
        String, index=True, nullable=False,
        comment="The retired hardware tag UID — no longer bound to any patient",
    )
    patient_id = Column(
        String, ForeignKey("patients.id"), index=True, nullable=False,
        comment="Patient whose record this UID was retired from",
    )
    reason = Column(
        String, nullable=False,
        comment="Why the UID was retired — 'lost', 'damaged' or 'replaced'",
    )
    device_role = Column(
        String, nullable=False, server_default="patient",
        comment="Which device was retired — 'patient' bracelet or 'guardian' card",
    )
    retired_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    retired_by = Column(
        String, nullable=True,
        comment="User id that performed the retirement, when known",
    )

    def __repr__(self):
        return (
            f"<RetiredDeviceUid(device_uid={self.device_uid}, "
            f"patient_id={self.patient_id}, role={self.device_role}, "
            f"reason={self.reason})>"
        )


class RevokedToken(Base):
    """
    Token revocation list.
    Stores the JTI (JWT ID) of tokens that have been explicitly revoked
    (e.g., via logout). Checked on every authenticated request.

    Entries can be cleaned up after their original expiry time passes
    (the token would be invalid anyway), keeping the table small.
    """
    __tablename__ = "revoked_tokens"

    jti = Column(
        String, primary_key=True,
        comment="JWT ID claim from the revoked token"
    )
    revoked_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at = Column(
        DateTime(timezone=True), nullable=False,
        comment="Original token expiry — safe to delete row after this time"
    )


class EmergencyAccessLog(Base):
    """
    Central, append-only audit ledger of break-glass (emergency) accesses to a
    minor's record made without the guardian's second factor present — the
    offline emergency path in the mobile app.

    The app records each access locally and syncs the pending entries here so
    there is a central trail. ``user_id`` is what the device declares;
    ``uploaded_by`` is the authenticated user whose session synced the entry.
    Online reads (/scan, /search) are recorded server-side in
    ``patient_access_log`` instead.

    Idempotency: the client generates a stable ``client_event_id`` (UUID) per
    entry. The local queue may retry, so re-sending the same entry is a no-op —
    the unique constraint on ``client_event_id`` de-duplicates server-side.

    Rows are never updated or deleted.
    """
    __tablename__ = "emergency_access_log"

    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_event_id = Column(
        String, unique=True, index=True, nullable=False,
        comment="Client-generated UUID — idempotency/dedup key across retries",
    )
    patient_uid = Column(
        String, index=True, nullable=False,
        comment="Hardware UID of the record that was accessed",
    )
    patient_name = Column(
        String, nullable=True,
        comment=(
            "No longer written (the UID identifies the record); kept for entries "
            "synced before September 2026"
        ),
    )
    user_id = Column(
        String, nullable=True,
        comment="Acting user reported by the client (the break-glass actor)",
    )
    uploaded_by = Column(
        String, ForeignKey("users.id"), nullable=True, index=True,
        comment=(
            "Authenticated user whose session synced the entry. user_id is what "
            "the device declares; this is what the server verified. Null only "
            "for entries synced before the column existed."
        ),
    )
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False,
        comment="Organization of the authenticated caller that synced the entry",
    )
    reason = Column(
        String, nullable=False,
        comment="Why the emergency access happened (e.g. guardian_absent_offline)",
    )
    occurred_at = Column(
        String, nullable=False,
        comment="Timestamp as reported by the client (ISO 8601; may be naive local time)",
    )
    received_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        comment="Server receipt time — the defensible audit timestamp",
    )

    def __repr__(self):
        return (
            f"<EmergencyAccessLog(client_event_id={self.client_event_id}, "
            f"patient_uid={self.patient_uid}, reason={self.reason})>"
        )


class PatientAccessLog(Base):
    """
    Append-only ledger of who opened or changed which patient record.

    Patients are global: any doctor, nurse or org admin can read any record,
    and /search returns a minor's record without the guardian's second factor.
    Every successful /scan, /search and /sync writes one row here, with the
    authenticated actor (never a client-declared one) and the server's clock,
    so "who saw this child's record, when, how" has an answer.

    Rows are never updated or deleted.
    """
    __tablename__ = "patient_access_log"

    id = Column(
        String, primary_key=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    patient_id = Column(
        String, ForeignKey("patients.id"), nullable=False, index=True,
        comment="Server id of the patient whose record was accessed",
    )
    actor_id = Column(
        String, ForeignKey("users.id"), nullable=False, index=True,
        comment="Authenticated user who made the request",
    )
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True,
        comment="Organization of the actor at the time of the access",
    )
    channel = Column(
        String, nullable=False,
        comment="'scan' (bracelet), 'search' (identity lookup) or 'sync' (write)",
    )
    guardian_factor = Column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="Whether the guardian's card was presented and matched",
    )
    reason = Column(
        String, nullable=True,
        comment="Reason given by the caller, when the channel accepts one",
    )
    accessed_at = Column(
        DateTime(timezone=True), nullable=False, index=True,
        # Set by the app (microseconds everywhere, so "newest first" is exact);
        # the database default covers rows written by anything else.
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="Server time of the access",
    )

    def __repr__(self):
        return (
            f"<PatientAccessLog(patient_id={self.patient_id}, "
            f"actor_id={self.actor_id}, channel={self.channel})>"
        )

