# NFC Key Management

How the AES-256 keys that encrypt NFC chip payloads are configured, rotated and
retired. Until now these rules lived only in pull request descriptions; this is
the reference.

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
delivers the whole ring at login, refresh and `/users/me`. Devices decrypt with
whichever version a chip names and encrypt with the current one.

| Setting | Meaning |
|---|---|
| `NFC_MASTER_KEY` | The original key. Registered as **version 0**. |
| `NFC_KEY_V<n>` | The key for version `n`. One environment variable per key. |
| `NFC_CURRENT_KEY_VERSION` | The version new writes are encrypted with. Defaults to `0`. |

Every key must be exactly **64 hexadecimal characters** (32 bytes) and every
version must be between **0 and 255** — the version travels in a single byte of
the payload header.

The application **refuses to start** if any key is malformed or if
`NFC_CURRENT_KEY_VERSION` has no matching key. The error names the offending
variable and never prints its value. A deployment with no NFC key at all is
valid; NFC is simply unavailable.

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
device between 3 and 8 September 2026, so nothing was ever written with a
version header, and no rotation has taken place: `NFC_CURRENT_KEY_VERSION` has
been `0` throughout.

That means the two formats never coexisted in the field, and the wire format is
frozen from the current build onwards. It also means the key in
`NFC_MASTER_KEY` is the only key any existing chip can be read with — losing or
changing it makes every chip in circulation unreadable offline.

## 4. Where the ring lives

The ring can come from two places, and `GET /api/v1/patients/nfc-keys` reports
which one is in use.

**Without `NFC_KEK`** the ring is read from the environment, exactly as it
always was. Nothing changes on upgrade.

**With `NFC_KEK`** the ring lives in the `nfc_keys` table, with each key sealed
under the KEK (AES-256-GCM, version as associated data, `kek_id` recording which
KEK sealed each row). On first start the environment's `NFC_MASTER_KEY` is
imported as version 0 and the table becomes the source of truth. That is what
makes revocation an API call instead of a redeploy.

Importing version 0 also closes the `hwb-nfc-master-key:latest` hazard — adding a
Secret Manager version there can no longer silently change what version 0 *is*.
The trade is that the KEK then guards every version, version 0 included: losing
it costs offline reads across the whole fleet, not just for rotated chips.
**The KEK backup procedure is a precondition for setting `NFC_KEK`.**

The backend refuses to start if the KEK cannot unwrap the current version. A
wrong KEK would otherwise surface as devices quietly receiving an empty ring.

### Revoking a compromised key

```
POST /api/v1/patients/nfc-keys/revoke
{"version": 0, "reason": "...", "acknowledge_chip_impact": true}
```

`superadmin` only. The version stops being delivered for reading *and* writing —
anything less is useless against a leak, since the leaked key is the one that
reads. If it was the current version, a replacement is generated and becomes
current. Every operation is recorded in `nfc_key_events` with actor and reason.

**It is not instant on the device.** Revocation stops *delivery*. A device that
already holds the ring keeps it until its next refresh: up to an hour online, up
to the refresh-token window (7 days) offline. There is no way to reach an
offline device sooner.

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

1. **Deactivate the user** of that device (`PATCH /users/{id}` with
   `{"is_active": false}`). Login, refresh and every authenticated call are
   refused from that moment, so the device stops receiving keys.
2. **Revoke the key version** the device held, as above.
3. **Do not simply reactivate the account.** Reactivating revives the refresh
   tokens issued before the deactivation, including the stolen one. Until
   per-user session revocation exists, give the person a new account instead of
   reactivating the old one.

If the whole organization is compromised, deactivate the organization: that
blocks all its users at once.

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

1. Generate a new key and create a **new** secret, e.g. `hwb-nfc-key-v1`.
2. Mount it in `cloudbuild.yaml` under `--update-secrets` as `NFC_KEY_V1`.
3. Set `NFC_CURRENT_KEY_VERSION=1` under `--update-env-vars`.
4. Deploy. New writes use version 1; version 0 stays in the ring for reading.

> **Never add a new version to an existing secret.** `NFC_MASTER_KEY` is mounted
> as `hwb-nfc-master-key:latest`. Adding a Secret Manager version to that secret
> silently changes what version 0 is, and **every existing chip becomes
> unreadable**. Rotation always means a new secret with a new name.

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
once it reaches `NFC_ROTATION_PERIOD_DAYS` (default 90). The check runs on the
request path, not on a schedule — the project has no scheduler, and an adopting
organisation should not have to stand one up for key rotation to happen. It is
evaluated at most once a minute per instance, on its own database session so a
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
`retirable_versions` — versions with no sighting inside the window.

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

- The keyring lives in the platform secure store (Keystore / Keychain).
- It is **bounded by the session window**: it is only served while the refresh
  token's `exp` is in the future, checked locally so it holds offline. Outside
  the window it is wiped from memory and disk and NFC is refused.
- Moving the device clock backwards more than 24 hours is treated as tampering
  and closes the window. The high-water mark is re-anchored to the server's own
  clock (the refresh token's `iat`) on every successful login and refresh, so a
  device whose clock ran ahead recovers by reconnecting rather than staying
  locked out.
- `superadmin` accounts receive **no** key material: they have no clinical access
  and never touch a chip.

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

**Custodians:** Andrés Guerrero and Leonardo Calderón.

**Primary copy:** Secret Manager, as the value of `NFC_KEK`.

**Backup:** the KEK encrypted with `age` under a six-word passphrase known to
both custodians, stored in each custodian's Drive — two separate accounts.

Created once, at the time the KEK is generated:

```bash
openssl rand -hex 32 > kek.txt          # the KEK: 64 hex characters
age -p -a -o kek.age kek.txt            # prompts for the passphrase

age -d kek.age > kek-check.txt          # verify BEFORE deleting anything
diff kek.txt kek-check.txt && echo "BACKUP OK"

# kek.txt  -> Secret Manager, as NFC_KEK
# kek.age  -> each custodian's Drive

shred -u kek.txt kek-check.txt          # macOS: rm -P
```

Then one custodian downloads `kek.age` **from their Drive** — not the local copy
— and decrypts it, to prove what was uploaded works.

**Permanent rules:**

- Never paste the KEK or the passphrase into chat, email or Slack.
- The passphrase must not live in the same Drive as the file. Together they are
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
