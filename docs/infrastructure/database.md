# Database Architecture & Schema

This document details the data persistence layer for the **Health Without Borders API**. The system utilizes a relational database model optimized for transactional integrity, multi-tenancy, interoperability with FHIR R4 (Resolution 1888/2025), and seamless synchronization with offline-first mobile clients.

## 1. Technology Stack

* **Engine:** PostgreSQL 15 (Managed via Google Cloud SQL in production, Docker for local development).
* **ORM (Object-Relational Mapping):** SQLAlchemy 2.0. Leverages the Data Mapper pattern to translate database schemas into Python objects, ensuring type safety and preventing SQL injection.
* **Driver:** `psycopg2-binary` for standard TCP connections and Google Cloud Unix Socket integration.
* **Migration Strategy:** Schema initialization and catalog seeding are handled via idempotent Python scripts located in the `scripts/` directory.

---

## 2. Data Dictionary

### 2.1. Organizations (Tenants) (`organizations`)

Serves as the root boundary for the Multi-Tenant architecture, isolating data between different NGOs, clinics, or humanitarian missions.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK, UUID v4 | Internal unique identifier. |
| `name` | Varchar | Unique, Not Null, Index | The official name of the organization. |
| `is_active` | Boolean | Default: True | Soft delete flag for the entire tenant. |

### 2.2. Authentication & Authorization (`users`)

This table manages access credentials and roles. Users are strictly bound to an organization to prevent cross-tenant data leaks.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK, UUID v4 | Internal unique identifier. |
| `organization_id`| Varchar | FK | Links the user to a specific `Organization`. |
| `email` | Varchar | Unique, Not Null, Index | The user's login username (OAuth2 standard). |
| `hashed_password` | Varchar | Not Null | Bcrypt encrypted hash of the password. |
| `full_name` | Varchar | Nullable | Human-readable name of the staff member. |
| `role` | Varchar | Default: 'doctor' | RBAC assignment (`superadmin`, `org_admin`, `doctor`, `nurse`). |
| `is_active` | Boolean | Default: True | Used for soft-deleting users to preserve audit logs. |

### 2.3. Patient Demographics (`patients`)

Stores the core identity data of migrant children. Relational columns mirror the most-queried RDA elements (Resolution 866/2021) so the database can filter without scanning JSON. The `organization_id` tracks which organization originally registered the patient (traceability), but patient records are globally accessible by any authenticated professional.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK, UUID v4 | Server-generated unique identifier. The authoritative patient ID. |
| `frontend_patient_id` | Varchar | Not Null, Index | Frontend-generated UUID sent during first sync. Used for sync correlation. |
| `organization_id`| Varchar | FK, Not Null | Organization that originally registered this patient. |
| `device_uid` | Varchar | Unique, Not Null, Index | Hardware ID (NFC Bracelet/Tag) for physical 2FA. |
| `document_type` | Varchar(5) | Index | Identity document type — CC, CE, TI, RC, PT, PE, etc. (Res. 866 Elem. 2.1). |
| `document_number`| Varchar | Index | Identity document number (Res. 866 Elem. 2.2). |
| `first_name` | Varchar | Not Null, Index | Primer nombre (case-insensitive search). |
| `last_name` | Varchar | Not Null, Index | Primer apellido (Res. 866 Elem. 3.1). |
| `second_last_name`| Varchar | Nullable | Segundo apellido (Res. 866 Elem. 3.2). |
| `birth_date` | Date | Not Null | Date of birth for age calculation and vaccine schedules. |
| `biological_sex` | Varchar(2) | Nullable | Biological sex: M, F, I (Res. 866 Elem. 5). |
| `blood_type` | Varchar(5) | Nullable | Optional blood group (e.g., O+, A-). |
| `nationality_code`| Varchar(3) | Index | ISO 3166-1 country code (Res. 866 Elems. 1.1, 1.2). Critical for migrant population filtering. |
| `guardian_name` | Varchar | Nullable | Name of the legal guardian or companion. |
| `guardian_phone` | Varchar | Nullable | Contact number for the guardian. |
| `guardian2_name` | Varchar | Nullable | Name of the second guardian (optional). |
| `guardian2_phone` | Varchar | Nullable | Contact phone of the second guardian (optional). |
| `full_record_json`| JSON | Nullable | Authoritative source for the complete patient payload (clinical evaluations, diagnoses, allergies, vaccinations, family history). |
| `synced_encounter_ids`| JSON | Default: [] | List of `encounterIdentifier` UUIDs already sent to the FHIR Store. Used for encounter-based delta sync. |
| `background_data_hash`| Varchar(64) | Nullable | SHA-256 hash of background data fields (demographics, guardians, allergies, chronic conditions). Used to detect changes for RDA-Paciente regeneration. |
| `rda_paciente_sent`| Boolean | Default: false | Whether the RDA-Paciente bundle has been sent to the FHIR Store at least once. |
| `created_at` | DateTime | Default: now() | Audit metadata — record creation timestamp. |
| `updated_at` | DateTime | Default: now(), onupdate | Audit metadata — last modification timestamp. |

**Constraints:**

- `UNIQUE(frontend_patient_id, organization_id)` — Two organizations can independently register the same frontend-generated ID without collision.
- `UNIQUE(device_uid)` — A hardware bracelet can only be linked to one patient globally.

### 2.4. Token Revocation (`revoked_tokens`)

Stores the JTI (JWT ID) of tokens that have been explicitly revoked via logout or refresh token rotation. Checked on every authenticated request. Entries whose `expires_at` has passed can be safely deleted (the token would be invalid anyway).

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `jti` | Varchar | PK | JWT ID claim from the revoked token. |
| `revoked_at` | DateTime | Default: now() | When the token was revoked. |
| `expires_at` | DateTime | Not Null | Original token expiry — safe to delete this row after this time. |

### 2.5. Standard Clinical Catalogs

#### Vaccines Catalog (`catalog_vaccines`)
Based on the **CVX** (Code for Vaccine Administered) standard.
* **`code` (PK):** The numeric CVX code (e.g., `90707` for MMR).
* **`name`:** The official descriptive name of the vaccine.
* **`is_active`:** Boolean flag.

#### Diagnosis Catalog (`catalog_cie10`)
*Note: While this table exists for historical reference, the primary assignment of ICD-10 and ICD-11 codes is now performed dynamically via the LLM service (Vertex AI) during the sync process.*
* **`code` (PK):** The alphanumeric code (e.g., `A09.9`).
* **`description`:** The official Spanish translation.
* **`is_common`:** Boolean flag.

---

## 3. Architecture & Design Decisions

### 3.1. Multi-Tenancy & Global Patient Access
Every `User` must belong to an `Organization`. Patient records track the `organization_id` of the registering organization for traceability. However, patients are **globally accessible** by any authenticated professional — this is by design for humanitarian settings where a child registered by NGO "A" in Cúcuta may later be seen by NGO "B" in Bogotá. The `frontend_patient_id` + `organization_id` composite unique constraint prevents cross-org data overwrites during sync, while the server-generated `id` (PK) ensures no frontend-generated ID collisions.

### 3.2. Hybrid Relational-Document Model (JSON)
Migrant populations often have unstructured or transient data.
* **Implementation:** PostgreSQL's `JSON` data type stores the `full_record_json` field — the authoritative source for the complete patient payload.
* **Relational Columns:** The most-queried fields (`document_number`, `first_name`, `last_name`, `nationality_code`) are mirrored as indexed relational columns for fast lookups without scanning JSON.
* **FHIR Source:** The JSON is the source of truth used to build FHIR R4 RDA bundles for interoperability.

### 3.3. Delta Sync Tracking
Three columns track sync state: `synced_encounter_ids` (JSON list of encounter UUIDs already sent), `rda_paciente_sent` (boolean), and `background_data_hash` (SHA-256). Visits are identified by their `encounterIdentifier` UUID rather than list index, preventing duplicates when records are merged from multiple devices. The background hash detects changes in demographics, allergies, and chronic conditions to avoid unnecessary RDA-Paciente retransmission. Tracking is updated **only after successful FHIR Store upload** — if GCP fails, the next sync retries automatically.

### 3.4. Soft Deletion (`is_active`)
Rows in critical tables (Users, Organizations) are never physically deleted. This preserves historical integrity for future audits.

### 3.5. Indexing Strategy
* **Search Optimization:** B-Tree indexes on `first_name`, `last_name`, `document_number`, and `nationality_code` for fast patient lookups. Composite index on `(organization_id, frontend_patient_id)` for sync lookups.
* **Data Integrity:** Unique constraints on `users.email`, `patients.device_uid`, and composite `(frontend_patient_id, organization_id)` to prevent duplicates during network sync anomalies.

---

## 4. Role-Based Access Control (RBAC) Matrix

### 4.1. Organizations

| Endpoint | `superadmin` | `org_admin` | `doctor` | `nurse` |
|---|---|---|---|---|
| `POST /organizations` | ✅ | ❌ | ❌ | ❌ |
| `GET /organizations` | ✅ All orgs | ✅ Own org only | ❌ | ❌ |

### 4.2. Users

| Endpoint | `superadmin` | `org_admin` | `doctor` | `nurse` |
|---|---|---|---|---|
| `POST /users` | ✅ Any org | ✅ Own org only (`doctor`/`nurse` only) | ❌ | ❌ |
| `GET /users` | ✅ All users | ✅ Own org only | ❌ | ❌ |

### 4.3. Patients

| Endpoint | `superadmin` | `org_admin` | `doctor` | `nurse` |
|---|---|---|---|---|
| `GET /patients/scan/{device_uid}` | ❌ | ✅ | ✅ Global | ✅ Global |
| `POST /patients/sync` | ❌ | ❌ | ✅ Full record | ✅ Vaccines only¹ |
| `GET /patients/search` | ❌ | ✅ | ✅ Global | ✅ Global |

> Patients are global — any authenticated professional from any organization
> can read and update any patient. The `organization_id` on the patient record
> tracks who originally registered them (traceability), not access control.

### 4.4. Design Rationale

- **`superadmin` has zero clinical access.** It is a platform administrator role. It cannot read, create, or modify any patient record.
- **`org_admin` manages staff, not patients.** It can provision and list users within its organization but has no access to clinical data.
- **Multi-tenancy is enforced at the query level**, not just the role check. Every database query is automatically scoped to `current_user.organization_id`.