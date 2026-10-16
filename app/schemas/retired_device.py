from datetime import datetime
from typing import List

from pydantic import BaseModel, Field


class RetiredDevicesResponse(BaseModel):
    """Retired patient and guardian devices, as hashes, for the offline check."""

    uid_hashes: List[str] = Field(
        ...,
        description=(
            "sha256 hex of 'hwb-retired-device:v1:' + the UID with only its hex "
            "digits, upper case. Sorted, no duplicates."
        ),
    )
    server_time: datetime = Field(
        ..., description="Pass it back as `since` on the next call."
    )
