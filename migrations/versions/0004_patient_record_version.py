"""patients.record_version

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24

Audit: x-v2-sync-sin-control-de-versiones-pierde-datos-clinicos.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing records start at version 1, the same as a new one.
    with op.batch_alter_table('patients') as batch:
        batch.add_column(sa.Column(
            'record_version', sa.Integer(), server_default=sa.text('1'), nullable=False,
            comment=(
                'Incremented by every /sync write. Clients send the version their copy '
                'is based on (baseVersion) so an older offline copy is detected.'
            ),
        ))


def downgrade() -> None:
    with op.batch_alter_table('patients') as batch:
        batch.drop_column('record_version')
