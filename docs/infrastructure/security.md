# Security Architecture & Protocols

This document outlines the security measures, cryptographic standards, and access control models implemented in the Health Without Borders API. The system follows a "Defense in Depth" strategy, securing Protected Health Information (PHI) and Personally Identifiable Information (PII) at the application, transport, and storage levels.

---

## 1. Authentication (AuthN)

Authentication uses the OAuth2 Password Flow with JSON Web Tokens (JWT). The system implements a **short-lived access token + long-lived refresh token** pattern with JTI-based revocation.

- **Signing Algorithm:** HS256 (HMAC with SHA-256) using a high-entropy `SECRET_KEY` injected at runtime.
- **Access Token:** Short-lived (default 60 minutes, configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`). Used for every API call. Contains `sub` (the user's id; tokens issued before September 2026 carry the email), `iat` (issue time, the server's clock), `exp` (expiration), `jti` (unique JWT ID for revocation), `tv` (the user's `token_version`) and `type` (`"access"`). No PHI or PII in the token.
- **Refresh Token:** Longer-lived (default 7 days, configurable via `REFRESH_TOKEN_EXPIRE_MINUTES`). Used **only** to obtain a new token pair via `POST /login/refresh`. Contains the same claims with `type` set to `"refresh"`. The old refresh token is revoked on each rotation to prevent reuse. A rotated token presented again means a copy exists, and every session of the user is revoked — except a retry after a lost response: within `REFRESH_RETRY_GRACE_SECONDS` (default 120 s) of the rotation, and while the token issued in its place is still unused, the retry gets a new pair and that unused token is revoked instead, so only one refresh token of the chain stays valid. Revocation is a single `INSERT … ON CONFLICT DO NOTHING`, so simultaneous refreshes or logouts with the same token never fail with a 500.
- **Token Revocation:** `POST /logout` revokes the access token in the `Authorization` header and, when the client sends it in the body, the refresh token too (otherwise the refresh token stays valid and can mint new access tokens). The `jti` claim is stored in the `revoked_tokens` database table and checked on every authenticated request. Expired entries are purged on each refresh and logout.
- **Ending Every Session of a User:** each token carries the user's `token_version` (`tv`), and a token whose `tv` is not the current one is rejected. Bumping it ends every session of that user on every device at once: `POST /users/{id}/revoke-sessions` (lost or stolen device), deactivating the user or their organization, a password change or reset, and a reused refresh token. Reactivating an account does not revive its old tokens. Every authenticated request, login and refresh also checks that the user and their organization are active (`403` on requests, `401` on login and refresh, with `code` `user_inactive` or `organization_inactive`). The runbook for a stolen device is in [NFC Key Management](nfc-key-management.md) § 4.
- **Password Storage:** Bcrypt iterative hashing. Plaintext passwords are never stored.
- **Password Changes:** A user changes their own password with `POST /users/me/password` (the current one is required; the new one needs at least 12 characters, at most 72 bytes, and is checked against a list of common passwords; attempts share the login rate limit). A wrong current password and a new one equal to it answer `400` with `code` `current_password_incorrect` and `password_unchanged`. Every other session of the user ends; the device that made the change gets a new token pair. An administrator (`superadmin`, or the `org_admin` for their own doctors and nurses) resets a password with `POST /users/{id}/reset-password`: the server generates a temporary password, returns it once, ends every session, and sets `must_change_password`, which the login response and `/users/me` report. Accounts created by an administrator start with the flag set. Neither password ever reaches the logs.

### 1.1. Token Flow

```
1. POST /login/access-token  →  { access_token, refresh_token, token_type, expires_in,
                                  nfc_keyring, nfc_key_version, nfc_encryption_key,
                                  must_change_password }
2. Use access_token in Authorization: Bearer header for all API calls
3. When access_token expires (401) → POST /login/refresh { refresh_token }
   → Returns a new pair and the NFC keyring (old refresh is revoked)
4. POST /logout { refresh_token }  →  Revokes the access token and, if sent,
   the refresh token (204 No Content)
```

---

## 2. Authorization (AuthZ) & Multi-Tenancy

Role-Based Access Control (RBAC). Tenant isolation applies to **organization and user administration**; patient clinical records are intentionally **global** — any authenticated clinician may access any patient (see Database Schema § 3.1).

### 2.1. System Roles

| Role | Scope | Capabilities |
|---|---|---|
| `superadmin` | Global | Create and deactivate organizations; create users of any role in any organization; deactivate, reset the password of, revoke the sessions of and delete any non-`superadmin` user; manage the NFC keyring (`/patients/nfc-keys`, rotate, revoke); read the whole patient access log and statistics. **No clinical access**: cannot read patient records and receives no NFC keys |
| `org_admin` | Organization | Create, deactivate, reset the password of, revoke the sessions of and delete the `doctor` and `nurse` accounts of their organization; read patient records (`/scan`, `/search`, used for the lost-bracelet flow) and receive the NFC keyring; read their organization's part of the access log and statistics, and the NFC key-version usage (all organizations: it holds no patient data). Cannot create or edit records (`/sync`) |
| `doctor` | Organization | Full clinical access: read records, create patients, add medical history |
| `nurse` | Organization | Read records; add vaccines and change allergies, background, guardians and demographics. Cannot add visits (consultations) |

### 2.2. Minors: the Guardian's Card (Hardware 2FA)

For physical security in refugee or transit camps, the bracelet scan
(`POST /patients/scan`) applies this rule:

- **Adults (18+):** Scanning the bracelet returns the medical record.
- **Minors (<18), and patients with no recorded birth date:** the scan is refused (`403`, `code` `guardian_required` or `guardian_mismatch`) unless the card of one of the two registered guardians is scanned too and matches.

The rule covers the scan only. So that care is never blocked, two paths reach a
minor's record without the card, and both leave a trace (§ 3.4):
`POST /patients/search` (lookup by document, for a lost bracelet) and the app's
offline emergency access (break-glass), uploaded later to `emergency_access_log`.

A retired patient or guardian device (lost, damaged or replaced) is refused online
with `410 device_retired`. Offline, the app reads the devices without the server;
`GET /patients/retired-devices` gives it the list of retired devices to check
them against (hashes of the UIDs, downloaded on each sync; see
[Database](database.md) § 3.1).

---

## 3. Data & Clinical Security

### 3.1. Encryption at Rest
- PostgreSQL on Google Cloud SQL encrypted with AES-256 (Google-managed keys).
- NFC chip payloads encrypted with AES-256-GCM using a versioned keyring. See [NFC Key Management](nfc-key-management.md) for configuration, rotation and retirement.
- Cloud SQL backups are encrypted the same way. Backups are not on by default: enable them when creating the instance (see [GCP Deployment](gcp-deploy.md) § 2). On the pilot instance, daily backups (14 kept), point-in-time recovery (7 days) and deletion protection are on since 2026-10-02. A database backup is only usable together with a backup of `NFC_KEK` (see [Database](database.md) § 2.5).

### 3.2. Encryption in Transit
- **External:** HTTPS between mobile clients and Cloud Run. Cloud Run negotiates the TLS version with the client: the pilot accepts TLS 1.2 and 1.3 (checked 2026-10-15), and nothing in the service restricts it to 1.3. Browsers are told to use HTTPS only (§ 4.4).
- **Internal:** the Cloud SQL connector's Unix socket (`/cloudsql/<instance>`) between Cloud Run and Cloud SQL, over the private VPC.

### 3.3. Clinical Interoperability Security (FHIR)
FHIR RDA bundles sent to the Google Cloud Healthcare API are protected by:
- **IAM Service Accounts:** The service calls the Healthcare API as its Cloud Run service account. **The pilot deployment uses the Compute Engine default account**, which holds `roles/editor` on the whole project (and reads the four secrets), so its reach is much wider than FHIR; the database user is `postgres`. The least-privilege setup is a dedicated account with `roles/healthcare.fhirResourceEditor` on the FHIR store, `roles/cloudsql.client`, `roles/aiplatform.user` and `roles/secretmanager.secretAccessor` on the four secrets, deployed with `--service-account` (see [Healthcare API](healthcare-api.md) § 4).
- **Cloud Audit Logs:** Admin Activity logs (always on) record changes to the dataset and the FHIR store. Reading and writing FHIR resources — every bundle the service sends — falls under Data Access logs (`DATA_READ`, `DATA_WRITE`), which Google leaves off by default. **They are off on the pilot**: the project has no audit configuration and its logs hold no Data Access entries for the Healthcare API (checked 2026-10-15). To record bundle writes, enable Data Write for Cloud Healthcare API under IAM & Admin → Audit Logs; the entries count toward Cloud Logging volume. Until then, who synced which record — the action that sends the bundle — is in `patient_access_log` (§ 3.4).
- **Referential Integrity:** Enabled on the FHIR Store to prevent malformed references.
- **Resource Versioning:** Enabled to maintain a complete history of every resource. Both are on in the pilot store (checked 2026-10-15).

### 3.4. Access to Patient Records
Patient records are global: any `doctor`, `nurse` or `org_admin` can read any record, and `/search` returns a minor's record without the guardian's card. Every successful `/scan`, `/search` and `/sync` therefore writes one row to the append-only `patient_access_log` table **before** the record is returned: the authenticated actor, their organization, the channel, whether the guardian's card was presented and matched, the reason given on `/search` (`access_reason`, optional) and the server time. If that row cannot be written, the record is not served.

`POST /api/v1/patients/access-log` returns that history for one patient (by server `patient_id` or current bracelet `device_uid`, in the body): all of it for a `superadmin`, the accesses made by their own organization's users for an `org_admin`.

Offline break-glass accesses (a minor's record opened on the device without the guardian) are synced by the app to `emergency_access_log`, which stores the device-declared actor next to the authenticated user whose session uploaded the entry (`uploaded_by`). `occurred_at` must be ISO 8601; a value without an offset is read as local time in the reporting zone, and it is stored with its offset. `POST /api/v1/patients/access-log` returns these entries too (`emergency_entries`, for the patient's current and retired bracelets, scoped like the rest of the ledger).

Logs never carry a bracelet or guardian-card UID or a document number, not even in part: they show a reference (`ref:` plus 10 hex characters of an HMAC keyed with `SECRET_KEY`), the same for every line about the same value. At startup, the log warns when `SECRET_KEY` is shorter than 32 bytes or is the example value from `.env.example` (never showing the key).

Use `POST /api/v1/patients/scan` (UIDs in the body) rather than `GET /api/v1/patients/scan/{device_uid}`: a bracelet UID in the URL ends up in Cloud Run's request logs.

---

## 4. Infrastructure Security

### 4.1. Network Isolation
- Cloud SQL has no public IP — accessed via internal VPC routing or Unix sockets. On the pilot instance it has a private address only (checked 2026-10-15); seeding the first `superadmin` opens a public one temporarily (see [GCP Deployment](gcp-deploy.md) § 8).
- Cloud Run instances are ephemeral (no persistent attack surface).

### 4.2. Secret Management
- Sensitive configurations injected at runtime via Google Secret Manager: `hwb-db-pass` (`DB_PASS`), `hwb-secret-key` (`SECRET_KEY`) and `nfc-kek` (`NFC_KEK`), mounted as `:latest` by `cloudbuild.yaml`. `hwb-nfc-master-key` (`NFC_MASTER_KEY`) holds version 0 of the NFC keyring in clear; it was mounted only for the first import into the database and is no longer in the service's environment ([NFC Key Management](nfc-key-management.md) § 4).
- Cloud Run reads `:latest` every time an instance starts, so a new version of one of these secrets reaches every instance started afterwards, without a deploy. Never add a version to rotate one of them in place: for `SECRET_KEY`, instances would disagree on token signatures until all of them restart; for the NFC secrets, see [NFC Key Management](nfc-key-management.md) § 6 and § 10.
- No secrets committed to the Git repository.

### 4.3. Rate Limiting

API endpoints that are vulnerable to brute-force attacks (`/login/access-token`, `/users/me/password`, `/patients/search`) enforce per-client request limits using [SlowAPI](https://github.com/laurents/slowapi).

- **Backend Storage:** Redis when `REDIS_URL` is set, shared across all Cloud Run instances. **The current deployment does not set it**, so each instance counts in its own memory (the effective limit is multiplied by the number of instances and resets on cold start). Redis is not provisioned on purpose: the per-account limit below, kept in PostgreSQL, is what stops guessing an account's password from many addresses or instances.
- **Client IP Detection:** Read from the `X-Forwarded-For` header, but never from its leftmost entries, which the client controls. With `TRUSTED_PROXY_HOPS` trusted proxies in front of the service (default `1`: Cloud Run's own front end), the client is the n-th entry from the right. A deployment behind one more proxy (a load balancer in front of Cloud Run, nginx) must raise it to match; otherwise every client behind that proxy shares one limit.
- **Default Limits:** `10/minute` for login and for password changes (each endpoint counts separately), `30/minute` for patient search. Configurable via `RATE_LIMIT_LOGIN` and `RATE_LIMIT_PATIENT_SEARCH` environment variables.

**Per-account limit on failed sign-ins.** Independent of the address and of the instance: failures are counted per account in PostgreSQL (`login_failures`, shared by every instance, no Redis needed).

- After `LOGIN_FAILURES_BEFORE_DELAY` (5) failures within `LOGIN_FAILURE_WINDOW_SECONDS` (15 min), the account is **paused** for `LOGIN_DELAY_BASE_SECONDS` (60 s). Each further failure doubles the pause, up to `LOGIN_DELAY_MAX_SECONDS` (15 min).
- While paused, every attempt gets `429` with `code: login_paused` and `Retry-After`, even with the right password, so the guessing cannot go on. A refused attempt is not counted, so it does not lengthen the pause.
- Each attempt is counted **before** its password is checked, under a lock on the account's row. A burst of simultaneous guesses, from any number of addresses or instances, therefore gets at most 5 evaluations; the rest get `429` without their password being checked.
- A wrong current password in `POST /users/me/password` counts the same way.
- A successful sign-in, or an administrator's reset (`POST /users/{id}/reset-password`), clears the count.
- **Trade-off:** someone who knows a user's email can delay that user's sign-in by up to 15 minutes at a time, but never lock the account for good. That is why it is a pause and not a lockout.
- The table stores an HMAC of the email, never the email itself (not even for accounts that do not exist), and an unknown email is paused exactly like a real one, so the limit reveals nothing about which accounts exist.

### 4.4. HTTP Hardening

- **Security headers on every response:** `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; plus `Cache-Control: no-store` on every response under `/api/v1` (patient records, tokens, the NFC keyring) unless the endpoint sets its own. There is no `Content-Security-Policy`: it would break the Swagger UI at `/docs`, which stays public because the project is open source.
- **Request body limits**, enforced before any body is parsed: 16 KiB on `/api/v1/login*`, 10 MiB on every other route. A larger body gets `413`.
- **Errors carry no data:** an unexpected error answers `500` with only an `error_id`, and the log line records the exception type, never its message (database errors embed PHI). A `422` keeps the type, location and message of each validation error but drops the echoed input.

---

## 5. Data Classification & Flow

### 5.1. Data Classification

| Category | Examples |
|---|---|
| Authentication (Sensitive) | Emails, hashed passwords, roles, organization IDs |
| PII | Patient names, dates of birth, guardian names, document numbers |
| PHI (Critical) | Patient IDs, device UIDs, medical history, diagnoses (ICD-10/11), allergies, vaccinations |

### 5.2. Data Flow

1. **Origin:** Tablet generates JSON payload (offline-first).
2. **Transit 1:** Tablet → Backend via HTTPS (TLS 1.2 or 1.3).
3. **Processing:** Cloud Run validates JWT/RBAC. Missing diagnoses inferred via Vertex AI (data NOT used for model training).
4. **Transit 2:** Backend → PostgreSQL via Unix Sockets.
5. **Transit 3:** Backend converts JSON to FHIR R4 RDA bundles → Cloud Healthcare API via HTTPS (GCP IAM).

```mermaid
flowchart TD
    classDef mobile fill:#1565c0,stroke:#0d47a1,stroke-width:2px,color:white;
    classDef backend fill:#2e7d32,stroke:#1b5e20,stroke-width:2px,color:white;
    classDef database fill:#e65100,stroke:#bf360c,stroke-width:2px,color:white;
    classDef healthcare fill:#6a1b9a,stroke:#4a148c,stroke-width:2px,color:white;
    classDef ai fill:#00838f,stroke:#006064,stroke-width:2px,color:white;

    subgraph FieldZone ["Field Operations (Untrusted Network)"]
        style FieldZone fill:none,stroke:#ccc,stroke-width:1px,color:#ccc,stroke-dasharray: 5 5
        Tablet["Mobile App (Offline-First)"]:::mobile
    end

    subgraph GCPZone ["Google Cloud Platform (Private VPC)"]
        style GCPZone fill:none,stroke:#ccc,stroke-width:1px,color:#ccc,stroke-dasharray: 5 5
        API["FastAPI Backend (Cloud Run)"]:::backend
        DB[("PostgreSQL (Cloud SQL)")]:::database
        VertexAI["Vertex AI (Gemini)"]:::ai
    end

    subgraph FHIRZone ["FHIR Interoperability Layer"]
        style FHIRZone fill:none,stroke:#ccc,stroke-width:1px,color:#ccc,stroke-dasharray: 5 5
        FHIRStore[("FHIR R4 Store (Healthcare API)")]:::healthcare
    end

    Tablet -- "Sync JSON via HTTPS (TLS)" --> API
    API -- "Extract diagnoses (internal API)" --> VertexAI
    API -- "Read/Write (Unix Sockets)" --> DB
    API -- "Push RDA Bundles (GCP IAM)" --> FHIRStore
```