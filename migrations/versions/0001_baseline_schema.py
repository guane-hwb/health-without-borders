"""baseline schema

Revision ID: 0001
Revises: (none)
Create Date: 2026-09-24 12:02:44.848610

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create every table the models define, skipping the ones that exist.

    Deployed databases were built with scripts/create_tables.py and may hold
    all, some or none of these tables. Only what is missing is created, so the
    same revision brings an empty database, a partial one and an up-to-date
    one to the same schema, and is then recorded as applied.
    """
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if 'nfc_keyring_state' not in existing:
        op.create_table('nfc_keyring_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    if 'organizations' not in existing:
        op.create_table('organizations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_organizations_id'), 'organizations', ['id'], unique=False)
        op.create_index(op.f('ix_organizations_name'), 'organizations', ['name'], unique=True)

    if 'revoked_tokens' not in existing:
        op.create_table('revoked_tokens',
        sa.Column('jti', sa.String(), nullable=False, comment='JWT ID claim from the revoked token'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, comment='Original token expiry — safe to delete row after this time'),
        sa.PrimaryKeyConstraint('jti')
        )

    if 'emergency_access_log' not in existing:
        op.create_table('emergency_access_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('client_event_id', sa.String(), nullable=False, comment='Client-generated UUID — idempotency/dedup key across retries'),
        sa.Column('patient_uid', sa.String(), nullable=False, comment='Hardware UID of the record that was accessed'),
        sa.Column('patient_name', sa.String(), nullable=True, comment='Patient name as reported by the client (decrypted before sync)'),
        sa.Column('user_id', sa.String(), nullable=True, comment='Acting user reported by the client (the break-glass actor)'),
        sa.Column('organization_id', sa.String(), nullable=False, comment='Organization of the authenticated caller that synced the entry'),
        sa.Column('reason', sa.String(), nullable=False, comment='Why the emergency access happened (e.g. guardian_absent_offline)'),
        sa.Column('occurred_at', sa.String(), nullable=False, comment='Timestamp as reported by the client (ISO 8601; may be naive local time)'),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='Server receipt time — the defensible audit timestamp'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_emergency_access_log_client_event_id'), 'emergency_access_log', ['client_event_id'], unique=True)
        op.create_index(op.f('ix_emergency_access_log_id'), 'emergency_access_log', ['id'], unique=False)
        op.create_index(op.f('ix_emergency_access_log_patient_uid'), 'emergency_access_log', ['patient_uid'], unique=False)

    if 'patients' not in existing:
        op.create_table('patients',
        sa.Column('id', sa.String(), nullable=False, comment='Server-generated UUID4 — the authoritative patient ID'),
        sa.Column('frontend_patient_id', sa.String(), nullable=False, comment='Frontend-generated UUID sent during first sync'),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('device_uid', sa.String(), nullable=False),
        sa.Column('document_type', sa.String(length=5), nullable=True, comment='Tipo de documento — CC, CE, PA, RC, TI, PT, etc.'),
        sa.Column('document_number', sa.String(), nullable=True, comment='Número de documento de identidad del paciente'),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('last_name', sa.String(), nullable=True, comment='Primer apellido (Elem. 3.1)'),
        sa.Column('second_last_name', sa.String(), nullable=True, comment='Segundo apellido (Elem. 3.2)'),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('biological_sex', sa.String(length=2), nullable=True, comment='Sexo biológico M/F/I (Elem. 5)'),
        sa.Column('blood_type', sa.String(length=5), nullable=True),
        sa.Column('nationality_code', sa.String(length=3), nullable=True, comment='Código ISO 3166-1 alfa-3 del país de nacionalidad; el bundle FHIR lo convierte a numérico'),
        sa.Column('guardian_name', sa.String(), nullable=True),
        sa.Column('guardian_phone', sa.String(), nullable=True),
        sa.Column('guardian2_name', sa.String(), nullable=True, comment='Nombre del segundo guardián (opcional)'),
        sa.Column('guardian2_phone', sa.String(), nullable=True, comment='Teléfono del segundo guardián (opcional)'),
        sa.Column('full_record_json', sa.JSON(), nullable=True),
        sa.Column('synced_encounter_ids', sa.JSON(), server_default='[]', nullable=False, comment='List of encounterIdentifier UUIDs already sent to FHIR Store'),
        sa.Column('background_data_hash', sa.String(length=64), nullable=True, comment='SHA-256 hash of background data fields (patientInfo, guardianInfo, allergies, etc.)'),
        sa.Column('rda_paciente_sent', sa.Boolean(), server_default='false', nullable=False, comment='Whether the RDA-Paciente bundle has been sent at least once'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('frontend_patient_id', 'organization_id', name='uq_patient_frontend_id_org')
        )
        op.create_index('ix_patient_org_frontend', 'patients', ['organization_id', 'frontend_patient_id'], unique=False)
        op.create_index(op.f('ix_patients_device_uid'), 'patients', ['device_uid'], unique=True)
        op.create_index(op.f('ix_patients_document_number'), 'patients', ['document_number'], unique=False)
        op.create_index(op.f('ix_patients_document_type'), 'patients', ['document_type'], unique=False)
        op.create_index(op.f('ix_patients_first_name'), 'patients', ['first_name'], unique=False)
        op.create_index(op.f('ix_patients_frontend_patient_id'), 'patients', ['frontend_patient_id'], unique=False)
        op.create_index(op.f('ix_patients_id'), 'patients', ['id'], unique=False)
        op.create_index(op.f('ix_patients_last_name'), 'patients', ['last_name'], unique=False)
        op.create_index(op.f('ix_patients_nationality_code'), 'patients', ['nationality_code'], unique=False)

    if 'users' not in existing:
        op.create_table('users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('role', sa.Enum('superadmin', 'org_admin', 'doctor', 'nurse', name='userrole'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
        op.create_index(op.f('ix_users_full_name'), 'users', ['full_name'], unique=False)
        op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    if 'nfc_key_events' not in existing:
        op.create_table('nfc_key_events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False, comment="'generated', 'rotated', 'revoked' or 'imported'"),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_nfc_key_events_action'), 'nfc_key_events', ['action'], unique=False)
        op.create_index(op.f('ix_nfc_key_events_id'), 'nfc_key_events', ['id'], unique=False)
        op.create_index(op.f('ix_nfc_key_events_version'), 'nfc_key_events', ['version'], unique=False)

    if 'nfc_key_version_observations' not in existing:
        op.create_table('nfc_key_version_observations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('device_uid', sa.String(), nullable=False, comment='Hardware tag UID observed — not joined to any patient here'),
        sa.Column('device_role', sa.String(), server_default='patient', nullable=False, comment="'patient' bracelet or 'guardian' card"),
        sa.Column('key_version', sa.Integer(), nullable=False, comment='NFC key version that decrypted this chip'),
        sa.Column('had_header', sa.Boolean(), server_default='false', nullable=False, comment='Whether the payload carried a version header (version >= 1)'),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False, comment='When the device read the chip (client clock)'),
        sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='When the server received it — may lag by days for a brigade that was offline'),
        sa.Column('reported_by', sa.String(), nullable=True, comment='User whose device reported the sighting'),
        sa.Column('organization_id', sa.String(), nullable=True, comment='Retained for traceability only, never for access control'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['reported_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_nfc_key_version_observations_device_uid'), 'nfc_key_version_observations', ['device_uid'], unique=False)
        op.create_index(op.f('ix_nfc_key_version_observations_id'), 'nfc_key_version_observations', ['id'], unique=False)
        op.create_index(op.f('ix_nfc_key_version_observations_key_version'), 'nfc_key_version_observations', ['key_version'], unique=False)
        op.create_index(op.f('ix_nfc_key_version_observations_organization_id'), 'nfc_key_version_observations', ['organization_id'], unique=False)
        op.create_index(op.f('ix_nfc_key_version_observations_reported_by'), 'nfc_key_version_observations', ['reported_by'], unique=False)

    if 'nfc_keys' not in existing:
        op.create_table('nfc_keys',
        sa.Column('version', sa.Integer(), nullable=False, comment='Key version, stamped into the NFC payload header'),
        sa.Column('wrapped_key', sa.String(), nullable=False, comment='Base64 of nonce+ciphertext+tag, sealed under the KEK'),
        sa.Column('kek_id', sa.String(), nullable=False, comment='Fingerprint of the KEK that sealed this row — not the secret'),
        sa.Column('status', sa.String(), server_default='live', nullable=False, comment="'live' (delivered to devices) or 'revoked' (never delivered)"),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.String(), nullable=True),
        sa.Column('revoke_reason', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('version')
        )
        op.create_index(op.f('ix_nfc_keys_status'), 'nfc_keys', ['status'], unique=False)

    if 'retired_device_uids' not in existing:
        op.create_table('retired_device_uids',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('device_uid', sa.String(), nullable=False, comment='The retired hardware tag UID — no longer bound to any patient'),
        sa.Column('patient_id', sa.String(), nullable=False, comment='Patient whose record this UID was retired from'),
        sa.Column('reason', sa.String(), nullable=False, comment="Why the UID was retired — 'lost', 'damaged' or 'replaced'"),
        sa.Column('device_role', sa.String(), server_default='patient', nullable=False, comment="Which device was retired — 'patient' bracelet or 'guardian' card"),
        sa.Column('retired_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('retired_by', sa.String(), nullable=True, comment='User id that performed the retirement, when known'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_retired_device_uids_device_uid'), 'retired_device_uids', ['device_uid'], unique=False)
        op.create_index(op.f('ix_retired_device_uids_id'), 'retired_device_uids', ['id'], unique=False)
        op.create_index(op.f('ix_retired_device_uids_patient_id'), 'retired_device_uids', ['patient_id'], unique=False)

    # Added to an existing table in August 2026 (PR #34); create_tables.py
    # never applied it to databases created before that.
    if 'retired_device_uids' in existing:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('retired_device_uids')}
        if 'device_role' not in columns:
            op.add_column(
                'retired_device_uids',
                sa.Column('device_role', sa.String(), nullable=False, server_default='patient'),
            )


def downgrade() -> None:
    """The baseline is the whole clinical schema; it is never dropped."""
    raise NotImplementedError("The baseline migration cannot be downgraded.")
