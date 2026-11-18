from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.text_normalizer import normalize_document_number
from app.db.models import UserRole

#: bcrypt only reads the first 72 bytes; anything after them is silently ignored.
MAX_PASSWORD_BYTES = 72


def check_password_length(password: str) -> str:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return password


#: A password the user chooses: longer than the temporary ones an admin sets.
MIN_CHOSEN_PASSWORD_LENGTH = 12
#: Rejected whatever their length rules say (compared case-insensitively).
COMMON_PASSWORDS = frozenset({
    "123456789012", "1234567890123", "password1234", "contraseña123", "contrasena123",
    "qwertyuiop12", "change_me_now", "health-without-borders", "healthwithoutborders",
})


def check_chosen_password(password: str) -> str:
    if len(password) < MIN_CHOSEN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_CHOSEN_PASSWORD_LENGTH} characters."
        )
    if password.strip().casefold() in COMMON_PASSWORDS:
        raise ValueError("This password is too common; choose another one.")
    return check_password_length(password)


def normalize_email(email: str) -> str:
    """Emails are case-insensitive: store and compare them in lower case."""
    return email.strip().lower()


class PractitionerDocumentType(str, Enum):
    """
    Identity documents a professional can hold: the ColombianPersonIdentifier
    codes (see app.schemas.patient.DocumentType) less those for minors and
    unidentified people (CN, RC, TI, AS, MS, SI).
    """
    CC = "CC"    # Cédula de ciudadanía
    CE = "CE"    # Cédula de extranjería
    PA = "PA"    # Pasaporte
    CD = "CD"    # Carné diplomático
    SC = "SC"    # Salvoconducto de permanencia
    PE = "PE"    # Permiso Especial de Permanencia
    PT = "PT"    # Permiso Temporal de Permanencia
    PPT = "PPT"  # Permiso por protección temporal
    DE = "DE"    # Documento Extranjero


#: users.document_number is VARCHAR(32).
MAX_USER_DOCUMENT_NUMBER_LENGTH = 32


def clean_document_number(value: Optional[str]) -> Optional[str]:
    """Stored as typed, trimmed; blank (or separators only) means no document."""
    if value is None:
        return None
    value = value.strip()
    if not normalize_document_number(value):
        return None
    if len(value) > MAX_USER_DOCUMENT_NUMBER_LENGTH:
        raise ValueError(
            f"document_number must be at most {MAX_USER_DOCUMENT_NUMBER_LENGTH} characters."
        )
    return value


DOCUMENT_PAIR_MESSAGE = (
    "Send document_type and document_number together (both null to clear them)."
)


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=3, description="Full name of the user")
    role: UserRole = Field(default=UserRole.doctor, description="Role of the user")
    is_active: Optional[bool] = True
    document_type: Optional[PractitionerDocumentType] = Field(
        None,
        description=(
            "Identity document type of the professional. The app fills the "
            "practitioner of the visits it records with it (RDA Elem. 49.1)"
        ),
    )
    document_number: Optional[str] = Field(
        None, description="Identity document number of the professional (RDA Elem. 49.2)"
    )

class UserCreate(UserBase):
    """
    Schema for creating a new user. 
    The org_admin will provide a temporary password for the doctor/nurse.
    """
    password: str = Field(..., min_length=8, description="Temporary password")
    organization_id: Optional[str] = Field(None, description="Required only if superadmin is creating an org_admin")

    _normalize_email = field_validator("email")(normalize_email)
    _check_password = field_validator("password")(check_password_length)
    _clean_document = field_validator("document_number")(clean_document_number)

    @model_validator(mode="after")
    def _document_comes_in_pairs(self) -> "UserCreate":
        if (self.document_type is None) != (self.document_number is None):
            raise ValueError(DOCUMENT_PAIR_MESSAGE)
        return self


class UserUpdate(BaseModel):
    """
    Body of PATCH /users/{id}: the active state, the identity document, or both.
    A field left out is not changed.
    """
    is_active: Optional[bool] = Field(None, description="New active state for the user")
    document_type: Optional[PractitionerDocumentType] = Field(
        None, description="Identity document type; null together with document_number clears it"
    )
    document_number: Optional[str] = Field(
        None, description="Identity document number; null together with document_type clears it"
    )

    _clean_document = field_validator("document_number")(clean_document_number)

    @model_validator(mode="after")
    def _one_change_at_least(self) -> "UserUpdate":
        sent = self.model_fields_set
        if not sent:
            raise ValueError("Send is_active, or document_type with document_number.")
        if "is_active" in sent and self.is_active is None:
            raise ValueError("is_active cannot be null.")
        document_fields = sent & {"document_type", "document_number"}
        if document_fields and (
            len(document_fields) == 1
            or (self.document_type is None) != (self.document_number is None)
        ):
            raise ValueError(DOCUMENT_PAIR_MESSAGE)
        return self

class PasswordChange(BaseModel):
    """Body of POST /users/me/password."""
    current_password: str = Field(..., description="The password in use now")
    new_password: str = Field(
        ...,
        description=(
            f"At least {MIN_CHOSEN_PASSWORD_LENGTH} characters, at most "
            f"{MAX_PASSWORD_BYTES} bytes, not a common password"
        ),
    )

    _check_new = field_validator("new_password")(check_chosen_password)


class PasswordResetResponse(BaseModel):
    """Response of POST /users/{id}/reset-password: shown once, never stored in clear."""
    temporary_password: str = Field(
        ..., description="Give it to the user; they should replace it with their own"
    )


class UserResponse(UserBase):
    """
    Schema for returning user data (hides the password).
    """
    id: str
    organization_id: str
    must_change_password: bool = Field(
        False,
        description=(
            "The password was set by an administrator; the app asks the user "
            "to choose their own (POST /users/me/password). No NFC key is "
            "delivered until then"
        ),
    )
    nfc_encryption_key: Optional[str] = Field(
        None, description="Current version's hex AES-256 NFC key (backward compatible)"
    )
    nfc_key_version: Optional[int] = Field(
        None, description="Key version clients stamp into NFC payloads when writing"
    )
    nfc_keyring: Optional[dict[str, str]] = Field(
        None,
        description=(
            "{version_str: hex} of every live NFC key, for offline reads. "
            "Not delivered to a browser, or to a device that reports holding "
            "this exact ring (X-NFC-Keyring-Fingerprint)"
        ),
    )

    class Config:
        from_attributes = True