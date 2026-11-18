"""
Storage and aggregation of NFC write-size metrics.

The point of this data is one engineering question: how much room the NFC
payload has on the chips in the field, so the next payload format can be sized
from real writes rather than from a synthetic corpus. Nothing else depends on
it, so a lost metric is a less precise estimate, never a clinical failure.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence, TypeVar

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import NfcWriteMetric, User
from app.schemas.nfc_write_metric import (
    NfcWriteMetricEntry,
    NfcWriteMetricGroup,
    NfcWriteMetricSummaryResponse,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Roles a client may report. Anything else is normalised to 'patient' rather
#: than rejected, so a future device role never drops a batch on the floor.
_KNOWN_ROLES = {"patient", "guardian"}

#: Longest summary window, in days. Rows older than
#: ``NFC_WRITE_METRIC_RETENTION_DAYS`` are gone whatever the window.
MAX_WINDOW_DAYS = 730


def _inconsistency(entry: NfcWriteMetricEntry) -> Optional[str]:
    """
    Why ``entry`` contradicts itself, or None when it does not.

    Each check is something no real write can produce: a message is the
    payload plus its record, a chip refuses a message larger than its
    capacity, and a copy cannot keep more of the history than the record holds,
    nor report one count of a pair without the other.
    """
    if entry.message_bytes < entry.payload_bytes:
        return "message_below_payload"
    if entry.message_bytes > entry.chip_capacity:
        return "message_over_capacity"
    for kept, total in (
        (entry.consultations_kept, entry.consultations_total),
        (entry.vaccines_kept, entry.vaccines_total),
    ):
        if kept is None and total is None:
            continue
        if kept is None or total is None:
            return "count_without_pair"
        if kept > total:
            return "kept_over_total"
    return None


def store_write_metrics(
    db: Session,
    entries: Iterable[NfcWriteMetricEntry],
    reporter: User,
    app_version: str,
) -> int:
    """
    Append every consistent write in ``entries``. Returns how many rows were
    written.

    Deliberately append-only and not de-duplicated: each write is a sample of
    how big payloads are, and two writes of the same size are two samples.

    An entry that contradicts itself (see :func:`_inconsistency`) is dropped,
    not stored: it can only come from a measuring bug, and it would bend the
    sizes everyone reads. The batch is still accepted, so a device does not
    retry it forever, and the log says how many were dropped and why — counts,
    never the values. Metrics older than the retention period are deleted on
    the way.
    """
    stored = 0
    dropped: Counter[str] = Counter()
    for entry in entries:
        reason = _inconsistency(entry)
        if reason is not None:
            dropped[reason] += 1
            continue
        role = entry.device_role if entry.device_role in _KNOWN_ROLES else "patient"
        db.add(
            NfcWriteMetric(
                device_role=role,
                payload_format=entry.payload_format,
                key_version=entry.key_version,
                payload_bytes=entry.payload_bytes,
                message_bytes=entry.message_bytes,
                chip_capacity=entry.chip_capacity,
                consultations_kept=entry.consultations_kept,
                consultations_total=entry.consultations_total,
                vaccines_kept=entry.vaccines_kept,
                vaccines_total=entry.vaccines_total,
                # In UTC: SQLite (tests, local development) keeps the wall time
                # and drops the offset, which would shift the summary window.
                written_at=entry.written_at.astimezone(timezone.utc),
                app_version=app_version,
                reported_by=reporter.id,
                organization_id=reporter.organization_id,
            )
        )
        stored += 1

    if dropped:
        logger.warning(
            "Dropped %d NFC write metrics that contradict themselves (%s) "
            "reporter_id=%s app_version=%s",
            sum(dropped.values()),
            ", ".join(f"{reason}={count}" for reason, count in sorted(dropped.items())),
            reporter.id,
            app_version,
        )

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.NFC_WRITE_METRIC_RETENTION_DAYS
    )
    db.query(NfcWriteMetric).filter(NfcWriteMetric.reported_at < cutoff).delete(
        synchronize_session=False
    )

    db.commit()
    return stored


def summarize_write_metrics(
    db: Session,
    window_days: int = 90,
    organization_id: Optional[str] = None,
) -> NfcWriteMetricSummaryResponse:
    """
    Size the writes made inside the window, by device role, payload format and
    chip capacity.

    The window is on ``written_at``, the device's clock, as the key-version
    usage window is on ``observed_at``: the question is how big writes were
    when they were made, and a brigade that was offline for a week reports
    them a week late. A skewed device clock can move a write in or out of the
    window; it cannot change its size.

    ``organization_id`` limits the summary to the writes reported by that
    organization's users; None reads every organization.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    query = db.query(
        NfcWriteMetric.device_role,
        NfcWriteMetric.payload_format,
        NfcWriteMetric.chip_capacity,
        NfcWriteMetric.message_bytes,
        NfcWriteMetric.consultations_kept,
        NfcWriteMetric.consultations_total,
        NfcWriteMetric.vaccines_kept,
        NfcWriteMetric.vaccines_total,
    ).filter(NfcWriteMetric.written_at >= cutoff)
    if organization_id is not None:
        query = query.filter(NfcWriteMetric.organization_id == organization_id)

    groups: dict[tuple[str, int, int], list] = defaultdict(list)
    for row in query:
        groups[(row.device_role, row.payload_format, row.chip_capacity)].append(row)

    return NfcWriteMetricSummaryResponse(
        window_days=window_days,
        groups=[_group(key, rows) for key, rows in sorted(groups.items())],
    )


def _group(key: tuple[str, int, int], rows: list) -> NfcWriteMetricGroup:
    role, payload_format, capacity = key
    sizes = sorted(row.message_bytes for row in rows)
    utilization = sorted(
        row.message_bytes / row.chip_capacity * 100
        for row in rows
        if row.chip_capacity > 0
    )
    consultations = [
        _dropped(row.consultations_kept, row.consultations_total) for row in rows
    ]
    vaccines = [_dropped(row.vaccines_kept, row.vaccines_total) for row in rows]
    return NfcWriteMetricGroup(
        device_role=role,
        payload_format=payload_format,
        chip_capacity=capacity,
        writes=len(rows),
        message_bytes_median=_nearest_rank(sizes, 0.5),
        message_bytes_p90=_nearest_rank(sizes, 0.9),
        message_bytes_max=sizes[-1],
        utilization_median_pct=(
            round(_nearest_rank(utilization, 0.5), 1) if utilization else None
        ),
        trimmed_writes=sum(
            1 for c, v in zip(consultations, vaccines) if c > 0 or v > 0
        ),
        consultations_dropped=sum(consultations),
        vaccines_dropped=sum(vaccines),
    )


def _nearest_rank(ordered: Sequence[_T], q: float) -> _T:
    """The element at index ``int(q * (n - 1))`` of an ascending, non-empty list."""
    return ordered[int(q * (len(ordered) - 1))]


def _dropped(kept: Optional[int], total: Optional[int]) -> int:
    """Items of the record the copy did not carry; 0 when not reported."""
    if kept is None or total is None:
        return 0
    return total - kept
