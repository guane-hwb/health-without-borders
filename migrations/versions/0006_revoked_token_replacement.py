"""revoked_tokens.replaced_by_jti

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06

Audit: be-oct26-refresh-reintentado-revoca-todas-las-sesiones. Rows written
before this revision have no replacement, so their tokens get no retry grace.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('revoked_tokens') as batch:
        batch.add_column(sa.Column(
            'replaced_by_jti', sa.String(), nullable=True,
            comment=(
                'For a rotated refresh token, the refresh token issued in its place. '
                'Lets a retry after a lost response get through without being taken '
                'for theft. NULL for tokens revoked by logout.'
            ),
        ))


def downgrade() -> None:
    with op.batch_alter_table('revoked_tokens') as batch:
        batch.drop_column('replaced_by_jti')
