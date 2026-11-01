# Product Requirements Document — Firefly

**Status:** Implementation baseline for hackathon MVP  
**Product:** Firefly  
**Version:** 1.0  
**Primary audience:** project team and AI coding agents

## 1. Product summary

Firefly stores one synthetic demo secret by splitting it into three Shamir shares with a threshold of two. Each share is encrypted using an Akeyless AES-256-GCM key dedicated to its storage provider, then stored in Upstash, Neon, or Firestore. Firefly can recover the original secret when any two distinct valid shares can be retrieved and decrypted. Metadata is held in a SQLCipher-encrypted SQLite database.

## 2. Problem

A single database or storage-provider compromise should not automatically expose the secret. The system also needs to recover from the loss or unavailability of up to two providers after a successful store operation. The backend is a trusted component for this version.

## 3. Goals

- Store one synthetic demo secret as three cryptographic shares with a 2-of-3 threshold.
- Encrypt each share through Akeyless before the share is persisted by a provider.
- Store exactly one share per provider across Upstash, Neon, and Firestore.
- Recover from any two valid shares belonging to the same record/version.
- Track operation progress and handle provider failures without falsely reporting success.
- Protect metadata at rest with SQLCipher.
- Avoid plaintext secrets/shares in logs and intentional temporary files.
- Keep implementation achievable within a 24-hour hackathon by using existing, maintained libraries and simple synchronous orchestration.

## 4. Non-goals

- Defending against a fully compromised API process/host.
- Client-side encryption, splitting a DEK, or sending ciphertext-only input to the backend.
- Multi-party computation, fully homomorphic encryption, trusted execution environments, or independently deployed share-holder services.
- High availability across multiple Firefly backend instances.
- Automated reconciliation worker or queue in MVP.
- Public permanent-deletion endpoint in MVP. Adapter deletion remains required for rollback.
- Guaranteed secure zeroization of every Python copy.
- Custom application-level metadata MAC in MVP.

## 5. Actors

- **Authorized client:** submits a secret, receives a record ID, and requests recovery.
- **FastAPI layer:** validates requests, authenticates callers, checks ownership/authorization, and returns controlled responses.
- **Storage Orchestrator:** coordinates SSS, Akeyless, providers and metadata.
- **Akeyless:** performs share encryption/decryption with provider-specific AES-256-GCM keys.
- **Storage providers:** hold ciphertext only and expose put/get/delete operations through adapters.
- **SQLCipher metadata store:** holds operation/record metadata and per-share statuses.

## 6. Functional requirements

### FR-1 Store a secret

The API accepts one synthetic demo secret in a request body. It validates authentication, size and format before invoking the orchestrator. A successful response returns the random `record_id`, `status=AVAILABLE`, `share_count=3`, and `threshold=2`; it never echoes the secret.

### FR-2 Split

Use a reviewed SSS library to create three shares with threshold two. Do not implement Shamir's algorithm manually. Record each share's index and version. Do not use secret content in IDs.

### FR-3 Encrypt

Encrypt each share using the provider's assigned Akeyless key reference. Use Akeyless's documented binary/Base64 contract and a deterministic encryption context containing `record_id`, `version`, `share_index`, `provider_id`, and `format_version`. Use exactly the same context on decryption.

### FR-4 Distribute

Write one opaque Akeyless ciphertext to each of: Upstash Redis, Neon PostgreSQL, and Firebase Firestore. Use a deterministic share ID for idempotent retry and cleanup. Do not configure TTL/expiration for shares.

### FR-5 Track progress

Create a durable `PENDING` record before external writes. Persist per-share status as progress is made using short database transactions. `AVAILABLE` is permitted only after all active-profile provider writes are confirmed and that final status commits.

### FR-6 Recover

Authenticate and authorize before retrieving shares. Retrieve candidates from available providers, validate record/version/provider/index, decrypt with the matching Akeyless key/context, and collect two distinct valid plaintext shares. Use SSS to reconstruct on the trusted backend. Return only to the authorized caller; set `Cache-Control: no-store`.

### FR-7 Failure handling

For a confirmed write, persist `STORED`. For an explicit failure, persist the failure. For timeout/ambiguous outcomes, persist `UNKNOWN`, then reconcile the deterministic ID before retry or delete. Attempt rollback for stored/possibly stored shares. If cleanup is not confirmed, persist `CLEANUP_REQUIRED`. Do not claim atomicity across providers.

### FR-8 Metadata security

Use SQLCipher rather than the standard unencrypted SQLite driver. Load its stable key from protected configuration before database use. Verify the driver actually links SQLCipher. On wrong key/integrity failure, fail closed and do not silently create a new database. The SQLCipher key must be separate from the three Akeyless keys.

### FR-9 Cleanup

No intentional plaintext files, logs, traces, exception payloads, or caches. Use `try/finally` for cleanup and overwrite mutable buffers where practical. Describe this as best-effort; Python/library copies may remain.

### FR-10 Status/health

Expose a basic health endpoint that does not reveal secrets, credentials, raw provider errors, or sensitive configuration. A status endpoint may return record status/share availability to an authorized owner, not the secret or ciphertext.

## 7. Non-functional requirements

- Maintainable, typed Python; simple layer boundaries.
- Synchronous provider adapters are acceptable for MVP; explicit timeouts on all network calls.
- No open SQLite transaction during network calls.
- Provider writes/deletes should be idempotent by deterministic share ID.
- Secrets and credentials must not be committed to source control.
- CI/tests must run without live provider credentials using mocks/fakes.
- The app must fail closed when encryption config, database key, provider mapping, or SSS config is invalid.

## 8. MVP acceptance criteria

1. Round-trip a synthetic test secret through SSS and recover from every 2-share combination (3 combinations).
2. One share cannot successfully recover the secret; the application rejects recovery before calling reconstruction with fewer than two.
3. Every stored share is Akeyless ciphertext; plaintext shares and original secret never appear in provider payloads or metadata.
4. All three provider writes are confirmed before status becomes `AVAILABLE`.
5. One provider unavailable during recovery can be tolerated if the other two providers work.
6. Akeyless context/key mismatch or ciphertext tampering causes candidate rejection.
7. A timed-out write becomes `UNKNOWN` and is reconciled before cleanup/retry.
8. A failed store attempts rollback; incomplete cleanup is recorded as `CLEANUP_REQUIRED`.
9. SQLCipher database cannot be read without the correct key; supported SQLCipher integrity check passes on valid DB and reports deliberately corrupted test copies as invalid.
10. Secret, share, key, token, and connection string redaction tests pass.
11. The implementation agent reports the exact commands/tests run; no unverified success claims.

## 9. MVP demo script

1. Store a synthetic secret and show the returned record ID/status.
2. Inspect provider records and show each has opaque ciphertext, not plaintext.
3. Recover the secret with two providers and show exact equality against the input, without logging or persisting the response.
4. Temporarily make one provider unavailable; recovery should still work if the other two are available.
5. Try recovery with fewer than two valid shares; it must fail.
6. Tamper with a ciphertext or encryption context in a controlled test; Akeyless must reject it.
7. Simulate a failed/uncertain write and show the state/rollback result.
8. Demonstrate that SQLCipher requires its key and that metadata does not contain the secret.

## 10. Claims we may and may not make

**Allowed:** threshold recovery across independent storage services; encryption of each share before storage; reduced exposure from isolated provider theft; SQLCipher protection against offline metadata-file theft without the key; best-effort cleanup.

**Not allowed:** unbreakable security; protection against compromised backend/runtime; guaranteed RAM zeroization; atomic transactions across cloud providers; guaranteed physical deletion from provider backups; recovery if Akeyless access or the SQLCipher key is lost.
