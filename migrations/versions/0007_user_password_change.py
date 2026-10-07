"""users.must_change_password, users.password_changed_at

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07

Audit: be-oct26-contrasenas-sin-cambio-posible. Existing accounts start with
must_change_password false: nothing is forced on them by this revision.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column(
            'must_change_password', sa.Boolean(), server_default='false', nullable=False,
            comment=(
                'Set for an account created or reset by an administrator (who knows '
                'the password); cleared when the user chooses their own.'
            ),
        ))
        batch.add_column(sa.Column(
            'password_changed_at', sa.DateTime(timezone=True), nullable=True,
            comment='Last change or reset of the password; NULL if never changed',
        ))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('password_changed_at')
        batch.drop_column('must_change_password')
