from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=3, description="Name of the organization")
    is_active: Optional[bool] = True


class OrganizationAdminCreate(BaseModel):
    """Initial org_admin to provision together with the organization."""
    full_name: str = Field(..., min_length=3, description="Full name of the administrator")
    email: EmailStr = Field(..., description="Login email of the administrator")
    password: str = Field(..., min_length=8, description="Temporary password")


class OrganizationCreate(OrganizationBase):
    """
    Payload to create a new organization.

    When `admin` is provided, the organization and its initial org_admin are
    created atomically in a single transaction (see the create endpoint).
    """
    admin: Optional[OrganizationAdminCreate] = Field(
        None, description="Optional initial org_admin, provisioned atomically"
    )


class OrganizationUpdate(BaseModel):
    """Payload to toggle an organization's active state (soft deactivate)."""
    is_active: bool = Field(..., description="New active state for the organization")


class OrganizationResponse(OrganizationBase):
    """Payload returned to the client, enriched with membership counts."""
    id: str
    user_count: int = Field(0, description="Number of users bound to this organization")
    patient_count: int = Field(0, description="Number of patients registered by this organization")

    class Config:
        from_attributes = True