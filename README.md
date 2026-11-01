# Security Vault

Security vault is a synchronous FastAPI hackathon demo. It splits a secret into three
Shamir shares (threshold two), encrypts each share with its configured
Akeyless key, and stores one ciphertext each in Upstash, Neon, and Firestore.
SQLCipher protects local metadata.

See [Implemented architecture and workflow](./docs/firefly-docs-pack/docs/IMPLEMENTED_ARCHITECTURE.md)
for diagrams based on the code currently wired into the application.

The only supported setup is `FIREFLY_PROFILE=three_provider` with the fixed
3-share / 2-threshold configuration. The app defaults to this profile if the
variable is absent and rejects other profile values. The SQLCipher database
uses a `.three-provider` filename suffix to avoid reusing older five-provider
metadata; records in that older file are not migrated.

## Local setup

1. Use Python 3.11 or newer and create/activate a virtual environment.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set the local API token, a Base64-encoded
   32-byte SQLCipher key, Akeyless credentials/key references, and provider
   connection settings. Do not paste credentials into chat or commit `.env`.
4. Create the encrypted metadata database once with
   `.\.venv\Scripts\python.exe -m scripts.init_db`. Normal startup never creates
   it: a missing file, wrong key, or failed integrity check stops the app.
   Keep the same `SQLCIPHER_KEY_B64` afterwards.
5. Provision the Neon table and Firestore collection. See
   [Provider setup](./docs/firefly-docs-pack/docs/PROVIDER_SETUP.md) for the
   schema and credential boundaries.
6. Run the read-only live dependency preflight with
   `.\.venv\Scripts\python.exe -m scripts.check_services`. It checks Akeyless
   encrypt/decrypt and the three provider health probes without writing shares.
7. Start the API (it also reconciles unfinished records from a previous crash):

   ```powershell
   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

The API uses a single configured bearer token mapped to `demo-owner`; this is
demo authentication, not user identity or multi-tenant authorization. Keep the
API bound to localhost. Recovery returns plaintext, so use only a synthetic
demo secret and do not log, persist, screenshot, or share recovery response
bodies. Startup logs show the active profile, provider IDs, threshold, and
metadata DB path. `/health` confirms only that the app responds; use the
preflight and an end-to-end synthetic test to establish cloud readiness.

## Tests

```powershell
python -m pytest -q tests
```

The unit tests use fake service clients and do not contact cloud providers.
The fake Akeyless is not cryptography; `tests/test_real_crypto.py` checks real
AES-GCM behavior against Akeyless and runs only when `FIREFLY_LIVE_TESTS=1`.
Live store/recover tests write external ciphertext records; use dedicated test
projects and a synthetic secret, then verify cleanup. Never use production
credentials for integration tests.

## Demo

The demo is an HTTP client of the FastAPI server (1 share fails, 2 or 3 rebuild). Start `uvicorn app.main:app`
with `FIREFLY_DEMO_ENDPOINTS=true`, then run `python -m scripts.demo`; for a no-credentials rehearsal use
`uvicorn scripts.demo_server:create_app --factory` and `python -m scripts.demo --rehearsal`. 
