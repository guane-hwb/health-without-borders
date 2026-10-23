# NFC Key Management

How the AES-256 keys that encrypt NFC chip payloads are configured, rotated and
retired.

---

## 1. What the keys protect

Every HWB chip — a patient bracelet or a guardian card — carries its clinical
payload encrypted with AES-256-GCM. The key comes from the backend at login and
is held by the device so it can read and write chips **offline**, which is the
whole point: brigades work without connectivity.

The chip is a **cache, not the source of truth**. Its hardware UID is
unencrypted and always readable, so a device with connectivity can always
resolve the patient from the backend even when it cannot decrypt the payload.

---

## 2. The keyring

There is not one key but a **keyring**: a map of `version -> key`. The backend
delivers the whole ring to `doctor` and `nurse` accounts at login, refresh,
`/users/me` and in the response to a password change
(`POST /users/me/password`). Devices decrypt with whichever
version a chip names and encrypt with the current one.

| Setting | Meaning |
|---|---|
| `NFC_MASTER_KEY` | The original key. Registered as **version 0**. With `NFC_KEK`, only read by the first-start import (§ 4). |
| `NFC_KEY_V<n>` | The key for version `n`. One environment variable per key. |
| `NFC_CURRENT_KEY_VERSION` | The version new writes are encrypted with. Defaults to `0`. |

Every key must be exactly **64 hexadecimal characters** (32 bytes) and every
version must be between **0 and 255** — the version travels in a single byte of
the payload header.

The application **refuses to start** if any key is malformed or if
`NFC_CURRENT_KEY_VERSION` has no matching key. The error names the offending
variable and never prints its value. Without `NFC_KEK`, a deployment with no
NFC key at all is valid; NFC is simply unavailable. With `NFC_KEK` it is not:
the first start needs `NFC_MASTER_KEY` and `NFC_BOOTSTRAP_IMPORT=true` (§ 4).

### Wire format

```
version 0 : [12-byte nonce][ciphertext][16-byte tag]
version 1+: ['H']['W'][version][nonce][ciphertext][tag]
            with the three header bytes authenticated as associated data
```

Version 0 deliberately writes the pre-versioning layout so an older build can
still read chips written by a newer one during a partial rollout.

---

## 3. Field history

Recorded so the state of chips already in circulation is not guesswork.

**Every chip written in the field to date is key version 0, in the headerless
format.** No build carrying the versioned wire format was distributed to any
device between 3 and 8 September 2026, so nothing was written with a version
header then, and no rotation has taken place since.

**Since 14 September 2026 the pilot keeps the ring in the database** (§ 4):
`cloudbuild.yaml` mounts `NFC_KEK` since that day's deploy, and the first
start with it imported `NFC_MASTER_KEY` as version 0. From then on
`NFC_CURRENT_KEY_VERSION` is ignored; the current version is the one in
`nfc_keyring_state`. As of
15 October 2026 every start of the service logs
`NFC keyring ready: versions [0], current 0.` — no version has been generated,
rotated or revoked (any of those would have created version 1), and
`NFC_AUTO_ROTATE` is not set, so automatic rotation is off.

Since October 2026 `cloudbuild.yaml` no longer mounts `NFC_MASTER_KEY`: the
deploy removes it (`--remove-secrets`), so version 0 exists in clear only in the
`hwb-nfc-master-key` secret, not in the environment of every instance.

That means the two formats never coexisted in the field, and the wire format is
frozen from the current build onwards. It also means version 0 is the only key
any existing chip can be read with. It now lives in `nfc_keys`, sealed under
the KEK: losing the KEK (§ 10) makes every chip in circulation unreadable
offline.

## 4. Where the ring lives

The ring can come from two places, and `GET /api/v1/patients/nfc-keys` reports
which one is in use.

**Without `NFC_KEK`** the ring is read from the environment, exactly as it
always was. Nothing changes on upgrade.

**With `NFC_KEK`** the ring lives in the `nfc_keys` table, with each key sealed
under the KEK (AES-256-GCM, version as associated data, `kek_id` recording which
KEK sealed each row). On the first start the environment's `NFC_MASTER_KEY`
is imported as version 0 and the table becomes the source of truth. That is
what makes revocation an API call instead of a redeploy.

The import needs `NFC_BOOTSTRAP_IMPORT=true`. An empty `nfc_keys` table is also
what a restore from a backup older than the KEK, or a table emptied by mistake,
looks like; importing then would make version 0 current again even if it had
been revoked after a leak, and drop every later version, with nothing said. So:

- **Empty table, flag not set:** the backend refuses to start and says why.
- **Empty table, but `nfc_key_events` has rows:** the ring existed and its rows
  are missing. The backend refuses to start even with the flag. Restore
  `nfc_keys` and `nfc_keyring_state` from a backup instead.
- **Table already filled, flag still set:** nothing is imported, and the start
  logs a warning to remove it.

Once imported, `NFC_MASTER_KEY` is not read again. Remove it and the flag from
the service; the pilot's `cloudbuild.yaml` does both on every deploy. Keep the
`hwb-nfc-master-key` secret's original value: it is the plaintext of version 0,
which every chip in the field is written with.

The trade is that the KEK then guards every version, version 0 included: losing
it costs offline reads across the whole fleet, not just for rotated chips.
**The KEK backup procedure is a precondition for setting `NFC_KEK`.**

The backend refuses to start if the KEK cannot unwrap the current version. A
wrong KEK would otherwise surface as devices quietly receiving an empty ring.

It also refuses to start when the table is empty and `NFC_MASTER_KEY` is not
set: nothing is imported, and there is no endpoint that creates the first
version. An organisation starting fresh with a KEK sets, for the first start
only, a new `NFC_MASTER_KEY` (`openssl rand -hex 32`, it becomes version 0) and
`NFC_BOOTSTRAP_IMPORT=true`. On Cloud Run, before the first deploy that mounts
`NFC_KEK`:

```bash
gcloud run services update <SERVICE> --region <REGION> \
    --update-secrets=NFC_MASTER_KEY=hwb-nfc-master-key:latest,NFC_KEK=nfc-kek:latest \
    --update-env-vars=NFC_BOOTSTRAP_IMPORT=true
```

Check that the start logs `Imported NFC_MASTER_KEY into the keyring as version
0.` and `NFC keyring ready: versions [0], current 0.`, then remove both
(`--remove-secrets=NFC_MASTER_KEY --remove-env-vars=NFC_BOOTSTRAP_IMPORT`, which
the next deploy from `cloudbuild.yaml` also does).

### Revoking a compromised key

```
POST /api/v1/patients/nfc-keys/revoke
{"version": 0, "reason": "...", "acknowledge_chip_impact": true}
```

`superadmin` only. The version stops being delivered for reading *and* writing —
anything less is useless against a leak, since the leaked key is the one that
reads. If it was the current version, a replacement is generated and becomes
current. Every operation is recorded in `nfc_key_events` with actor and reason.

**It is not instant on the device.** Revocation stops *delivery*: at once on
the instance that handled the request, within 60 seconds on the others (each
instance reuses the ring it read for that long). A device that already holds
the ring keeps it until its next refresh: up to an hour online, up to the
refresh-token window (7 days) offline. There is no way to reach an offline
device sooner.

**Chips on the revoked version become online-only** until rewritten. No data is
lost: the UID resolves the patient through the backend and the next save
migrates the chip.

**Before the current version advances past 0**, every device must run a
keyring-aware build — the same rule as for rotation. `acknowledge_chip_impact`
exists so this is deliberate.

**If the database cannot be read** during a login or refresh, the backend
serves the last ring it read from the database, or no keys at all (the app then
keeps the ring it already has). It never falls back to `NFC_MASTER_KEY`: after
revoking version 0 that variable still holds the revoked key.

### Compromised device (lost or stolen tablet)

Revoking a key alone does **not** cut the stolen device: its refresh token stays
valid for up to 7 days and would receive the replacement key. In this order:

1. **Revoke the user's sessions:** `POST /api/v1/users/{id}/revoke-sessions`
   (`superadmin`; an `org_admin` only for the doctors and nurses of their own
   organization, so a lost `org_admin` tablet needs a `superadmin`; nobody can
   do it on their own account). Every access and refresh token issued to that
   user stops working at once, on every
   device; the account stays active and the person signs in again on a trusted
   device. Deactivating the account (`PATCH /users/{id}` with
   `{"is_active": false}`) has the same effect on sessions, and reactivating it
   later does **not** bring them back. The stolen device wipes its keyring as
   soon as it is online and its refresh is refused; while it stays offline it
   keeps the ring until its refresh token expires (up to 7 days).
2. **Revoke the key version** the device held, as above.
3. **Change the password** if it may have been exposed:
   `POST /api/v1/users/{id}/reset-password` (same roles and limits as step 1)
   returns a new temporary password once and also ends every session, so it can replace
   step 1. Give it to the user, who replaces it with their own
   (`POST /api/v1/users/me/password`).

If the whole organization is compromised, a `superadmin` deactivates the
organization: that blocks all its users at once and revokes all their sessions.

A refresh token presented twice (the device's copy and a stolen copy) is
treated the same way: the second use revokes every session of that user.

## 5. Generating a key

```bash
openssl rand -hex 32
```

Store it in Secret Manager. **Never** commit a real key, and never paste one
into an issue or a chat.

---

## 6. Rotating

Rotation limits how long a leaked key stays useful. It does **not** re-encrypt
existing chips: those migrate when they are next written.

The four steps below are for the environment mode (no `NFC_KEK`). **They have
no effect on the pilot**, which keeps the ring in the database (§ 3): the
backend would only log a warning. There, use
[Rotating with the keyring in the database](#rotating-with-the-keyring-in-the-database).

1. Generate a new key and create a **new** secret, e.g. `hwb-nfc-key-v1`.
2. Mount it in `cloudbuild.yaml` under `--update-secrets` as `NFC_KEY_V1`.
3. Set `NFC_CURRENT_KEY_VERSION=1` under `--update-env-vars`.
4. Deploy. New writes use version 1; version 0 stays in the ring for reading.

> **Never add a new version to an existing secret.** `NFC_MASTER_KEY` is mounted
> as `hwb-nfc-master-key:latest`, and Cloud Run reads `:latest` every time an
> instance starts — no deploy needed. In environment mode, adding a Secret
> Manager version to that secret silently changes what version 0 is, and
> **every existing chip becomes unreadable**. (With a KEK the damage is
> narrower, see § 4; the KEK's own secret has the same trap, see § 10.)
> Rotation always means a new secret with a new name.

> **Do not set `NFC_CURRENT_KEY_VERSION` above `0` until every device runs a
> build that understands the keyring.** An older build takes the current key and
> ignores the ring, losing the ability to read anything written under the old
> key.

### Rotating with the keyring in the database

Once `NFC_KEK` is configured, rotation no longer needs a redeploy.

**Manually:**

```
POST /api/v1/patients/nfc-keys/rotate
{"reason": "...", "acknowledge_fleet_updated": true}
```

`superadmin` only. A new version is generated and becomes current. Older
versions keep being delivered, so chips written under them stay readable
offline and migrate as they are rewritten.

**Automatically:** set `NFC_AUTO_ROTATE=true` and the current key is replaced
once it reaches `NFC_ROTATION_PERIOD_DAYS` (default 90). The check runs when the
service starts and on the request path (login, refresh, `/users/me`), not on a
schedule — the project has no scheduler, and an adopting organisation should
not have to stand one up for key rotation to happen. It is evaluated at most
once a minute per instance, on its own database session so a
rotation never commits anything an in-flight login had pending. A failed
rotation is logged and retried later; it never breaks a login.

> ### ⚠️ `NFC_AUTO_ROTATE` is the fleet gate
>
> It defaults to `false` and must stay there until **every** device runs a
> build that understands the keyring. An older build takes the current key,
> ignores the ring, and loses the ability to read everything written under the
> previous version. Turning it on is a deliberate act by someone who has
> confirmed the fleet; `acknowledge_fleet_updated` asks the same question of the
> manual endpoint.

Version 255 is the ceiling — the version travels in one byte of the payload
header. Rotating past it is refused, and retiring old versions is the way out.

### Migration is passive

A chip moves to the current version when something rewrites it — a consultation,
a vaccine, an edit. A read alone does not migrate it. Chips belonging to people
who never return stay on their original version indefinitely.

---

## 7. Retiring a version

With `NFC_KEK` set (the ring lives in the database), retiring a version means
**revoking** it (`POST /patients/nfc-keys/revoke`, section 4); `NFC_KEY_V<n>`
and `NFC_CURRENT_KEY_VERSION` are ignored in that mode, and the backend logs a
warning at startup if they are still set. The rest of this section describes the
environment mode (no KEK), where retiring means removing `NFC_KEY_V<n>` from the
deployment.

Either way, from that moment chips still on version `n` **cannot be decrypted
offline**.

They are not lost: with connectivity the UID still resolves the patient from the
backend, and the next write migrates the chip. The degradation is "this chip is
online-only until someone uses it once with a signal".

### Telemetry storage

Sightings land in `nfc_key_version_observations`, created by the
database migrations like every other table. It is **append-only**: the
device keeps one row per chip, the server keeps every sighting, because the gap
between consecutive sightings of the same UID is what a retention period has to
be sized from.

`observed_at` must arrive with a timezone offset. A naive value would be read as
UTC and silently shift the sighting by the reporting device's own offset, which
corrupts ordering between devices in different zones. The API rejects naive
timestamps.

Attribution ("latest sighting wins") is ordered by the **server's** clock, not
the client's, so a device with a skewed clock cannot make a stale sighting
outrank a newer one reported elsewhere.

### Check before retiring

```
GET /api/v1/patients/nfc-key-versions/usage?window_days=90
```

Requires `org_admin` or `superadmin`. It reports how many **distinct chips** were
last seen on each version, split by `patient` and `guardian`, plus
`retirable_versions` — versions chips were last seen on, but none inside the
window. Both come from the telemetry alone: a version no chip is currently
attributed to (never reported, or every chip seen on it has since moved on)
appears in neither, even if it is still in the ring.

**Read `retirable_versions` as a veto, not a clearance.** A version missing from
the telemetry may still have chips in the field whose patients have not come
back. Absence of sightings is not evidence of absence of chips. It can tell you a
retirement is obviously unsafe; it cannot tell you one is safe.

Watch the `guardian` rows specifically. Guardian cards are written with the same
keyring but rewritten less often, so they are the likelier stragglers and a
bracelet-only reading is optimistic.

### Retirement is manual today

Nothing retires versions automatically. In environment mode, creating and
retiring versions are human actions requiring deploy access. With a KEK, they
are API calls (`/rotate`, `/revoke`) and `NFC_AUTO_ROTATE` can create new
versions on a schedule, but retiring old ones is still a human decision. This is a known limitation: a control that depends on
someone remembering a periodic chore is weak, and it is worse for a self-hosting
adopter who will not administer keys at all. Automating the lifecycle — and the
retention policy it would need — remains open.

---

## 8. On the device

- The keyring lives in the platform secure store (Keystore / Keychain). The
  web build has none: there it is kept in memory only and is gone when the tab
  closes.
- A login or refresh response without keys (a backend that could not read the
  ring, § 4) leaves the ring the device already holds in place. A refused
  refresh (`401`) signs the device out and wipes the ring with the rest of the
  secure store.
- It is **bounded by the session window**: it is only served while the refresh
  token's `exp` is in the future, checked locally so it holds offline. Outside
  the window it is wiped from memory and disk and NFC is refused.
- Moving the device clock backwards more than 24 hours is treated as tampering
  and closes the window. The high-water mark is re-anchored to the server's own
  clock (the refresh token's `iat`) on every successful login and refresh, so a
  device whose clock ran ahead recovers by reconnecting rather than staying
  locked out.
- `superadmin` and `org_admin` accounts receive **no** key material. A
  `superadmin` has no clinical access; an `org_admin` reads records online only
  (`/scan`, `/search`), and the app's device reader is for `doctor` and `nurse`.
  With one symmetric key, a key that can read a chip can also write a forged
  one, so no account holds it without a use for it. An `org_admin` device that
  signed in before this rule keeps the ring it already had, because the app
  keeps its ring when a response carries none. It is deleted when that session
  ends: sign-out, a refused refresh, or the end of the 7-day window.

---

## 9. Local development

`NFC_KEY_V<n>` entries are read from `.env` as well as from the process
environment, so a rotation can be exercised locally:

```dotenv
NFC_MASTER_KEY="<64 hex chars>"
NFC_KEY_V1="<64 hex chars>"
NFC_CURRENT_KEY_VERSION=1
```

A real environment variable of the same name wins over the `.env` entry, matching
every other setting. Use throwaway values locally — never a production key.

---

## 10. KEK custody

`NFC_KEK` is the single secret that unwraps every key in the table. Losing it
makes every chip on a database-held version unreadable offline until rewritten —
which, once version 0 is imported, means the whole fleet. There is no recovery
by design: if there were, the wrapping would be worthless.

**Custodians:** two named people — the technical lead and the security lead
of the deploying organization. Who they are is recorded in the team's internal
register, not in this public document: naming them here only gives an attacker
two targets.

**Primary copy:** Secret Manager, secret `nfc-kek`, mounted by `cloudbuild.yaml`
as `NFC_KEK=nfc-kek:latest`.

> **Never add a version to `nfc-kek`.** Cloud Run reads `:latest` every time an
> instance starts. A new value cannot unwrap the rows, so every instance started
> after it — a new revision, but also a scale-up or a restart of the current
> one — refuses to start, and the service goes down as running instances are
> recycled. Disabling the new version does not help: `latest` is the most
> recently *created* version, enabled or not. To recover, add the original value
> back as a new version, from the backup below:
> `age -d kek.age | gcloud secrets versions add nfc-kek --data-file=-`.
> (Pinning the old version on the service, `--update-secrets=NFC_KEK=nfc-kek:<n>`,
> also works at once, but the next deploy from `cloudbuild.yaml` puts `:latest`
> back.) Changing the KEK means re-wrapping every row of `nfc_keys` under the
> new one; there is no tool or procedure for that yet.

**Backup:** the KEK encrypted with `age` under a six-word passphrase known to
both custodians, stored in two separate, access-controlled locations — one per
custodian, under different accounts.

Created once, at the time the KEK is generated:

```bash
openssl rand -hex 32 > kek.txt          # the KEK: 64 hex characters
age -p -a -o kek.age kek.txt            # prompts for the passphrase

age -d kek.age > kek-check.txt          # verify BEFORE deleting anything
diff kek.txt kek-check.txt && echo "BACKUP OK"

gcloud secrets create nfc-kek --data-file=kek.txt   # -> NFC_KEK
# kek.age  -> each custodian's backup location

shred -u kek.txt kek-check.txt          # macOS: rm -P
```

Then one custodian downloads `kek.age` **from their backup location** — not the
local copy — and decrypts it, to prove what was uploaded works.

**Permanent rules:**

- Never paste the KEK or the passphrase into chat, email or Slack.
- The passphrase must not live in the same place as the file. Together they are
  one secret, not two.
- Re-verify when a custodian changes, and at least once a year.
- **A database backup is incomplete without the KEK backup.** Restoring the
  database alone yields keys nobody can unwrap. This is recorded alongside the
  tables themselves in [Database Architecture](database.md), so whoever
  restores a backup meets the warning without having to know this page exists.

### Checking a backup matches production

`kek_id` is a fingerprint of the KEK that sealed each row, and it is not secret
— which is what makes it useful. To confirm a backed-up KEK is the one in use,
without unwrapping anything:

```bash
age -d kek.age | tr -d '\n' | xxd -r -p | sha256sum | cut -c1-16
```

Compare the result with the `kek_id` reported by
`GET /api/v1/patients/nfc-keys`. A mismatch means the backup does not
correspond to production — something to find out now rather than during an
incident.

Record the current fingerprint next to each copy of `kek.age`. It is safe to
store in the clear.

If both custodians forget the passphrase the KEK is lost just the same. Each may
keep it on paper, held personally and separately from the file. A six-word
passphrase transcribes without error; 64 hex characters does not, which is the
whole reason the file is encrypted rather than the key printed.
