# Security, Quality, and Compliance Remediation Program

This document defines the execution model for the current remediation effort across security, code quality, architecture consistency, and ISO 27001 technical alignment.

## Scope and branch strategy

- Working branch: `feature/quality-compiance-test`
- Target branch for all pull requests: `develop`
- Delivery model: incremental phases
- Rule: one phase equals one documented commit

## Delivery phases

### Phase 0 - Baseline and governance

- Consolidate prioritized findings and risk levels.
- Define acceptance criteria by phase.
- Standardize commit and PR documentation.

### Phase 1 - Functional critical fixes (schema and HL7)

- Remove schema duplication in patient medical history.
- Align HL7 generation with current payload contracts.
- Update tests to match current authorization model.

### Phase 2 - Authentication and authorization hardening

- Remove unsafe default bootstrap credentials.
- Normalize login error behavior and status codes.
- Harden user validation paths.

### Phase 3 - PHI-safe observability

- Remove PHI/PII from operational logs.
- Keep traceability with safe identifiers.
- Reduce sensitive runtime exposure in health endpoints.

### Phase 4 - API perimeter controls

- Add explicit CORS policy per environment.
- Add rate limiting on authentication and sensitive endpoints.
- Review token lifetime policy for operational reality vs risk.

### Phase 5 - CI/CD quality gates

- Re-enable automated tests in deployment pipeline.
- Add linting, typing, and coverage checks.
- Enforce gate criteria before deployment.

### Phase 6 - Secure Cloud Run deployment without static keys

- Implement Workload Identity Federation for CI to GCP auth.
- Use least-privilege deployment service account.
- Keep zero static credential files in repository and CI secrets.

### Phase 7 - ISO 27001 technical evidence mapping

- Map implemented controls to technical evidence.
- Track residual gaps that require non-code governance actions.

## Definition of done per phase

Each phase is complete only when all these conditions are met:

1. Code and configuration changes are committed.
2. Relevant automated checks are executed and documented.
3. Security/quality rationale is documented.
4. Rollback guidance is included.
5. A PR to `develop` is opened with traceable evidence.

## Commit documentation template

Use this template in every commit message body:

```text
Context:
- What problem is being solved and why now.

Risk addressed:
- Security, quality, compliance, or functional risk reduced.

Technical decision:
- Main design choice and alternatives considered (if any).

Validation:
- Commands/tests run and outcome.

Rollback:
- How to revert safely if needed.
```

## PR documentation checklist

- Phase identifier (for example: Phase 2)
- Linked risks/issues addressed
- Files changed and impact summary
- Validation evidence (tests, lint, checks)
- Deployment impact (yes/no)
- Rollback notes
