"""Column comments call the NFC chips patient and guardian devices

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-10

The app calls the chips "patient device" and "guardian device"; four column
comments still said bracelet and card. Only the comments change: no data, type
or constraint is touched. SQLite stores no comments, so this does nothing there.

The downgrade leaves the new wording in place: it describes the same columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMENTS = (
    ('nfc_key_version_observations', 'device_role', sa.String(),
     "'patient' or 'guardian' device"),
    ('retired_device_uids', 'device_role', sa.String(),
     "Which device was retired — 'patient' or 'guardian' device"),
    ('patient_access_log', 'channel', sa.String(),
     "'scan' (patient device), 'search' (identity lookup) or 'sync' (write)"),
    ('patient_access_log', 'guardian_factor', sa.Boolean(),
     "Whether the guardian device was presented and matched"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table, column, column_type, comment in _COMMENTS:
        op.alter_column(table, column, existing_type=column_type, comment=comment)


def downgrade() -> None:
    pass
