"""Bring the patients' relational columns in line with their records

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02

Audit: be-oct26-columnas-relacionales-desalineadas. The columns statistics and
/search read (nationality, guardians) were set only when a patient was created,
so a corrected nationality or a removed second guardian lived on. The service
now writes them on every sync; this updates the existing rows once.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.iso3166 import to_alpha3

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BATCH = 100
_BLOOD_TYPE_LENGTH = 5

patients = sa.table(
    'patients',
    sa.column('id', sa.String),
    sa.column('full_record_json', sa.JSON),
    sa.column('nationality_code', sa.String),
    sa.column('blood_type', sa.String),
    sa.column('guardian_name', sa.String),
    sa.column('guardian_phone', sa.String),
    sa.column('guardian2_name', sa.String),
    sa.column('guardian2_phone', sa.String),
)


def _columns(record: dict) -> dict:
    """The rules of patient_service.mirror_columns when this revision was written."""
    info = record.get('patientInfo') or {}
    guardian = record.get('guardianInfo') or {}
    guardian2 = record.get('guardian2Info') or {}
    columns = {
        'nationality_code': to_alpha3(info.get('nationalityCode')) or 'UNK',
        'guardian_name': guardian.get('name'),
        'guardian_phone': guardian.get('phone'),
        'guardian2_name': guardian2.get('name'),
        'guardian2_phone': guardian2.get('phone'),
    }
    blood_type = info.get('bloodType')
    # Records from before the length check may hold a longer value; the column
    # keeps what it has rather than failing the migration (and the startup).
    if blood_type is None or len(blood_type) <= _BLOOD_TYPE_LENGTH:
        columns['blood_type'] = blood_type
    return columns


def upgrade() -> None:
    conn = op.get_bind()
    ids = [row.id for row in conn.execute(sa.select(patients.c.id).order_by(patients.c.id))]
    # In batches: each record carries the consent signatures.
    for start in range(0, len(ids), _BATCH):
        rows = conn.execute(
            sa.select(patients).where(patients.c.id.in_(ids[start:start + _BATCH]))
        ).all()
        for row in rows:
            changed = {
                name: value
                for name, value in _columns(row.full_record_json or {}).items()
                if getattr(row, name) != value
            }
            if changed:
                conn.execute(patients.update().where(patients.c.id == row.id).values(**changed))


def downgrade() -> None:
    # Data only, and the previous values were the stale ones: nothing to undo.
    pass
