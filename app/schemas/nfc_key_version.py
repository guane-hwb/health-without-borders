from datetime import datetime
from typing import List, Optional

from pydantic import AwareDatetime, BaseModel, Field


class NfcKeyVersionEntry(BaseModel):
    """One sighting: a chip was read, and this is the version that opened it."""

    device_uid: str = Field(..., min_length=1, description="Hardware tag UID")
    device_role: str = Field(
        "patient", description="'patient' bracelet or 'guardian' card"
    )
    key_version: int = Field(..., ge=0, le=255, description="Version observed")
    had_header: bool = Field(
        False, description="Payload carried a version header (version >= 1)"
    )
    observed_at: AwareDatetime = Field(
        ...,
        description=(
            "Client clock at read time. Must carry an offset: a naive value "
            "would be read as UTC and silently shift the sighting by the "
            "device's timezone."
        ),
    )


class NfcKeyVersionSyncRequest(BaseModel):
    entries: List[NfcKeyVersionEntry] = Field(..., min_length=1)


class NfcKeyVersionSyncResponse(BaseModel):
    received: int
    stored: int


class NfcKeyVersionCount(BaseModel):
    """How many distinct chips of a given role were last seen on a version."""

    key_version: int
    device_role: str
    devices: int = Field(..., description="Distinct device UIDs")
    last_seen_at: Optional[datetime] = Field(
        None, description="Most recent sighting on this version"
    )


class NfcKeyVersionUsageResponse(BaseModel):
    """
    Key versions still in circulation, for deciding whether one can be retired.

    ``counts`` is by distinct device, not by sighting: a chip read twenty times
    counts once, and is attributed to the version of its **most recent**
    sighting, since that is the version it is on now.

    ``retirable_versions`` lists versions with no sighting inside
    ``window_days``. It is a veto, not a clearance: a version absent from the
    telemetry may still have chips in the field whose patients simply have not
    returned. Nothing here should be read as proof that retiring is safe — only
    that it is not obviously unsafe.
    """

    window_days: int
    counts: List[NfcKeyVersionCount]
    retirable_versions: List[int]
