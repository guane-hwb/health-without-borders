"""login_failures

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07

Audit: be-oct26-rate-limit-por-instancia-y-solo-por-ip (the per-account part).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'login_failures',
        sa.Column('key', sa.String(length=64), nullable=False,
                  comment='HMAC-SHA256 of the normalized email: no email is stored'),
        sa.Column('failures', sa.Integer(), nullable=False,
                  comment='Failures in the current streak'),
        sa.Column('first_failure_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True,
                  comment='Sign-in is paused for this account until then'),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_index(
        op.f('ix_login_failures_last_failure_at'), 'login_failures', ['last_failure_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_login_failures_last_failure_at'), table_name='login_failures')
    op.drop_table('login_failures')
