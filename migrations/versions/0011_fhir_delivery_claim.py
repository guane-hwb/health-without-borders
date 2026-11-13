"""One /sync at a time sends a patient's RDA bundles

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13

Backend audit 2026-11-04, be-nov26-reintento-duplica-rda-en-fhir: two /sync
requests for the same patient that overlapped both read the same delivered
encounters and both sent the new RDA-Consulta. patients.fhir_delivery_claimed_until
is set by the request that sends, under the row lock, and cleared when it has
recorded what the FHIR Store accepted. It expires on its own, so a request that
dies while sending does not hold it.

Adds a nullable column; existing rows need no value.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('patients') as batch:
        batch.add_column(sa.Column(
            'fhir_delivery_claimed_until', sa.DateTime(timezone=True), nullable=True,
            comment=(
                "Set while one /sync sends this patient's RDA bundles, so a concurrent "
                "/sync does not send them again; expires on its own if that request dies"
            ),
        ))


def downgrade() -> None:
    with op.batch_alter_table('patients') as batch:
        batch.drop_column('fhir_delivery_claimed_until')
