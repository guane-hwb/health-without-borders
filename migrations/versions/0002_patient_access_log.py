"""patient access log; uploader on emergency access entries

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24

Audit: be-v2-lecturas-de-historias-sin-registro-de-acceso,
be-v2-bitacora-emergencia-actor-declarado-por-cliente.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_NAME_COMMENT = 'Patient name as reported by the client (decrypted before sync)'
_NEW_NAME_COMMENT = (
    'No longer written (the UID identifies the record); kept for entries synced '
    'before September 2026'
)
_UPLOADED_BY_COMMENT = (
    'Authenticated user whose session synced the entry. user_id is what the device '
    'declares; this is what the server verified. Null only for entries synced '
    'before the column existed.'
)


def upgrade() -> None:
    op.create_table(
        'patient_access_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('patient_id', sa.String(), nullable=False,
                  comment='Server id of the patient whose record was accessed'),
        sa.Column('actor_id', sa.String(), nullable=False,
                  comment='Authenticated user who made the request'),
        sa.Column('organization_id', sa.String(), nullable=False,
                  comment='Organization of the actor at the time of the access'),
        sa.Column('channel', sa.String(), nullable=False,
                  comment="'scan' (bracelet), 'search' (identity lookup) or 'sync' (write)"),
        sa.Column('guardian_factor', sa.Boolean(), server_default=sa.text('false'),
                  nullable=False,
                  comment="Whether the guardian's card was presented and matched"),
        sa.Column('reason', sa.String(), nullable=True,
                  comment='Reason given by the caller, when the channel accepts one'),
        sa.Column('accessed_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False,
                  comment='Server time of the access'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('accessed_at', 'actor_id', 'id', 'organization_id', 'patient_id'):
        op.create_index(
            op.f(f'ix_patient_access_log_{column}'), 'patient_access_log', [column],
            unique=False,
        )

    # Batch mode: plain ALTERs on PostgreSQL, a table copy on SQLite (tests, CI).
    with op.batch_alter_table('emergency_access_log') as batch:
        batch.add_column(sa.Column('uploaded_by', sa.String(), nullable=True,
                                   comment=_UPLOADED_BY_COMMENT))
        batch.alter_column('patient_name', existing_type=sa.String(),
                           comment=_NEW_NAME_COMMENT,
                           existing_comment=_OLD_NAME_COMMENT,
                           existing_nullable=True)
        batch.create_index(op.f('ix_emergency_access_log_uploaded_by'), ['uploaded_by'],
                           unique=False)
        batch.create_foreign_key('fk_emergency_access_log_uploaded_by_users', 'users',
                                 ['uploaded_by'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('emergency_access_log') as batch:
        batch.drop_constraint('fk_emergency_access_log_uploaded_by_users',
                              type_='foreignkey')
        batch.drop_index(op.f('ix_emergency_access_log_uploaded_by'))
        batch.alter_column('patient_name', existing_type=sa.String(),
                           comment=_OLD_NAME_COMMENT,
                           existing_comment=_NEW_NAME_COMMENT,
                           existing_nullable=True)
        batch.drop_column('uploaded_by')
    op.drop_table('patient_access_log')
