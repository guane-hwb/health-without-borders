from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=3, description="Full name of the user")
    # Limitamos los roles que el frontend puede enviar por defecto
    role: str = Field(default="doctor", description="Role of the user: 'doctor' or 'nurse'")
    is_active: Optional[bool] = True

    @field_validator('role')
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed_roles = ["doctor", "nurse", "org_admin", "superadmin"]
        if v not in allowed_roles:
            raise ValueError(f"Role must be one of {allowed_roles}")
        return v

class UserCreate(UserBase):
    """
    Schema for creating a new user. 
    The org_admin will provide a temporary password for the doctor/nurse.
    """
    password: str = Field(..., min_length=8, description="Temporary password")
    organization_id: Optional[str] = Field(None, description="Required only if superadmin is creating an org_admin")

class UserResponse(UserBase):
    """
    Schema for returning user data (hides the password).
    """
    id: str               
    organization_id: str

    class Config:
        from_attributes = True