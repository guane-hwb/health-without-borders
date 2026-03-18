# ISO 27001 Technical Control Mapping

This document maps implemented technical controls in the codebase to ISO/IEC 27001:2022 Annex A themes and identifies residual gaps that require organizational or operational evidence.

## Scope

- Backend API source code and configuration.
- CI/CD automation and deployment workflows.
- Runtime security controls visible in repository artifacts.

## Control-to-evidence mapping

### A.5 Organizational Controls (selected technical touchpoints)

- **A.5.15 Access control policy**
  - Evidence:
    - `app/api/deps.py` (token validation and active-user enforcement)
    - `app/api/v1/endpoints/*` (role checks for privileged actions)
  - Residual gap:
    - Formal policy approval and periodic review are not code-verifiable.

### A.8 Technological Controls

- **A.8.5 Secure authentication**
  - Evidence:
    - `app/api/v1/endpoints/login.py` (uniform auth failure path, inactive-user handling)
    - `app/core/security.py` (JWT generation, bcrypt password verification)
  - Residual gap:
    - MFA/step-up auth policy for admins remains procedural.

- **A.8.9 Configuration management**
  - Evidence:
    - `app/core/config.py` centralizes runtime security controls.
    - `.env.example` documents mandatory security variables.
  - Residual gap:
    - Production baseline hardening checklist enforcement outside code.

- **A.8.15 Logging**
  - Evidence:
    - `app/api/v1/endpoints/patients.py` logs sanitized actor/org references and masked identifiers.
    - `app/core/logging.py` central logging strategy.
  - Residual gap:
    - Central retention policy and SIEM routing must be configured in GCP.

- **A.8.16 Monitoring activities**
  - Evidence:
    - `app/main.py` health endpoint and operational logs.
  - Residual gap:
    - Alert thresholds, on-call escalation, and response SLAs are operational.

- **A.8.20 Network security**
  - Evidence:
    - `app/main.py` CORS allowlist support and explicit middleware.
    - `app/core/config.py` CORS settings by environment.
  - Residual gap:
    - Perimeter controls in Cloud Armor/VPC are infrastructure-level.

- **A.8.23 Web filtering and API abuse controls**
  - Evidence:
    - `app/core/rate_limit.py`
    - `app/api/v1/endpoints/login.py` and `app/api/v1/endpoints/patients.py` rate-limit decorators.
  - Residual gap:
    - Adaptive abuse detection and bot management are external controls.

- **A.8.24 Use of cryptography**
  - Evidence:
    - `app/core/security.py` JWT signing and bcrypt password hashing.
    - `docs/infrastructure/security.md` transport/storage encryption strategy.
  - Residual gap:
    - Key rotation procedures and KMS governance require platform evidence.

- **A.8.28 Secure coding**
  - Evidence:
    - `.github/workflows/ci.yml` quality gates (lint, scoped type-check, tests with coverage).
    - `cloudbuild.yaml` quality gate step before build/deploy.
  - Residual gap:
    - Threat modeling and secure code review sign-off process must be formalized.

### A.8.31 Separation of environments

- Evidence:
  - Branch/PR workflow documented in `docs/development/qa-plan.md`.
  - Deployment workflows target defined environments.
- Residual gap:
  - Formal release approvals and environment segregation audit logs are platform/process controls.

## Residual risk register (technical)

- Scoped type-check is currently limited to selected modules to keep CI stable while legacy typing debt is addressed.
- OpenAPI exposure strategy is environment-dependent and must be reviewed at deployment policy level.
- Token lifetime remains configurable and should be periodically reassessed against threat model and offline requirements.

## Audit evidence checklist

For each release cycle, keep:

1. CI run links proving lint/type-check/tests passed.
2. PR links with security and rollback notes.
3. Deployment logs (Cloud Run revision + actor identity).
4. Secret access policy snapshots for runtime service account.
5. Change approval references for production releases.
