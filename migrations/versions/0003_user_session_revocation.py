"""user session revocation: users.token_version and users.created_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24

Audit: be-v2-sesiones-no-revocables-token-ligado-a-email,
be-v2-revocacion-no-corta-sesion-del-dispositivo.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column(
            'token_version', sa.Integer(), server_default=sa.text('0'), nullable=False,
            comment=(
                "Stamped into every token as 'tv'. Incrementing it invalidates every "
                "token issued before (deactivation, revoke-sessions, refresh reuse)."
            ),
        ))
        # Deliberately no server default: existing accounts stay NULL. Filling
        # them with the migration time would make every token issued before the
        # deploy look older than its account and sign everyone out.
        batch.add_column(sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=True,
            comment='Account creation time; NULL for accounts created before September 2026',
        ))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('created_at')
        batch.drop_column('token_version')
