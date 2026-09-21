"""
Aggregated statistics service.

Computes the consolidated figures served by ``GET /api/v1/stats/overview``.

Design notes
------------
**Where the numbers live.** Patient counts, ages and nationalities sit in
relational columns and could be aggregated in SQL. Vaccines, allergies and
encounters live inside ``Patient.full_record_json``, which is a portable
``JSON`` column rather than Postgres ``JSONB`` — the test suite runs on SQLite,
so ``jsonb_array_elements`` is not available. Everything is therefore folded in
a single streaming pass over the scoped rows, projecting only the four columns
this module actually reads.

That is O(patients) per request. It is the right trade for brigade-sized
populations. Once a single organization grows past tens of thousands of
patients, the JSON-derived metrics should move to dedicated ``vaccinations``
and ``allergies`` tables populated at sync time.

**Two temporal anchors.** Filtering *rows* by ``created_at`` would drop every
dose applied during a brigade to a patient enrolled months earlier. So the row
scan is never date-filtered; instead each metric is tested against the window
using its own anchor:

===================  ===========================================
Metric               Anchor
===================  ===========================================
patients, minors,    ``Patient.created_at``  (cohort)
nationalities,
allergies
vaccine_doses        ``vaccinationRecord[].date``  (event)
encounters           ``medicalHistory[].startDateTime``  (event)
===================  ===========================================

Allergies carry no date of their own in the RDA schema, so they are necessarily
a cohort metric.

**Time zone.** ``created_at`` is stored as an aware UTC timestamp under
Postgres and as a naive UTC timestamp under SQLite. Clinical timestamps written
by the app are local wall-clock. Both are normalised to a *local calendar date*
before any comparison, which removes every offset question from the counting
logic and puts month boundaries where a Colombian user expects them.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Patient
from app.schemas.stats import (
    AllergyStat,
    NationalityStat,
    StatsOverviewResponse,
    StatsScope,
    StatsTotals,
    StatsTrend,
    StatsWindow,
    TrendMetric,
    VaccineStat,
)

logger = logging.getLogger(__name__)

# Bucket labels for records that carry no code.
UNCODED_VACCINE = "UNCODED"
UNKNOWN_NATIONALITY = "UNK"

# Only doses actually administered count. The app also records 'refused' and
# 'not_given', which would otherwise inflate "vaccines administered".
COMPLETED_VACCINE_STATUS = "completed"

# Fallback when an allergy entry has no category (AllergyCategory.OTRA).
DEFAULT_ALLERGY_CATEGORY = "06"

# Allergens are free text and can have a long tail; nationalities cannot, but a
# cap keeps the payload bounded either way. Truncated counts are summed into
# the corresponding `*_others` field rather than silently dropped.
MAX_ALLERGENS = 15
MAX_NATIONALITIES = 10

MINOR_AGE_YEARS = 18

# Rows are streamed rather than materialised: the JSON payloads are large.
YIELD_PER = 200

# An inclusive [start, end] calendar-date range. ``None`` means unbounded.
DateRange = Tuple[Optional[date], Optional[date]]


# ============================================================================
# Time helpers
# ============================================================================


def _resolve_tz() -> tzinfo:
    """
    Return the reporting time zone.

    Prefers the configured IANA zone. Slim container images sometimes ship
    without the tz database; rather than crash or silently report in UTC (which
    would push the last five hours of every Colombian month into the next one),
    fall back to the configured fixed offset and say so in the logs.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.STATS_TIMEZONE)
    except Exception:  # noqa: BLE001 - any tz-db failure must degrade, not crash
        offset_hours = settings.STATS_TIMEZONE_FALLBACK_OFFSET_HOURS
        logger.warning(
            "Time zone %r unavailable; using fixed UTC%+d offset instead. "
            "Month boundaries will drift wherever daylight saving applies.",
            settings.STATS_TIMEZONE,
            offset_hours,
        )
        return timezone(timedelta(hours=offset_hours))


def _server_date(value: Optional[datetime], tz: tzinfo) -> Optional[date]:
    """Local calendar date of a server-generated timestamp (assumed UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).date()


def _plain_date(value: Any) -> Optional[date]:
    """Parse a value that represents a calendar date with no time component."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _clinical_date(value: Any, tz: tzinfo) -> Optional[date]:
    """
    Local calendar date of a timestamp written by the clinical app.

    An offset-aware value is converted to the reporting zone. A naive value is
    taken at face value: it is the clinician's wall clock, not UTC.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return _plain_date(value)
    else:
        return _plain_date(value)

    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(tz).date()


def _within(value: Optional[date], window: DateRange) -> bool:
    """
    Whether ``value`` falls inside the inclusive ``window``.

    A fully open window matches everything, including records whose date could
    not be parsed — an unbounded query should report the whole history rather
    than quietly discard malformed rows. Once either bound is set, an unknown
    date cannot be placed and is excluded.
    """
    start, end = window
    if start is None and end is None:
        return True
    if value is None:
        return False
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _age_years(born: date, today: date) -> int:
    """Completed years between ``born`` and ``today``."""
    had_birthday = (today.month, today.day) >= (born.month, born.day)
    return today.year - born.year - (0 if had_birthday else 1)


def _month_trend_windows(today: date) -> Tuple[DateRange, DateRange]:
    """
    Month-to-date against the same elapsed span of the previous month.

    Comparing the first nine days of July against the whole of June would make
    every metric look like a collapse, so the previous span is truncated to the
    same number of elapsed days (clamped to the last day of that month, which
    matters for a 31st against February).
    """
    current_start = today.replace(day=1)
    elapsed_days = (today - current_start).days

    previous_end_of_month = current_start - timedelta(days=1)
    previous_start = previous_end_of_month.replace(day=1)
    previous_end = min(
        previous_start + timedelta(days=elapsed_days),
        previous_end_of_month,
    )
    return (current_start, today), (previous_start, previous_end)


def _custom_trend_windows(
    date_from: date, date_to: Optional[date], today: date
) -> Tuple[DateRange, DateRange]:
    """The requested window against the preceding window of equal length."""
    current_end = date_to or today
    span_days = (current_end - date_from).days

    previous_end = date_from - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span_days)
    return (date_from, current_end), (previous_start, previous_end)


def _trend_metric(current: int, previous: int) -> TrendMetric:
    """Build a trend metric, leaving ``delta_pct`` null when it is undefined."""
    delta = round((current - previous) / previous * 100, 1) if previous > 0 else None
    return TrendMetric(current=current, previous=previous, delta_pct=delta)


def _iter_entries(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield the dict entries of a JSON list, tolerating a malformed payload."""
    if not isinstance(value, list):
        return ()
    return (item for item in value if isinstance(item, Mapping))


def _text(value: Any) -> str:
    """Normalise a JSON scalar to a stripped string."""
    return str(value).strip() if value is not None else ""


# ============================================================================
# Aggregation
# ============================================================================


class _Accumulator:
    """Mutable tallies folded over the scoped patient rows."""

    def __init__(self) -> None:
        self.patients = 0
        self.patients_with_birth_date = 0
        self.minors = 0
        self.allergies = 0
        self.vaccine_doses = 0
        self.encounters = 0

        # [current period, previous period]
        self.trend_patients = [0, 0]
        self.trend_vaccine_doses = [0, 0]
        self.trend_encounters = [0, 0]

        self.vaccine_counts: Counter = Counter()
        self.vaccine_names: dict[str, Counter] = defaultdict(Counter)

        # Keyed by (category, casefolded allergen) so that "Maní" and "maní"
        # collapse into one bucket.
        self.allergy_counts: Counter = Counter()
        self.allergy_labels: dict[tuple, Counter] = defaultdict(Counter)

        self.nationality_counts: Counter = Counter()


def _fold_patient(
    acc: _Accumulator,
    *,
    created_local: Optional[date],
    birth_date: Optional[date],
    nationality_code: Optional[str],
    record: Any,
    today: date,
    tz: tzinfo,
    window: DateRange,
    current: DateRange,
    previous: DateRange,
) -> None:
    """Fold one patient row into ``acc``."""
    in_window = _within(created_local, window)

    if in_window:
        acc.patients += 1
        if birth_date is not None:
            acc.patients_with_birth_date += 1
            if _age_years(birth_date, today) < MINOR_AGE_YEARS:
                acc.minors += 1
        acc.nationality_counts[_text(nationality_code) or UNKNOWN_NATIONALITY] += 1

    if _within(created_local, current):
        acc.trend_patients[0] += 1
    if _within(created_local, previous):
        acc.trend_patients[1] += 1

    payload: Mapping[str, Any] = record if isinstance(record, Mapping) else {}

    # --- Allergies: cohort metric, no date of their own ---
    if in_window:
        for entry in _iter_entries(payload.get("allergies")):
            allergen = _text(entry.get("allergen"))
            if not allergen:
                continue
            category = _text(entry.get("category")) or DEFAULT_ALLERGY_CATEGORY
            key = (category, allergen.casefold())
            acc.allergy_counts[key] += 1
            acc.allergy_labels[key][allergen] += 1
            acc.allergies += 1

    # --- Vaccine doses: event metric, deduplicated per patient ---
    seen_doses: set[str] = set()
    for entry in _iter_entries(payload.get("vaccinationRecord")):
        if _text(entry.get("status")).lower() != COMPLETED_VACCINE_STATUS:
            continue

        dose_id = _text(entry.get("vaccinationId"))
        if dose_id:
            if dose_id in seen_doses:
                continue
            seen_doses.add(dose_id)

        applied_on = _plain_date(entry.get("date"))
        if _within(applied_on, window):
            code = _text(entry.get("vaccineCode")) or UNCODED_VACCINE
            name = _text(entry.get("vaccineName"))
            acc.vaccine_doses += 1
            acc.vaccine_counts[code] += 1
            if name:
                acc.vaccine_names[code][name] += 1
        if _within(applied_on, current):
            acc.trend_vaccine_doses[0] += 1
        if _within(applied_on, previous):
            acc.trend_vaccine_doses[1] += 1

    # --- Encounters: event metric, deduplicated per patient ---
    seen_encounters: set[str] = set()
    for entry in _iter_entries(payload.get("medicalHistory")):
        encounter_id = _text(entry.get("encounterIdentifier"))
        if encounter_id:
            if encounter_id in seen_encounters:
                continue
            seen_encounters.add(encounter_id)

        started_on = _clinical_date(entry.get("startDateTime"), tz)
        if _within(started_on, window):
            acc.encounters += 1
        if _within(started_on, current):
            acc.trend_encounters[0] += 1
        if _within(started_on, previous):
            acc.trend_encounters[1] += 1


def _build_vaccines(acc: _Accumulator) -> list[VaccineStat]:
    ordered = sorted(acc.vaccine_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    stats = []
    for code, count in ordered:
        names = acc.vaccine_names.get(code)
        display = names.most_common(1)[0][0] if names else code
        stats.append(VaccineStat(code=code, name=display, count=count))
    return stats


def _build_allergies(acc: _Accumulator) -> tuple[list[AllergyStat], int]:
    ordered = sorted(
        acc.allergy_counts.items(),
        key=lambda kv: (-kv[1], kv[0][1], kv[0][0]),
    )
    stats = [
        AllergyStat(
            allergen=acc.allergy_labels[key].most_common(1)[0][0],
            category=key[0],
            count=count,
        )
        for key, count in ordered[:MAX_ALLERGENS]
    ]
    others = sum(count for _, count in ordered[MAX_ALLERGENS:])
    return stats, others


def _build_nationalities(acc: _Accumulator) -> tuple[list[NationalityStat], int]:
    ordered = sorted(acc.nationality_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    stats = [
        NationalityStat(code=code, count=count)
        for code, count in ordered[:MAX_NATIONALITIES]
    ]
    others = sum(count for _, count in ordered[MAX_NATIONALITIES:])
    return stats, others


# ============================================================================
# Public entry point
# ============================================================================


def build_overview(
    db: Session,
    *,
    organization_id: Optional[str] = None,
    organization_name: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    now: Optional[datetime] = None,
) -> StatsOverviewResponse:
    """
    Compute the consolidated statistics for a scope and date window.

    ``organization_id`` of ``None`` aggregates across every organization; the
    caller is responsible for having authorised that. ``now`` is injectable so
    that month-boundary behaviour can be tested deterministically instead of
    by patching ``datetime``.

    Trend semantics depend on the window:
      - no ``date_from``: month-to-date against the previous month (``"month"``)
      - ``date_from`` given: the window against the preceding window of equal
        length (``"custom"``)

    A lone ``date_to`` leaves the window open-ended on the left, so no equal
    length comparison exists and the monthly trend is reported instead.
    """
    tz = _resolve_tz()
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    now_local = reference.astimezone(tz)
    today = now_local.date()

    window: DateRange = (date_from, date_to)
    if date_from is not None:
        trend_period = "custom"
        current, previous = _custom_trend_windows(date_from, date_to, today)
    else:
        trend_period = "month"
        current, previous = _month_trend_windows(today)

    statement = select(
        Patient.created_at,
        Patient.birth_date,
        Patient.nationality_code,
        Patient.full_record_json,
    )
    if organization_id is not None:
        statement = statement.where(Patient.organization_id == organization_id)

    acc = _Accumulator()
    rows = db.execute(statement.execution_options(yield_per=YIELD_PER))
    for created_at, birth_date, nationality_code, record in rows:
        _fold_patient(
            acc,
            created_local=_server_date(created_at, tz),
            birth_date=_plain_date(birth_date),
            nationality_code=nationality_code,
            record=record,
            today=today,
            tz=tz,
            window=window,
            current=current,
            previous=previous,
        )

    minors_pct = (
        round(acc.minors / acc.patients_with_birth_date * 100, 2)
        if acc.patients_with_birth_date
        else 0.0
    )

    allergies, allergies_others = _build_allergies(acc)
    nationalities, nationalities_others = _build_nationalities(acc)

    return StatsOverviewResponse(
        scope=StatsScope(
            organization_id=organization_id,
            organization_name=organization_name,
        ),
        generated_at=now_local,
        window=StatsWindow(date_from=date_from, date_to=date_to),
        totals=StatsTotals(
            patients=acc.patients,
            patients_with_birth_date=acc.patients_with_birth_date,
            minors=acc.minors,
            minors_pct=minors_pct,
            vaccine_doses=acc.vaccine_doses,
            allergies=acc.allergies,
            encounters=acc.encounters,
        ),
        trend=StatsTrend(
            period=trend_period,
            patients=_trend_metric(*acc.trend_patients),
            vaccine_doses=_trend_metric(*acc.trend_vaccine_doses),
            encounters=_trend_metric(*acc.trend_encounters),
        ),
        vaccines=_build_vaccines(acc),
        allergies=allergies,
        allergies_others=allergies_others,
        nationalities=nationalities,
        nationalities_others=nationalities_others,
    )
