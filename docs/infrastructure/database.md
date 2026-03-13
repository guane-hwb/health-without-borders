# Database Architecture & Schema

This document details the data persistence layer for the **Health Without Borders API**. The system utilizes a relational database model optimized for transactional integrity, multi-tenancy, interoperability with international health standards (HL7/FHIR), and seamless synchronization with offline-first mobile clients.

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

Stores the core identity data of migrant children. Data is strictly scoped by `organization_id`.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK | Unique ID (National ID, PPT, or generated UUID). |
| `organization_id`| Varchar | FK | Ensures patients are only visible to their registering NGO. |
| `device_uid` | Varchar | Unique, Not Null, Index | Hardware ID (NFC Bracelet/Tag) for physical 2FA. |
| `first_name` | Varchar | Not Null, Index | Standardized first name (ILIKE searchable). |
| `last_name` | Varchar | Not Null, Index | Standardized last name (ILIKE searchable). |
| `birth_date` | Date | Not Null | Date of Birth for age calculation and vaccine schedules. |
| `blood_type` | Varchar | Nullable | Optional blood group (e.g., O+, A-). |
| `guardian_name` | Varchar | Nullable | Name of the legal guardian or companion. |
| `guardian_phone` | Varchar | Nullable | Contact number for the guardian. |
| `full_record_json`| JSONB | Nullable | Flexible document storage for clinical evaluation, background, LLM-generated diagnoses, and vaccines. |
| `created_at` | DateTime| Default: now() | Audit metadata. |

### 2.4. Standard Clinical Catalogs

#### Vaccines Catalog (`catalog_vaccines`)
Based on the **CVX** (Code for Vaccine Administered) standard.
* **`code` (PK):** The numeric CVX code (e.g., `90707` for MMR).
* **`name`:** The official descriptive name of the vaccine.
* **`is_active`:** Boolean flag.

#### Diagnosis Catalog (`catalog_cie10`)
*Note: While this table exists for historical reference and legacy support, the primary assignment of ICD-10 and ICD-11 codes is now performed dynamically via Generative AI (Vertex AI) based on the physician's clinical evaluation notes during the sync process.*
* **`code` (PK):** The alphanumeric code (e.g., `A09.9`).
* **`description`:** The official Spanish translation.
* **`is_common`:** Boolean flag.

---

## 3. Architecture & Design Decisions

### 3.1. Strict Multi-Tenancy
The database enforces tenant isolation at the schema level. Every `Patient` and `User` must belong to an `Organization`. Queries at the service layer automatically inject the `organization_id` of the requesting user, making it mathematically impossible for a doctor in NGO "A" to query or modify a patient from NGO "B".

### 3.2. Hybrid Relational-Document Model (JSONB)
Migrant populations often have unstructured or transient data. 
* **Implementation:** We utilize PostgreSQL's `JSONB` data type for the `full_record_json` field. 
* **Querying:** The backend safely queries deeply nested JSON data allowing high-performance searches on unstructured fields without loading the entire document into memory. This JSON is also the source of truth used to build the HL7v2 messages.

### 3.3. Soft Deletion (`is_active`)
Rows in critical tables (Users, Organizations) are never physically deleted (`DELETE FROM`). This preserves historical integrity. If a doctor leaves the NGO, their historical records and clinical edits must remain valid and intact for future audits.

### 3.4. Indexing Strategy
* **Search Optimization:** B-Tree indexes are applied to `last_name` and `first_name` to support lightning-fast offline/online search functionality from mobile tablets.
* **Data Integrity:** Unique constraints are strictly enforced on `users.email`, `patients.id`, and `patients.device_uid` to prevent duplicate records during network syncing anomalies.