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

Stores the core identity data of migrant children. Data is strictly scoped by `organization_id`. Relational columns mirror the most-queried RDA elements (Resolution 866/2021) so the database can filter without scanning JSON.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK | Unique ID (generated UUID v4 from frontend). |
| `organization_id`| Varchar | FK, Not Null | Ensures patients are only visible to their registering NGO. |
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
| `full_record_json`| JSON | Nullable | Authoritative source for the complete patient payload (clinical evaluations, diagnoses, allergies, vaccinations, family history). |
| `synced_visit_count`| Integer | Default: 0 | Number of `medicalHistory` entries already sent to the FHIR Store. Used for delta sync logic. |
| `rda_paciente_sent`| Boolean | Default: false | Whether the RDA-Paciente bundle has been sent to the FHIR Store at least once. |
| `created_at` | DateTime | Default: now() | Audit metadata — record creation timestamp. |
| `updated_at` | DateTime | Default: now(), onupdate | Audit metadata — last modification timestamp. |

### 2.4. Standard Clinical Catalogs

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

### 3.1. Strict Multi-Tenancy
The database enforces tenant isolation at the schema level. Every `Patient` and `User` must belong to an `Organization`. Queries at the service layer automatically inject the `organization_id` of the requesting user, making it structurally impossible for a doctor in NGO "A" to query or modify a patient from NGO "B".

### 3.2. Hybrid Relational-Document Model (JSON)
Migrant populations often have unstructured or transient data.
* **Implementation:** PostgreSQL's `JSON` data type stores the `full_record_json` field — the authoritative source for the complete patient payload.
* **Relational Columns:** The most-queried fields (`document_number`, `first_name`, `last_name`, `nationality_code`) are mirrored as indexed relational columns for fast lookups without scanning JSON.
* **FHIR Source:** The JSON is the source of truth used to build FHIR R4 RDA bundles for interoperability.

### 3.3. Delta Sync Tracking
Two columns (`synced_visit_count`, `rda_paciente_sent`) track which data has already been sent to the FHIR Store. This prevents duplicate bundle transmissions and enables automatic retry: if GCP fails, the tracking is not updated, so the next sync retries the failed bundles.

### 3.4. Soft Deletion (`is_active`)
Rows in critical tables (Users, Organizations) are never physically deleted. This preserves historical integrity for future audits.

### 3.5. Indexing Strategy
* **Search Optimization:** B-Tree indexes on `first_name`, `last_name`, `document_number`, and `nationality_code` for fast patient lookups.
* **Data Integrity:** Unique constraints on `users.email`, `patients.id`, and `patients.device_uid` to prevent duplicates during network sync anomalies.

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
| `GET /patients/scan/{device_uid}` | ❌ | ✅ | ✅ Own org | ✅ Own org |
| `POST /patients/sync` | ❌ | ❌ | ✅ Full record | ✅ Vaccines only¹ |
| `GET /patients/search` | ❌ | ✅ | ✅ Own org | ✅ Own org |

> ¹ A `nurse` can call `POST /patients/sync` but the service layer blocks any attempt to add or modify `medicalHistory`. Only vaccine records can be appended.

### 4.4. Design Rationale

- **`superadmin` has zero clinical access.** It is a platform administrator role. It cannot read, create, or modify any patient record.
- **`org_admin` manages staff, not patients.** It can provision and list users within its organization but has no access to clinical data.
- **Multi-tenancy is enforced at the query level**, not just the role check. Every database query is automatically scoped to `current_user.organization_id`.