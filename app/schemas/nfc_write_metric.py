"""
NFC write-size telemetry: what the app reports after writing a chip, and the
summary an administrator reads from it.

Sizes and counts only. No field carries a chip UID, a patient or clinical
content, so none can be stored; a field outside the schema is ignored
(pydantic's default, as for the key-version sightings).
"""

from typing import List, Optional

from pydantic import AwareDatetime, BaseModel, Field


class NfcWriteMetricEntry(BaseModel):
    """One chip write: how big it was against the chip, nothing of what it said."""

    device_role: str = Field(
        "patient", description="'patient' or 'guardian' device"
    )
    payload_format: int = Field(
        ...,
        ge=1,
        le=255,
        description=(
            "Payload format written: 1 = CBOR + DEFLATE + AES-GCM (today's), "
            "2 = the v2 format under development"
        ),
    )
    key_version: int = Field(
        ..., ge=0, le=255, description="NFC key version the payload was encrypted with"
    )
    payload_bytes: int = Field(
        ..., ge=0, le=65535, description="Encrypted payload, in bytes"
    )
    message_bytes: int = Field(
        ...,
        ge=0,
        le=65535,
        description="Whole NDEF message written, in bytes; never less than the payload",
    )
    chip_capacity: int = Field(
        ..., ge=0, le=65535, description="NDEF capacity the chip reported, in bytes"
    )
    consultations_kept: Optional[int] = Field(
        None,
        ge=0,
        le=1000,
        description="Guardian copy only: consultations written to the chip",
    )
    consultations_total: Optional[int] = Field(
        None,
        ge=0,
        le=1000,
        description="Guardian copy only: consultations in the record it was built from",
    )
    vaccines_kept: Optional[int] = Field(
        None,
        ge=0,
        le=1000,
        description="Guardian copy only: vaccines written to the chip",
    )
    vaccines_total: Optional[int] = Field(
        None,
        ge=0,
        le=1000,
        description="Guardian copy only: vaccines in the record it was built from",
    )
    written_at: AwareDatetime = Field(
        ...,
        description=(
            "Client clock at write time. Must carry an offset: a naive value "
            "would be read as UTC and silently shift the write by the device's "
            "timezone."
        ),
    )


class NfcWriteMetricSyncRequest(BaseModel):
    # The app uploads every pending write at once; the cap only bounds a request.
    entries: List[NfcWriteMetricEntry] = Field(..., min_length=1, max_length=5000)


class NfcWriteMetricSyncResponse(BaseModel):
    received: int
    stored: int


class NfcWriteMetricGroup(BaseModel):
    """
    The writes of one kind: one device role, one payload format, one capacity.

    The median and the p90 are nearest-rank values of the sorted sizes, the
    element at index ``int(q * (n - 1))`` for q = 0.5 and 0.9: always a size
    some write really had. With an even number of writes the median is the
    lower of the two middle ones.
    """

    device_role: str
    payload_format: int
    chip_capacity: int = Field(..., description="NDEF capacity, in bytes")
    writes: int
    message_bytes_median: int = Field(..., description="In bytes")
    message_bytes_p90: int = Field(..., description="In bytes")
    message_bytes_max: int = Field(..., description="In bytes")
    utilization_median_pct: Optional[float] = Field(
        None,
        description=(
            "Median of message_bytes / chip_capacity × 100, to one decimal; "
            "null when the chips reported a capacity of 0"
        ),
    )
    trimmed_writes: int = Field(
        ...,
        description=(
            "Writes that kept fewer consultations or fewer vaccines than the "
            "record held"
        ),
    )
    consultations_dropped: int = Field(
        ..., description="Sum of consultations_total − consultations_kept"
    )
    vaccines_dropped: int = Field(
        ..., description="Sum of vaccines_total − vaccines_kept"
    )


class NfcWriteMetricSummaryResponse(BaseModel):
    """
    How big NFC writes are against the chips they go to, for sizing the
    payload format.

    Writes count by ``written_at`` (the device's clock), so a brigade that
    syncs a week late still lands its writes on the days they happened. Groups
    are sorted by device role, payload format and chip capacity.
    """

    window_days: int
    groups: List[NfcWriteMetricGroup]
