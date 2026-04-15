# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Health Without Borders seriously, especially given that the system handles Protected Health Information (PHI) of vulnerable populations including migrant children.

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via one of the following channels:

1. **GitHub Security Advisories:** Use the "Report a vulnerability" button on the [Security tab](https://github.com/guane-hwb/health-without-borders/security/advisories) of this repository.
2. **Email:** Send a detailed report to the project maintainers at the email listed in the repository contact information.

### What to include

- Description of the vulnerability and its potential impact.
- Steps to reproduce the issue.
- Any relevant logs, screenshots, or proof-of-concept code.
- Your recommended fix, if applicable.

### What to expect

- **Acknowledgment** within 48 hours.
- **Initial assessment** within 7 business days.
- **Priority patching** for critical vulnerabilities affecting PHI.

### Scope

In scope: authentication/authorization bypass, patient data exposure (PII/PHI), cross-tenant access, NFC 2FA bypass, FHIR bundle injection, LLM prompt injection affecting clinical data, infrastructure misconfigurations in GCP deployment templates.

Out of scope: third-party dependency vulnerabilities (report upstream), social engineering, DoS against development environments.

## Security Architecture

For details on security controls, see [docs/infrastructure/security.md](docs/infrastructure/security.md).