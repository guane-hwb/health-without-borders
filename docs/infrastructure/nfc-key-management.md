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

## 4. Generating a key

```bash
openssl rand -hex 32
```

Store it in Secret Manager. **Never** commit a real key, and never paste one
into an issue or a chat.

---

## 5. Rotating

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

### Migration is passive

A chip moves to the current version when something rewrites it — a consultation,
a vaccine, an edit. A read alone does not migrate it. Chips belonging to people
who never return stay on their original version indefinitely.

---

## 6. Retiring a version

Retiring means removing `NFC_KEY_V<n>` from the deployment. From that moment,
chips still on version `n` **cannot be decrypted offline**.

They are not lost: with connectivity the UID still resolves the patient from the
backend, and the next write migrates the chip. The degradation is "this chip is
online-only until someone uses it once with a signal".

### Telemetry storage

Sightings land in `nfc_key_version_observations`, created by
`scripts/create_tables.py` like every other table. It is **append-only**: the
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

Nothing creates or retires versions automatically. Both are human actions
requiring deploy access. This is a known limitation: a control that depends on
someone remembering a periodic chore is weak, and it is worse for a self-hosting
adopter who will not administer keys at all. Automating the lifecycle — and the
retention policy it would need — remains open.

---

## 7. On the device

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

## 8. Local development

`NFC_KEY_V<n>` entries are read from `.env` as well as from the process
environment, so a rotation can be exercised locally:

```dotenv
NFC_MASTER_KEY="<64 hex chars>"
NFC_KEY_V1="<64 hex chars>"
NFC_CURRENT_KEY_VERSION=1
```

A real environment variable of the same name wins over the `.env` entry, matching
every other setting. Use throwaway values locally — never a production key.
