# User Guide

This guide is for the **healthcare staff** who use the Health Without Borders mobile app in the field — during medical brigades and at pilot sites. It explains, step by step, how to register patients, read and update their records with NFC devices, and keep everything synchronized.

No technical knowledge is required. If you are looking for API, architecture, or deployment documentation, see the **Development** and **Infrastructure and Security** sections instead.

---

## What the app does

Health Without Borders stores a patient's clinical record on the patient's **NFC device** and keeps a synchronized copy on the server. Because brigades often work without a stable internet connection, the app is **offline-first**: you can register patients and record care with no signal, and the app will synchronize automatically when a connection is available.

A single patient has **one shared record** across all participating organizations, so any authenticated professional can read and update it at the point of care.

---

## Before you start

- A mobile device with **NFC enabled** (Android) and the app installed.
- Your **login credentials**, provided by your organization administrator.
- **Patient devices** (NFC) and, for minors, **guardian devices**.

**Note:** The interface is available in **Spanish and English**. You can switch the language at any time from the header (see [Getting Started](getting-started.md)).

---

## How this guide is organized

1. [Getting Started](getting-started.md) — Log in, understand the home screen, switch language, and sign out safely.
2. [Registering a New Patient](register-patient.md) — Create a patient, assign the patient device, and add guardians.
3. [Finding & Viewing a Patient](patient-records.md) — Search, scan a device, and read the clinical history.
4. [Consultations & Vaccines](consultations-vaccines.md) — Add care to a patient's record.
5. [Syncing Pending Records](sync-pending.md) — Understand and clear the sync queue.
6. [Statistics](statistics.md) — Review brigade activity (administrators).
7. [Administration](administration.md) — Manage users and organizations (administrators).
