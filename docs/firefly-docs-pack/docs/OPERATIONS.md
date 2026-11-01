# Operations and Local Development

## 1. Local setup principles

- Use a project virtual environment and a supported Python version selected during Milestone 0.
- Copy `.env.example` to `.env`; fill only local test credentials.
- `.env` must be ignored by Git. Never paste real secrets into issue trackers, agent prompts, README, or screenshots.
- Use separate test credentials/projects for Firefly; do not use production database accounts.
- Keep the SQLCipher database in a persistent local path excluded from Git.
- If using Docker, ensure the metadata directory is mounted to persistent storage; deleting a container must not silently destroy the only metadata database.

## 2. Configuration categories

Required categories (exact names are in `.env.example`):
- application auth token and environment;
- SQLCipher DB path and stable encoded key;
- Akeyless endpoint/auth and three active key references;
- Upstash connection URL/token;
- Neon connection string;
- Firebase credentials path or supported application-default credentials;
- Neon PostgreSQL connection string/table/schema.

The `.env.example` contains placeholders only. Validate the shape of every required value on startup. Do not print values on validation error.

## 3. Akeyless account readiness

Before coding all adapters:
- verify the active account can use three AES-256-GCM keys;
- verify Encrypt/Decrypt operation limits;
- verify the authentication method and least-privilege access to those keys;
- test binary input/output via Base64 with a record/index/provider/version context;
- confirm context mismatch fails decryption.

API-key authentication is acceptable for the short hackathon proof of concept, but Akeyless does not recommend it for production. Revisit workload identity/cloud IAM if the project is deployed beyond the demo.

## 4. SQLCipher readiness

First-time setup is the explicit `python -m scripts.init_db`; normal startup refuses to create or replace the database and stops on a wrong key, missing schema, or failed `cipher_integrity_check`. On every start the app also reconciles `PENDING`/`CLEANUP_REQUIRED` records; check the startup log line `startup reconciliation ...` after a crash.

- Install selected SQLCipher-backed Python driver on the actual OS before implementation proceeds.
- Verify runtime reports SQLCipher version.
- Confirm DB key is applied before first database read.
- Confirm correct key opens and incorrect key fails.
- Confirm `cipher_integrity_check` behavior in the selected build/version.
- Never recreate an existing DB after key/integrity failure.
- Use encrypted, access-controlled backups and test restoration.

## 5. Provider retention

- Do not set TTL/expiration for share objects.
- Ensure Upstash eviction settings cannot evict shares.
- Verify each provider's free-tier limit, storage behavior, connection method and persistence configuration. Free-tier plans can change; do not encode pricing assumptions into application logic.
- Ensure each provider account/database/collection/table has the correct deterministic ID and credential boundary.

## 6. Demo safety

Use synthetic test secrets only. The API bearer token maps every authenticated request to the fixed `demo-owner`; it is not user identity or multi-tenant authorization. Bind the server to `127.0.0.1`, protect `.env`, and do not describe the demo as production-ready. Recovery returns plaintext: never log, persist, screenshot, or share response bodies.

At startup, verify the log's profile, provider IDs, 3/2 threshold, and metadata DB path. Run `.\.venv\Scripts\python.exe -m scripts.check_services` before a demo: it performs provider health probes and Akeyless synthetic encrypt/decrypt checks without writing provider objects. `GET /health` only confirms that the app responds; it is not a cloud readiness check.

Provider operations are sequential and do not share a transaction. If a store fails or times out, inspect both the record status and remote objects; a failed HTTP response does not prove that no ciphertext was stored. `CLEANUP_REQUIRED` means cleanup could not be confirmed. The active metadata file ends in `.three-provider`; switching to it does not migrate or read records from the legacy database file.
