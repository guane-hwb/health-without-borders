import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Organization, User, UserRole
from app.db.session import get_db
from app.schemas.stats import StatsOverviewResponse
from app.services.stats_service import build_overview

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/overview", response_model=StatsOverviewResponse)
def get_stats_overview(
    organization_id: Optional[str] = Query(
        None,
        description=(
            "Restrict the figures to one organization. Superadmins may omit it "
            "to aggregate across all of them. Ignored for org admins, who are "
            "always scoped to their own organization."
        ),
    ),
    date_from: Optional[date] = Query(
        None, description="Inclusive lower bound of the reporting window"
    ),
    date_to: Optional[date] = Query(
        None, description="Inclusive upper bound of the reporting window"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Consolidated, aggregate-only statistics for brigade reporting.

    **Scope by role:**
    - `superadmin`: all organizations by default; one organization when
      `organization_id` is supplied.
    - `org_admin`: always their own organization. Any `organization_id` in the
      query string is ignored rather than rejected, mirroring how user creation
      pins the tenant regardless of the caller's input.
    - `doctor` / `nurse`: access denied.

    **Window semantics.** Patient counts, minors, nationalities and allergies
    are counted for patients *registered* inside the window. Vaccine doses and
    encounters are counted by the date of the clinical event itself, so a dose
    applied during the window to a patient enrolled earlier is still reported.
    Only doses with status `completed` are counted.

    **Trend.** With no `date_from`, the response compares month-to-date against
    the same elapsed span of the previous month. With a `date_from`, it compares
    the requested window against the window of equal length immediately before
    it. `delta_pct` is `null` when the previous period is empty.

    **Privacy.** The response carries no patient identifiers. It is nonetheless
    an aggregate over a possibly tiny population: in an organization with very
    few patients, an allergen or nationality breakdown approaches identifying a
    specific individual. No small-cell suppression is applied, because both
    roles that can reach this endpoint already administer the tenant whose data
    they are reading. Revisit this if the endpoint is ever exposed more widely.

    **Responses:**
    - `200`: Aggregated statistics.
    - `400`: `date_from` is later than `date_to`.
    - `403`: Caller is a `doctor` or `nurse`.
    - `404`: Requested organization does not exist.
    """
    if current_user.role not in {UserRole.superadmin, UserRole.org_admin}:
        logger.warning(
            "Unauthorized stats access attempt by actor_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges to view statistics.",
        )

    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'date_from' cannot be later than 'date_to'.",
        )

    if current_user.role == UserRole.org_admin:
        # Pin the tenant to the caller's own organization, regardless of input.
        target_org_id: Optional[str] = current_user.organization_id
    else:
        target_org_id = organization_id

    organization_name: Optional[str] = None
    if target_org_id is not None:
        organization = (
            db.query(Organization).filter(Organization.id == target_org_id).first()
        )
        if organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found.",
            )
        organization_name = organization.name

    return build_overview(
        db,
        organization_id=target_org_id,
        organization_name=organization_name,
        date_from=date_from,
        date_to=date_to,
    )
