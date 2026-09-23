"""
Errors that carry a machine-readable ``code`` next to the human ``detail``.

The mobile app shows ``detail`` verbatim (``detail.toString()``), so it stays a
string; ``code`` is a separate top-level field that newer app builds can switch
on without parsing messages. Response body::

    {"detail": "Organization is inactive.", "code": "organization_inactive"}
"""

from typing import Dict, Optional

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


USER_INACTIVE = "user_inactive"
ORGANIZATION_INACTIVE = "organization_inactive"
GUARDIAN_REQUIRED = "guardian_required"
GUARDIAN_MISMATCH = "guardian_mismatch"
DEVICE_UID_CONFLICT = "device_uid_conflict"
DEVICE_RETIRED = "device_retired"
DUPLICATE_IDENTITY = "duplicate_identity"
IDENTITY_MISMATCH = "identity_mismatch"
