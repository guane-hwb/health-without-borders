"""Identity document of the professional on users

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17

The app fills the practitioner of each visit (RDA Elems. 49.1, 49.2) from the
signed-in user, online and offline, so the user record carries the document.
An administrator sets it (POST /users/, PATCH /users/{id}); /users/me returns
it. Not unique: one professional can hold accounts in two organizations.

Adds two nullable columns; existing rows need no value.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column(
            'document_type', sa.String(length=5), nullable=True,
            comment=(
                "Identity document type of the professional (CC, CE, PA, PPT...); "
                "the app puts it on the visits they record (RDA Elem. 49.1)"
            ),
        ))
        batch.add_column(sa.Column(
            'document_number', sa.String(length=32), nullable=True,
            comment="Identity document number of the professional (RDA Elem. 49.2)",
        ))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('document_number')
        batch.drop_column('document_type')
