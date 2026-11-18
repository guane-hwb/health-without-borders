"""NFC write-size metrics reported by the app

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17

For every chip it writes, the app reports how big the encrypted payload and
the NDEF message were, the capacity the chip reported and, for a guardian
copy, how many consultations and vaccines it kept out of how many it would
carry on a chip with room for everything (at most three of each). nfc_write_metrics keeps those numbers so the next payload format can be
sized from field data. No chip UID, no patient, no clinical content; the
reporter, the organization and the build come from the request.

Creates a table; nothing existing changes. The downgrade drops it with its rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = 'nfc_write_metrics'
_INDEXED = ('id', 'organization_id', 'reported_at', 'reported_by')


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('device_role', sa.String(), server_default='patient', nullable=False,
                  comment="'patient' or 'guardian' device"),
        sa.Column('payload_format', sa.Integer(), nullable=False,
                  comment='Payload format written: 1 = CBOR + DEFLATE + AES-GCM, 2 = v2'),
        sa.Column('key_version', sa.Integer(), nullable=False,
                  comment='NFC key version the payload was encrypted with'),
        sa.Column('payload_bytes', sa.Integer(), nullable=False,
                  comment='Encrypted payload, in bytes'),
        sa.Column('message_bytes', sa.Integer(), nullable=False,
                  comment='Whole NDEF message written, in bytes'),
        sa.Column('chip_capacity', sa.Integer(), nullable=False,
                  comment='NDEF capacity the chip reported, in bytes'),
        sa.Column('consultations_kept', sa.Integer(), nullable=True,
                  comment='Guardian copy: consultations written to the chip; NULL if not reported'),
        sa.Column('consultations_total', sa.Integer(), nullable=True,
                  comment='Guardian copy: consultations it carries with room for all (max 3); NULL if not reported'),
        sa.Column('vaccines_kept', sa.Integer(), nullable=True,
                  comment='Guardian copy: vaccines written to the chip; NULL if not reported'),
        sa.Column('vaccines_total', sa.Integer(), nullable=True,
                  comment='Guardian copy: vaccines it carries with room for all (max 3); NULL if not reported'),
        sa.Column('written_at', sa.DateTime(timezone=True), nullable=False,
                  comment='When the device wrote the chip (client clock)'),
        sa.Column('app_version', sa.String(length=64), server_default='none', nullable=False,
                  comment="X-App-Version of the reporting build: 'none' if absent, "
                          "'invalid' if malformed"),
        sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False,
                  comment='When the server received it — may lag by days for a brigade '
                          'that was offline'),
        sa.Column('reported_by', sa.String(), nullable=True,
                  comment='User whose device reported the write'),
        sa.Column('organization_id', sa.String(), nullable=True,
                  comment="Organization of the reporting user; an org admin's summary "
                          "is limited to it"),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['reported_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in _INDEXED:
        op.create_index(op.f(f'ix_{_TABLE}_{column}'), _TABLE, [column], unique=False)


def downgrade() -> None:
    for column in _INDEXED:
        op.drop_index(op.f(f'ix_{_TABLE}_{column}'), table_name=_TABLE)
    op.drop_table(_TABLE)
