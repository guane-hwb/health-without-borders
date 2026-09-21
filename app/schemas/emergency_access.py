"""
Schemas for the break-glass (emergency access) audit sync endpoint.

The mobile app keeps a local ``emergency_access_log`` table and syncs its
pending rows to the central, append-only audit ledger. Field names are
snake_case to mirror both the app's local columns and the existing
``PatientSearchRequest`` body convention.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class EmergencyAccessEntry(BaseModel):
    """A single break-glass access event recorded on the device."""

    client_event_id: str = Field(
        ..., min_length=1,
        description="Client-generated UUID — idempotency/dedup key across retries",
    )
    patient_uid: str = Field(
        ..., min_length=1,
        description="Hardware UID of the record that was accessed",
    )
    patient_name: Optional[str] = Field(
        None, description="Patient name (decrypted by the client before sync)"
    )
    user_id: Optional[str] = Field(
        None, description="Acting user — the break-glass actor"
    )
    reason: str = Field(
        ..., min_length=1,
        description="Why the emergency access happened (e.g. guardian_absent_offline)",
    )
    occurred_at: str = Field(
        ..., min_length=1,
        description="ISO 8601 timestamp as recorded on the device",
    )


class EmergencyAccessSyncRequest(BaseModel):
    """Batch of pending emergency-access entries from one device."""

    entries: List[EmergencyAccessEntry] = Field(..., min_length=1)


class EmergencyAccessSyncResponse(BaseModel):
    """Result of an emergency-access sync — safe to treat as idempotent."""

    status: str
    received: int = Field(..., description="Entries in the request")
    stored: int = Field(..., description="Entries newly persisted")
    duplicates: int = Field(..., description="Entries already present, ignored")
