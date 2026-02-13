# Database Architecture & Schema

This document details the data persistence layer for the **UNICEF Border Health Backend**. The system utilizes a relational database model optimized for transactional integrity, interoperability with health standards (HL7/FHIR), and synchronization with offline mobile clients.

## 1. Technology Stack

* **Engine:** PostgreSQL 15 (Managed via Google Cloud SQL).
* **ORM (Object-Relational Mapping):** SQLAlchemy 2.0 (Python). It use SQLAlchemy to manipule the database like simples variables of Python.
* **Driver:** `psycopg2-binary` for standard connections and Unix Socket integration.
* **Migration Strategy:** Schema initialization is currently handled via idempotent Python scripts (`scripts/create_tables.py`).

---

## 2. Data Dictionary

### 2.1. Authentication & Authorization (`users`)

This table manages access credentials for medical staff and administrators.

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | Integer | PK, Auto-increment | Internal unique identifier. |
| `email` | Varchar | Unique, Not Null, Index | The user's login username. |
| `hashed_password` | Varchar | Not Null | Bcrypt hash of the password. |
| `full_name` | Varchar | Nullable | Human-readable name of the staff member. |
| `role` | Varchar | Default: 'doctor' | RBAC role (`admin`, `doctor`, `coordinator`). |
| `is_active` | Boolean | Default: True | Used for soft-deleting users without removing audit history. |

### 2.2. Patient Demographics (`patients`)

Stores the core identity data of the migrant children. The design prioritizes minimal necessary data to protect privacy while ensuring continuity of care.

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | Varchar | PK | Unique ID (National ID, PPT, or generated UUID). |
| `first_name` | Varchar | Not Null, Index | Standardized first name (ILIKE searchable). |
| `last_name` | Varchar | Not Null, Index | Standardized last name (ILIKE searchable). |
| `dob` | Date | Not Null | Date of Birth for age calculation and vaccine schedules. |
| `gender` | Varchar | Not Null | Biological sex (M/F) required for clinical charts. |
| `blood_type` | Varchar | Nullable | Optional blood group (e.g., O+, A-). |
| `guardian_info` | JSONB | Nullable | Contact details of parents or guardians. |

### 2.3. Standard Catalogs

To ensure interoperability with systems like **Google Cloud Healthcare API (HL7v2)**, the system does not store free-text diagnoses or vaccine names. Instead, it references international standard catalogs.

#### Vaccines Catalog (`vaccines_catalog`)

Based on the **CVX** (Code for Vaccine Administered) standard.

* **`code` (PK):** The numeric CVX code (e.g., `90707` for MMR).
* **`name`:** The official descriptive name.
* **`is_active`:** A boolean flag. If `False`, the vaccine is hidden from the "New Record" forms in the frontend but remains visible in historical data to maintain integrity.



#### Diagnosis Catalog (`diagnoses_catalog`)

Based on the **ICD-10** (International Classification of Diseases) standard.

* **`code` (PK):** The alphanumeric code (e.g., `A09.9`).
* **`description`:** The official Spanish translation of the disease.
* **`is_active`:** Boolean flag for deprecating obsolete codes.

---

## 3. Design Decisions

### 3.1. Soft Deletes (`is_active`)

Rows in critical tables (Users, Catalogs) are rarely physically deleted (`DELETE FROM`). Instead, an `is_active` boolean column is used.

* **Rationale:** This preserves historical integrity. For example, if a vaccine is discontinued, historical records of children who received it remain valid, but it cannot be selected for new patients.

### 3.2. JSONB for Flexible Fields

PostgreSQL's `JSONB` data type is utilized for fields like `guardian_info` and `address`.

* **Rationale:** Migrant populations often have unstructured or transient address formats (e.g., shelters, temporary camps) that do not fit into rigid `Street/City/Zip` columns.

### 3.3. Indexing Strategy

* **Search Optimization:** B-Tree indexes are applied to `patients.last_name` and `patients.first_name` to support the offline/online search functionality described in the operational guidelines.
* **Uniqueness:** Unique constraints are enforced on `users.email` and `patients.id` to prevent duplication.

---

## 4. Initialization and Seeding

The database schema is initialized using Python scripts located in the root directory.

1. **`scritps/create_tables.py`**: Uses SQLAlchemy `Base.metadata.create_all(bind=engine)` to generate the schema if it does not exist.
2. **`scripts/load_catalogs.py`**: Parsers a JSON dataset of ICD-10 and CVX codes and efficiently inserts them using bulk operations (`bulk_save_objects`). This ensures the system is production-ready with valid medical data immediately after deployment.

```