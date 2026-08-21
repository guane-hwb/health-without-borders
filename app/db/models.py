import enum  # stdlib
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
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
    there is a tamper-evident central trail. It is the natural compensating
    control for /search access to minors' records (which by design does not
    require the guardian second factor): it leaves a record of who accessed
    what, when, and why.

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
        comment="Patient name as reported by the client (decrypted before sync)",
    )
    user_id = Column(
        String, nullable=True,
        comment="Acting user reported by the client (the break-glass actor)",
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
