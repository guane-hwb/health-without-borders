# DPG Compliance Checklist — Health Without Borders

The **Digital Public Goods (DPG) Standard** is the set of requirements a project must meet to be
recognized as a Digital Public Good by the [Digital Public Goods Alliance](https://digitalpublicgoods.net/).
It defines **nine indicators**; indicator 9 ("Do No Harm by Design") is broken into three
sub-indicators (**9a** data privacy & security, **9b** inappropriate & illegal content,
**9c** protection from harassment). This document is a **self-assessment** of Health Without Borders
against those indicators and is intended to be validated with the privacy & security mentor.

- **Reference:** <https://digitalpublicgoods.net/standard/>
- **Status legend:** ✅ Met · 🟡 Partial · 🔴 Gap

| #  | DPG Standard indicator | Status | Notes / action |
|----|------------------------|--------|----------------|
| 1  | Relevance to the SDGs (SDG 3 — Health) | ✅ Met | Clinical-records platform for medical brigades and migrant/pediatric patients; aligned with SDG 3. |
| 2  | Use of an approved open license | ✅ Met | OSI-approved license in the repository (`LICENSE`). |
| 3  | Clear ownership | ✅ Met | Owned and maintained by Guane Enterprises; stated in `README`. |
| 4  | Platform independence | 🟡 Partial | No mandatory proprietary dependencies; document alternatives to managed services (e.g. FHIR store). |
| 5  | Documentation | ✅ Met | Developer documentation (architecture, setup, deployment, security) and the new **User Guide** are both published on the docs site. |
| 6  | Mechanism for extracting non-PII data | 🟡 Partial | Aggregated statistics are exposed via API; document the non-PII data-export mechanism. |
| 7  | Adherence to privacy & applicable laws | ✅ Met | Compliant with Colombian regulation (Res. 1888/2025, RDA); encryption and access control in place. |
| 8  | Adherence to standards & best practices | ✅ Met | FHIR R4 / RDA IG; CI/CD, tests and PR workflow documented. |
| 9a | Do No Harm — Data Privacy & Security | ✅ Met | AES-256 encryption, role-based access control, emergency-access audit logging, offline security. |
| 9b | Do No Harm — Inappropriate & Illegal Content | 🟡 Partial | Minimal user-generated content; document a short content-handling policy. |
| 9c | Do No Harm — Protection from Harassment | ✅ Met | Code of Conduct in the repository; community spaces (GitHub Discussions) are moderated. |

## Action plan for open gaps
- [ ] Document the aggregated (non-PII) data-export mechanism. *(indicator 6)*
- [ ] Document platform-independence alternatives for managed services, e.g. the FHIR store. *(indicator 4)*
- [ ] Add a short content-handling policy for the community spaces. *(indicator 9b)*
- [ ] Validate this checklist with the privacy & security mentor, especially **indicator 7** (adherence to privacy & applicable laws) and **9a** (data privacy & security).
