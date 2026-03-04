from typing import Optional
from pydantic import BaseModel, Field

class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=3, description="Name of the organization")
    is_active: Optional[bool] = True

class OrganizationCreate(OrganizationBase):
    """Payload to create a new organization."""
    pass

class OrganizationResponse(OrganizationBase):
    """Payload returned to the client."""
    id: str

    class Config:
        from_attributes = True