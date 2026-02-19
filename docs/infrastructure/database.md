# Database Architecture & Schema

This document details the data persistence layer for the **Health Without Borders API**. The system utilizes a relational database model optimized for transactional integrity, interoperability with international health standards (HL7/FHIR), and seamless synchronization with offline-first mobile clients.

## 1. Technology Stack

* **Engine:** PostgreSQL 15 (Managed via Google Cloud SQL in production, Docker for local development).
* **ORM (Object-Relational Mapping):** SQLAlchemy 2.0. Leverages the Data Mapper pattern to translate database schemas into Python objects, ensuring type safety and preventing SQL injection.
* **Driver:** `psycopg2-binary` for standard TCP connections and Google Cloud Unix Socket integration.
* **Migration Strategy:** Schema initialization and catalog seeding are handled via idempotent Python scripts located in the `scripts/` directory.

---

## 2. Data Dictionary

### 2.1. Authentication & Authorization (`users`)

This table manages access credentials and roles for medical staff, coordinators, and administrators.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Integer | PK, Auto-increment | Internal unique identifier. |
| `email` | Varchar | Unique, Not Null, Index | The user's login username (OAuth2 standard). |
| `hashed_password` | Varchar | Not Null | Bcrypt encrypted hash of the password. |
| `full_name` | Varchar | Nullable | Human-readable name of the staff member. |
| `role` | Varchar | Default: 'doctor' | RBAC (Role-Based Access Control) assignment. |
| `is_active` | Boolean | Default: True | Used for soft-deleting users to preserve audit logs. |

### 2.2. Patient Demographics (`patients`)

Stores the core identity data of migrant children. The design prioritizes collecting only the minimal necessary data to protect privacy while ensuring continuity of care.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | Varchar | PK | Unique ID (National ID, PPT, or generated UUID). |
| `first_name` | Varchar | Not Null, Index | Standardized first name (ILIKE searchable). |
| `last_name` | Varchar | Not Null, Index | Standardized last name (ILIKE searchable). |
| `dob` | Date | Not Null | Date of Birth for age calculation and vaccine schedules. |
| `gender` | Varchar | Not Null | Biological sex (M/F) required for clinical charts. |
| `blood_type` | Varchar | Nullable | Optional blood group (e.g., O+, A-). |
| `full_record_json`| JSONB | Nullable | Flexible document storage for nested data (e.g., guardian info, dynamic medical history). |

### 2.3. Standard Clinical Catalogs

To ensure strict interoperability with systems like the **Google Cloud Healthcare API (HL7v2)**, the system rejects free-text diagnoses or vaccine names. Instead, it references immutable international standard catalogs.

#### Vaccines Catalog (`vaccines_catalog`)
Based on the **CVX** (Code for Vaccine Administered) standard.
* **`code` (PK):** The numeric CVX code (e.g., `90707` for MMR).
* **`name`:** The official descriptive name of the vaccine.
* **`is_active`:** Boolean flag. If `False`, the vaccine is hidden from frontend forms but remains queryable for historical records.

#### Diagnosis Catalog (`diagnoses_catalog`)
Based on the **ICD-10** (International Classification of Diseases) standard.
* **`code` (PK):** The alphanumeric code (e.g., `A09.9`).
* **`description`:** The official Spanish translation of the disease.
* **`is_active`:** Boolean flag for deprecating obsolete diagnostic codes.

---

## 3. Architecture & Design Decisions

### 3.1. Hybrid Relational-Document Model (JSONB)
Migrant populations often have unstructured or transient data (e.g., shelter addresses, temporary camp locations, or varying guardian relationships) that do not fit into rigid relational columns. 
* **Implementation:** We utilize PostgreSQL's `JSONB` data type for the `full_record_json` field. 
* **Querying:** The backend safely queries deeply nested JSON data using PostgreSQL's native `json_extract_path_text` function directly through SQLAlchemy, allowing high-performance searches on unstructured fields (like Guardian Names) without loading the entire JSON document into memory.

### 3.2. Soft Deletion (`is_active`)
Rows in critical tables (Users, Catalogs) are never physically deleted (`DELETE FROM`). 
* **Rationale:** This preserves historical integrity. If a specific vaccine is discontinued globally, historical records of children who received it must remain valid and intact for future audits.

### 3.3. Indexing Strategy
* **Search Optimization:** B-Tree indexes are applied to `last_name` and `first_name` to support lightning-fast offline/online search functionality from mobile tablets.
* **Data Integrity:** Unique constraints are strictly enforced on `users.email` and `patients.id` to prevent duplicate records during network syncing anomalies.

---

## 4. Initialization and Seeding

The database schema is initialized using automated Python scripts. These should be executed via a secure Cloud SQL Proxy tunnel for production environments, or directly via Docker for local development.

1. **`scripts/create_tables.py`**: Utilizes SQLAlchemy's `Base.metadata.create_all(bind=engine)` to declaratively generate the schema if it does not already exist.
2. **`scripts/create_generic_user.py`**: Bootstraps the first administrative user required to access the system.
3. **`scripts/load_catalogs.py`**: Parses local JSON datasets of ICD-10 and CVX codes and efficiently inserts them into the database using bulk operations (`bulk_save_objects`). This ensures the API is fully operational with valid medical data immediately after deployment.