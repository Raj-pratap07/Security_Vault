# Hackathon verification

This runbook separates mocked tests, live provider checks, authenticated API
checks, and PC4 restricted-credential checks. A pass in one category does not
imply a pass in another.

## Terminal demo (through the FastAPI server)

The demo is an HTTP client of the real API. Use two terminals.

**Live (real Akeyless, Upstash, Neon, Firestore from `.env`)**

```powershell
# terminal 1: server
$env:FIREFLY_DEMO_ENDPOINTS = "true"
python -m scripts.check_services          # optional preflight
python -m uvicorn app.main:app --port 8000

# terminal 2: demo
python -m scripts.demo
```

**Rehearsal (no credentials; simulated clouds and Akeyless)**

```powershell
# terminal 1
python -m uvicorn scripts.demo_server:create_app --factory --port 8000
# terminal 2
python -m scripts.demo --rehearsal
```

Demo flags: `--url`, `--token`, `--auto`, `--secret TEXT`, `--plain`, `--no-color`. The token defaults to `FIREFLY_API_TOKEN` from the environment or `.env`.

The demo asks for a secret (Enter for a random key), stores it with `POST /secrets`, then runs three rounds, one Enter each. Every round collects one more share (`POST /demo/.../collect/n`) and asks the server to rebuild (`POST /demo/.../rebuild`):

| Round | Shares held | Server answer |
|---|---|---|
| 1 | 1 | `409`: one share is not enough |
| 2 | 2 | `200`: secret rebuilt, identical to the input |
| 3 | 3 | `200`: rebuilt from every one of the 3 pairs |

Each API call is shown with its status and timing, and the server terminal logs the same requests. It also first shows that a call with no token gets `401`, and finishes with `DELETE /demo/secrets/{id}` to remove the demo shares from every cloud and verify they are gone. The demo record stays in the server's metadata database with status `FAILED`, like the other live scripts.

Live mode uses your real metadata database, so the server must already have one (`python -m scripts.init_db`) and its startup will reconcile any old unfinished records first. Rehearsal mode is simulated: its Akeyless only Base64-encodes, so it is not evidence of real encryption. The 6-character codes are SHA-256 prefixes of each decrypted share; the "all one share holds" line is the numeric value of one share's first fragment. The layout is at most 78 columns wide so it never wraps.

**Troubleshooting `the server rejected our bearer token (HTTP 401)`.** The demo prints where its token came from (`--rehearsal`, `--token`, the `FIREFLY_API_TOKEN` environment variable, or `.env`) and which mismatch it detected:

- Rehearsal server with a normal demo: add `--rehearsal`.
- Real server with `--rehearsal`: drop the flag.
- Different tokens: restart the server after any `.env` change, and clear a stale shell variable that overrides `.env` (`Remove-Item Env:FIREFLY_API_TOKEN`, in both terminals), or pass `--token`.

Claims the demo does not support: protection from a compromised backend (it is trusted with plaintext), and failure scenarios such as outages, tampering or crash recovery (covered by the automated tests, not shown here). `/demo` exposes decrypted shares, so enable it only for the demo.

## Automated tests

Run unit and API-route integration tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_firefly_basics.py -q
```

The route integration test sends requests through the existing FastAPI routes
and authentication dependency, while using fake provider/Akeyless clients. It
verifies input validation, storage/recovery orchestration, and
`Cache-Control: no-store`. It is not a live cloud test.

## Live provider-pair recovery

With the application `.env` configured for the three active providers and
Akeyless, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_provider_pairs
```

The harness creates one random synthetic secret, stores it through the
orchestrator and actual adapters, then tests Upstash + Neon, Upstash +
Firestore, and Neon + Firestore through retrieval, Akeyless decryption, and
SSS reconstruction. It prints each pair's result and confirms cleanup.
Unavailable services are reported as `INCONCLUSIVE`. Cleanup leaves the local
metadata row as a failed record for audit.

## Live FastAPI route flow

Run:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_live_api
```

This uses FastAPI's in-process HTTP test client against the actual
authenticated routes and configured live services. It checks store, status,
recovery, no-store headers, and denial of a request without a bearer token.
The secret is compared in memory and never printed. Remote shares are removed
afterward; local metadata remains as a failed record after cleanup.

This verifies the API application boundary. It does not demonstrate PC4
reaching Uvicorn over the LAN, TLS, or Windows Firewall configuration.

## Create a PC4 test record on a trusted machine

On PC2 or another trusted machine with the legitimate Firefly token and
provider configuration, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.create_pc4_test_record
```

The script sends a fresh synthetic secret through the authenticated API,
checks that the record is `AVAILABLE`, then prints the record ID, version,
provider IDs, and exact deterministic share IDs. It deliberately leaves the
record and its provider shares available for PC4. It never prints the secret.

## PC4 unauthenticated API checks

These checks need only PC2's private LAN IP, API port, and the synthetic
record ID/version. They do not need any Firefly token or provider credential.
From PC4, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.pc4_api_probe `
  http://<PC2-LAN-IP>:8000 <record-id> <version>
```

The probe calls unauthenticated `GET /health`, then sends `POST
/secrets/{record_id}/recover` with no token. Health should return `200`; the
recovery request should return `401`. The recovery route uses the record ID;
the version argument is reported as the test record being checked because
the current public API does not take a version parameter.

To reach PC2 over the LAN, Uvicorn must listen on its LAN interface and the
firewall must allow the demo port. This app does not configure TLS; use only a
controlled lab network and synthetic values.

## PC4 restricted-provider probes

Use a separate restricted identity for each provider. Do not copy PC2's
`.env`, Akeyless credentials, provider-admin credentials, or legitimate
Firefly API token to PC4. The provider probe reads only `FIREFLY_PC4_*` values
and `PC4_PROVIDER_IDENTITY_LABEL` from PC4's local ignored `.env` or process
environment. Set the label to the actual restricted role or service-account
identity; the probe includes it in its output. It has no Akeyless client.

For Upstash, configure `FIREFLY_PC4_UPSTASH_URL` and
`FIREFLY_PC4_UPSTASH_READ_TOKEN`. Use the exact Upstash key printed by the
trusted record-creation script:

```powershell
.\.venv\Scripts\python.exe -m scripts.pc4_provider_probe upstash `
  <record-id> --version <version> --share-id <exact-upstash-key>
```

Select the database's **Read Only** REST token in the Upstash console. That
token is read-only for the database, so use a dedicated test database if
other keys in the same database must remain private.

For Neon, configure `FIREFLY_PC4_NEON_DATABASE_URL`, and optionally
`FIREFLY_PC4_NEON_SCHEMA` / `FIREFLY_PC4_NEON_TABLE`. Use the exact `share_id`
printed for Neon. The role should have `CONNECT`, schema `USAGE`, and `SELECT`
on `public.firefly_shares`, with no write-capable role membership.

An administrator can create the login and grants from `psql` (replace the
database name; set the password with `\\password firefly_pc4_ro`, not in a
saved SQL file):

```sql
CREATE ROLE firefly_pc4_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE <database_name> TO firefly_pc4_ro;
GRANT USAGE ON SCHEMA public TO firefly_pc4_ro;
GRANT SELECT ON TABLE public.firefly_shares TO firefly_pc4_ro;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.firefly_shares FROM firefly_pc4_ro;
```

Check that the role is not a member of any write-capable role and verify a
read succeeds while a harmless write is rejected.

For Firestore, configure `FIREFLY_PC4_FIREBASE_PROJECT_ID`,
`FIREFLY_PC4_FIRESTORE_COLLECTION`, and
`FIREFLY_PC4_FIREBASE_CREDENTIALS_PATH`. Use the exact document ID printed for
Firebase. The server SDK uses Google Cloud IAM rather than Firestore client
Security Rules; check the actual role scope and use a dedicated test project
or database containing only demo data if the read role is broader than one
collection. Grant the separate service account a read-only Firestore/Datastore
IAM role at the narrowest supported resource scope; Security Rules do not
restrict this server SDK identity. Keep its credentials file under the ignored
`secrets/` directory or another protected local path.

Each provider probe checks that the exact envelope can be read and matches
the record ID, version, share index, and provider ID. It then attempts a
harmless write at a random test ID; the restricted identity should be denied.
If the write unexpectedly succeeds, the probe attempts to delete that unique
test object and reports cleanup. It does not test cross-provider access or
prove what credentials exist elsewhere on PC4. Akeyless decryption is
intentionally not tested on PC4.

## First-time database setup

Before the first run, and before `create_pc4_test_record` or the live scripts, create the metadata database once:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_db
```

The live scripts start the application lifespan, so they also fail closed if the database is missing. `verify_live_api --max-size` stores a secret of exactly `FIREFLY_MAX_SECRET_BYTES` to check the largest payload against the real providers and Akeyless.

## Evidence recorded for this implementation change

- Automated tests: 29 passed, including HTTP route tests with fake provider
  clients.
- Live provider-pair check: Upstash + Neon, Upstash + Firestore, and Neon +
  Firestore passed; cleanup was confirmed.
- Live FastAPI routes: store/status/recovery, no-store, and unauthenticated
  denial passed; cleanup was confirmed.
- PC4 restricted credentials and LAN access: run from PC4 before the demo;
  until then, report them as `INCONCLUSIVE`.
