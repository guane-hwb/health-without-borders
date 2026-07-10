"""
Aggregated statistics schemas.

These models describe the read-only response of ``GET /api/v1/stats/overview``.
Every value is an aggregate: no patient-level identifier ever leaves this API.

Naming follows the snake_case convention already used by the Organization and
User responses so the frontend can rely on a single casing rule.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

# ============================================================================
# Scope and window
# ============================================================================


class StatsScope(BaseModel):
    """
    The organization the figures belong to.

    Both fields are ``None`` when a superadmin requests the system-wide
    aggregate across every organization.
    """

    organization_id: Optional[str] = Field(
        None, description="Organization the figures are scoped to; null = all"
    )
    organization_name: Optional[str] = Field(
        None, description="Display name of the scoped organization; null = all"
    )


class StatsWindow(BaseModel):
    """
    The inclusive date window the totals were computed over.

    Both bounds are ``None`` when no filter was supplied, meaning the totals
    cover the full history of the scope.
    """

    date_from: Optional[date] = Field(None, description="Inclusive lower bound")
    date_to: Optional[date] = Field(None, description="Inclusive upper bound")


# ============================================================================
# Totals
# ============================================================================


class StatsTotals(BaseModel):
    """
    Headline figures for the requested window.

    Two different temporal anchors are in play, by design:

    - ``patients``, ``patients_with_birth_date``, ``minors`` and ``allergies``
      are *cohort* metrics: they count patients whose registration date
      (``created_at``) falls inside the window.
    - ``vaccine_doses`` and ``encounters`` are *event* metrics: they count
      clinical events whose own date falls inside the window, no matter when
      the patient was first registered.

    Counting vaccines by the patient's registration date would silently drop
    every dose applied during a brigade to someone enrolled months earlier,
    which is the opposite of what a brigade report is for.
    """

    patients: int = Field(..., description="Patients registered within the window")
    patients_with_birth_date: int = Field(
        ..., description="Subset of `patients` with a known birth date"
    )
    minors: int = Field(..., description="Patients under 18 years old today")
    minors_pct: float = Field(
        ...,
        description=(
            "minors / patients_with_birth_date * 100. The denominator excludes "
            "patients with an unknown birth date so the ratio stays honest."
        ),
    )
    vaccine_doses: int = Field(
        ..., description="Doses with status 'completed' administered in the window"
    )
    allergies: int = Field(..., description="Allergy entries recorded for the cohort")
    encounters: int = Field(..., description="Clinical encounters started in the window")


# ============================================================================
# Trend
# ============================================================================


class TrendMetric(BaseModel):
    """
    One metric compared against the immediately preceding period.

    ``delta_pct`` is ``None`` when ``previous`` is zero: growth from nothing is
    not a percentage, and rendering "+inf%" or "+100%" would both be lies.
    """

    current: int
    previous: int
    delta_pct: Optional[float] = Field(
        None, description="Percentage change; null when `previous` is 0"
    )


class StatsTrend(BaseModel):
    """
    Period-over-period comparison.

    ``period`` tells the client which comparison was performed:

    - ``"month"``: month-to-date against the same number of days of the
      previous month. Used when no date filter is supplied. Comparing the
      first nine days of July against the whole of June would make every
      metric look like it collapsed, so the previous span is truncated to
      match the elapsed one.
    - ``"custom"``: the requested window against the immediately preceding
      window of equal length.
    """

    period: str = Field(..., description="'month' or 'custom'")
    patients: TrendMetric
    vaccine_doses: TrendMetric
    encounters: TrendMetric


# ============================================================================
# Breakdowns
# ============================================================================


class VaccineStat(BaseModel):
    """
    Doses grouped by vaccine code.

    Grouping is by ``code`` rather than by ``name`` because the name is free
    text and drifts across typos and translations. ``name`` carries the most
    frequently observed spelling for that code, purely for display.
    """

    code: str = Field(..., description="CVX code, or 'UNCODED' when absent")
    name: str = Field(..., description="Most frequent display name for this code")
    count: int


class AllergyStat(BaseModel):
    """
    Allergy entries grouped by (category, allergen).

    ``category`` is the canonical Res. 866/2021 code ('01'..'06'), never a
    presentation-layer bucket. Mapping a code to a colour is the client's job.
    """

    allergen: str = Field(..., description="Most frequent spelling of the allergen")
    category: str = Field(..., description="AllergyCategory code, '01'..'06'")
    count: int


class NationalityStat(BaseModel):
    """
    Patients grouped by nationality.

    ``code`` is echoed back exactly as stored on the patient, or 'UNK' when the
    patient has none. In practice that is an ISO 3166-1 *alpha-3* code such as
    'COL', because the registration form writes alpha-3.

    Note that this is not the representation the RDA implementation guide binds
    to: the nationality extension requires an ISO 3166-1 *numeric* code. That
    mismatch is a separate, pre-existing conformance issue in the FHIR bundle
    builder and is deliberately not papered over here — this endpoint reports
    what is actually in the database.

    Resolving a code to a country name and flag is left to the client.
    """

    code: str = Field(..., description="Nationality code as stored, or 'UNK'")
    count: int


# ============================================================================
# Response
# ============================================================================


class StatsOverviewResponse(BaseModel):
    """Full payload of ``GET /api/v1/stats/overview``."""

    scope: StatsScope
    generated_at: datetime = Field(
        ..., description="Server time the aggregate was computed, in local time"
    )
    window: StatsWindow
    totals: StatsTotals
    trend: StatsTrend

    vaccines: List[VaccineStat] = Field(default_factory=list)

    allergies: List[AllergyStat] = Field(default_factory=list)
    allergies_others: int = Field(
        0, description="Sum of the counts of allergens truncated from the list"
    )

    nationalities: List[NationalityStat] = Field(default_factory=list)
    nationalities_others: int = Field(
        0, description="Sum of the counts of nationalities truncated from the list"
    )
