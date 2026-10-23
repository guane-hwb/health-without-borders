"""Key-version sightings keep an HMAC of the chip UID, not the UID

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23

Security review 2026-10-15, N6: nfc_key_version_observations kept the raw chip
UID with the reporter, organization and time of every sighting, forever. A UID
resolves to its patient through /scan, so the table was a trail of who saw
which person, and when. Each row now keeps device_ref, an HMAC keyed with
SECRET_KEY; this converts the existing rows. The usage report only needs
distinct chips, and the same UID always gives the same reference.

The downgrade cannot bring the UIDs back: it puts the reference in device_uid.
"""
import hashlib
import hmac
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BATCH = 500
_TABLE = 'nfc_key_version_observations'

# Frozen copy of app.services.nfc_key_version_service.device_ref as of this
# revision, so a later change there cannot change what this migration wrote.
_PREFIX = "hwb-nfc-sighting:v1:"
_NOT_HEX = re.compile(r"[^0-9A-Fa-f]")

observations = sa.table(
    _TABLE,
    sa.column('id', sa.String),
    sa.column('device_uid', sa.String),
    sa.column('device_ref', sa.String),
)


def _device_ref(uid: str) -> str:
    normalized = _NOT_HEX.sub("", uid or "").upper() or (uid or "").strip().upper()
    return hmac.new(
        settings.SECRET_KEY.encode(), (_PREFIX + normalized).encode(), hashlib.sha256
    ).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(
            'device_ref', sa.String(length=64), nullable=True,
            comment='HMAC-SHA256 of the normalized chip UID: no UID is stored',
        ))

    conn = op.get_bind()
    rows = conn.execute(sa.select(observations.c.id, observations.c.device_uid)).all()
    for start in range(0, len(rows), _BATCH):
        for row in rows[start:start + _BATCH]:
            conn.execute(
                observations.update()
                .where(observations.c.id == row.id)
                .values(device_ref=_device_ref(row.device_uid))
            )

    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_index('ix_nfc_key_version_observations_device_uid')
        batch.drop_column('device_uid')
        batch.alter_column('device_ref', existing_type=sa.String(length=64), nullable=False)
        batch.create_index(
            'ix_nfc_key_version_observations_device_ref', ['device_ref'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(
            'device_uid', sa.String(), nullable=True,
            comment='Hardware tag UID observed — not joined to any patient here',
        ))

    conn = op.get_bind()
    conn.execute(observations.update().values(device_uid=observations.c.device_ref))

    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_index('ix_nfc_key_version_observations_device_ref')
        batch.drop_column('device_ref')
        batch.alter_column('device_uid', existing_type=sa.String(), nullable=False)
        batch.create_index(
            'ix_nfc_key_version_observations_device_uid', ['device_uid'], unique=False
        )
