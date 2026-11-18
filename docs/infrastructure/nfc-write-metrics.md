# NFC Write Metrics

How big the app's NFC writes are against the chips they go to: what the app
reports, what the backend keeps, and how to read it before choosing a payload
format.

---

## 1. Why

The app writes an encrypted payload to every chip it touches. The patient
device gets a triage summary; the guardian device gets a copy of the record,
bounded to the most recent consultations and vaccines and trimmed further until
it fits the chip. Today every payload is format 1: CBOR, compressed with
DEFLATE, encrypted with AES-256-GCM (wire format in
[NFC Key Management](nfc-key-management.md) § 2). A format 2 is being designed.

Choosing between them needs three numbers that only the field has: how big the
writes are, how close they come to each chip's capacity, and how often the
guardian copy has to leave history out to fit. A synthetic corpus can estimate
them; this telemetry measures them.

---

## 2. What the app reports

`POST /api/v1/patients/nfc-write-metrics`, one entry per chip write, up to
5,000 entries per request:

| Field | Type and unit | Meaning |
|---|---|---|
| `device_role` | `"patient"` or `"guardian"`; default `"patient"` | Which device was written. Any other value is stored as `patient`, so a future role never drops a batch. |
| `payload_format` | integer, 1–255 | 1 = CBOR + DEFLATE + AES-GCM (today's); 2 = the format under development. |
| `key_version` | integer, 0–255 | NFC key version the payload was encrypted with. |
| `payload_bytes` | integer, 0–65,535 bytes | The encrypted payload. |
| `message_bytes` | integer, 0–65,535 bytes | The whole NDEF message written. |
| `chip_capacity` | integer, 0–65,535 bytes | The NDEF capacity the chip reported. |
| `consultations_kept`, `consultations_total` | integer, 0–1,000, or null | Guardian copy only: consultations written to the chip, out of how many the copy would carry on a chip with room for everything (the most recent, at most three). Null for a patient device. |
| `vaccines_kept`, `vaccines_total` | integer, 0–1,000, or null | The same, for vaccines. |
| `written_at` | date-time **with an offset** | The device's clock at write time. A value without an offset is refused (`422`): read as UTC, it would shift the write by the device's own offset. |

Allowed roles: `doctor`, `nurse` and `org_admin` (an `org_admin` device that
signed in before that role stopped receiving keys keeps its ring until its
session ends). The answer is `202` with `received` and `stored`; a value out of
range, an empty batch or a missing field is a `422` for the whole batch.

**Not idempotent, by design.** Every write is a sample and is appended. Nothing
in an entry identifies the chip or the write (§ 3), so there is nothing to
de-duplicate on, and two writes of the same size are two samples. A batch sent
twice counts twice.

**Entries that contradict themselves are dropped**, not stored: a message
smaller than its payload, a message larger than the chip's capacity, a copy
that kept more consultations or vaccines than its total, or a kept count
without its total (or the reverse). No real write produces one; it can only
come from a measuring bug, and storing it would bend the sizes everyone reads.
The batch is still accepted (`stored` < `received`) so the device does not
retry it, and the backend logs one warning per batch with the counts by reason,
never the values:

```
Dropped 3 NFC write metrics that contradict themselves (kept_over_total=1, message_over_capacity=2) reporter_id=… app_version=1.2.3+45 (android)
```

A capacity of 0 is therefore consistent only with an empty message: any
message larger than 0 bytes is larger than the chip.

---

## 3. What is not collected

- **No chip UID**, not even as an HMAC as the
  [key-version sightings](nfc-key-management.md#telemetry-storage) keep it. The
  question is about sizes, not chips: writes to one chip cannot be told apart
  from writes to several.
- **No patient identifier and no clinical content.** The consultation and
  vaccine counts say how much history a guardian copy carried, not what it
  said.
- The request schema has no field for any of these, and a field outside the
  schema is ignored (pydantic's default, as for the sightings): a `device_uid`
  or a `patient_id` sent by mistake never reaches the service, and the table
  `nfc_write_metrics` has no column for them
  ([Database Schema](database.md) § 2.7).

The server adds four values from the request, not from the entry:

| Column | Source |
|---|---|
| `reported_by` | The authenticated user. |
| `organization_id` | That user's organization; it limits an `org_admin`'s summary (§ 5). |
| `app_version` | The `X-App-Version` header, read as login reads it: `none` when absent, `invalid` when it does not match the format. |
| `reported_at` | The server's clock when the batch arrived. |

The reporter and the time remain, as in the sightings: a row says which staff
member wrote a chip of which kind, and when. Matched against the patient access
log, a row could point at the record that had just been synced, which tells
nothing the record itself does not hold. It is still one more reason the rows
do not outlive the retention period.

---

## 4. Retention

Rows older than `NFC_WRITE_METRIC_RETENTION_DAYS` (365 days by default) are
deleted whenever a batch is stored. Age is counted by `reported_at`, the
server's clock: a write made long ago but reported today is kept a full period.
A payload-format decision needs months of writes, not a history.

The setting sits next to `NFC_KEY_SIGHTING_RETENTION_DAYS` in
`app/core/config.py` and `.env.example`.

---

## 5. Reading the summary

```
GET /api/v1/patients/nfc-write-metrics/summary?window_days=90
```

| Role | Sees |
|---|---|
| `superadmin` | Every organization. |
| `org_admin` | The writes reported by users of their own organization, as `/stats/overview` scopes it. |
| `doctor`, `nurse` | `403`. |

`window_days` goes from 1 to 730 (default 90). Rows older than the retention
period are already deleted, so a longer window reads the same as that period.

**The window is on `written_at`**, the device's clock, as the key-version usage
window is on `observed_at`: a brigade that was offline for a week reports its
writes late, and they still count on the days they were made. A device with a
skewed clock can move a write in or out of the window; it cannot change its
size.

Writes are grouped by `device_role`, `payload_format` and `chip_capacity`, and
the groups are sorted in that order. Each group reports:

| Field | Unit | Meaning |
|---|---|---|
| `writes` | writes | How many writes the group holds. |
| `message_bytes_median` | bytes | Median NDEF message size (nearest rank, below). |
| `message_bytes_p90` | bytes | 90th percentile of the message size (nearest rank). |
| `message_bytes_max` | bytes | Largest message. |
| `utilization_median_pct` | % of `chip_capacity`, one decimal | Median of `message_bytes / chip_capacity × 100`; null when the chips reported a capacity of 0. |
| `trimmed_writes` | writes | Writes where `consultations_kept < consultations_total` or `vaccines_kept < vaccines_total`. |
| `consultations_dropped` | consultations | Sum of `consultations_total − consultations_kept`. |
| `vaccines_dropped` | vaccines | Sum of `vaccines_total − vaccines_kept`. |

**Nearest rank.** The median and the p90 are the elements at index
`int(q × (n − 1))` of the sorted sizes, with q = 0.5 and 0.9: always a size some
write really had. With an even number of writes the median is the lower of the
two middle ones; with two writes, both are the smaller one. Read small groups
with that in mind.

**What counts as trimmed.** A guardian copy carries at most the three most
recent consultations and the three most recent vaccines
(health-without-borders-frontend, `nfc_guardian_payload.dart`), whatever the
chip. The app reports as `*_total` what the copy would carry on a chip with
room for everything — the record's count, capped at three — not the record's
whole count. So `kept < total` means the chip's capacity left history out, and
nothing else: a patient with five consultations whose copy kept its three most
recent is not trimmed.

---

## 6. How it informs the payload-format decision

1. **Does format 1 fit today's chips with room to spare?** For each `patient`
   group, compare `message_bytes_p90` and `message_bytes_max` with
   `chip_capacity`; `utilization_median_pct` is the typical fill. A p90 close
   to the capacity means the next field added to the triage payload will not
   fit on those chips.
2. **How much history do guardian copies lose?** `trimmed_writes / writes` is
   the share of guardian writes that left something out, and
   `consultations_dropped` and `vaccines_dropped` say how much (§ 5 for what
   counts as trimmed).
3. **What does format 2 buy?** Once builds that write it are in the field,
   compare its groups with the format 1 groups of the same `device_role` and
   `chip_capacity`: median and p90 sizes, fill and trimming. Same role, same
   capacity and a different format is the comparison that isolates the format.
4. **Which builds write which format?** The summary does not split by build;
   the rows do. A reader that predates format 2 cannot open what it writes, so
   format 2 can only become the one written once every device that reads chips
   understands it: readers first, as with the keyring
   ([NFC Key Management](nfc-key-management.md#which-build-each-device-runs)).
   On the database:

   ```sql
   SELECT app_version, device_role, payload_format, count(*) AS writes
   FROM nfc_write_metrics
   WHERE reported_at > now() - interval '30 days'
   GROUP BY app_version, device_role, payload_format
   ORDER BY app_version, device_role, payload_format;
   ```

**Limits.** Only builds that report write metrics appear here, and
`app_version` says which ones do. The sizes are measured by the app: the
consistency checks drop impossible entries, but they cannot catch a measurement
that is wrong in a plausible way.
