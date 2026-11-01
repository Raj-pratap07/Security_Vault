# Architecture Decision Records — Firefly

This file is the source of truth for locked decisions. Ask a human before changing any accepted decision.

## ADR-001 — Trusted backend threat model

**Status:** Accepted  
**Decision:** The API server/orchestrator is trusted to handle plaintext secret and plaintext SSS shares during storage/recovery.  
**Reason:** This is the agreed hackathon scope.  
**Consequences:** No client-side encryption, DEK reconstruction on client, independent share-holder services, MPC/FHE/TEE, or claims that a compromised backend cannot read the secret. We still protect against isolated provider data theft and offline metadata-file theft.

## ADR-002 — SSS configuration

**Status:** Superseded by ADR-013
**Decision:** In the default `full` profile, split one original secret directly into 5 shares with threshold 3.
**Reason:** Simple MVP for one secret credential; no separate DEK/payload envelope.  
**Consequences:** In `full`, any 3 valid shares recover; fewer than 3 reveal no information under a correct SSS implementation. A successful creation requires all five writes confirmed, while later recovery tolerates up to two unavailable providers.

## ADR-003 — Share encryption

**Status:** Superseded by ADR-013
**Decision:** Akeyless AES-256-GCM Encrypt/Decrypt operations; five provider-specific key references, one per provider.  
**Reason:** Keep key material managed outside share stores and simplify backend key handling.  
**Consequences:** The backend needs Akeyless authentication and permission to use the five configured keys. Akeyless availability/authorization is required for recovery. Exact same encryption context must be supplied at decrypt. Verify account plan/quotas before depending on this design.

## ADR-004 — Storage providers

**Status:** Superseded by ADR-013
**Decision:** Upstash Redis, Neon PostgreSQL, MongoDB Atlas, Firebase Firestore, Supabase PostgreSQL.  
**Reason:** Five separately configured cloud provider/project boundaries for the demo.  
**Consequences:** Each provider gets exactly one share index and its own credentials. Share writes/deletes are separate remote operations; there is no cross-provider atomic transaction.

## ADR-005 — Metadata database

**Status:** Accepted  
**Decision:** SQLCipher-encrypted SQLite.  
**Reason:** Small single-instance MVP; metadata remains local and lightweight.  
**Consequences:** Keep a stable DB key outside the DB; verify the driver actually uses SQLCipher; use persistent local storage; short transactions; no multi-instance/shared network filesystem support in MVP. Application-level metadata HMAC is deferred.

## ADR-006 — Language/runtime

**Status:** Accepted  
**Decision:** Python only, using synchronous requests/adapters for MVP.  
**Reason:** Time constraints and lower integration complexity.  
**Consequences:** No Rust zeroization subsystem. Best-effort cleanup only; avoid blocking async handlers by using sync route handlers with sync dependencies.

## ADR-007 — Distributed write semantics

**Status:** Accepted  
**Decision:** All three writes must be confirmed before marking a new record `AVAILABLE`.
**Reason:** Clear conservative create semantics.  
**Consequences:** Create fails if any provider is unavailable even though 2-of-3 would theoretically allow recovery later. Use stable IDs, per-share statuses, `UNKNOWN` for ambiguous timeouts, rollback and `CLEANUP_REQUIRED`.

## ADR-008 — Public deletion endpoint

**Status:** Deferred  
**Decision:** No user-facing permanent deletion endpoint in initial MVP. Adapter `delete` is mandatory for rollback.  
**Reason:** Focus the hackathon on secure storage and recovery first.  
**Consequences:** Do not remove delete from adapter interface. Add public delete later only with a full state/retention policy.

## ADR-009 — Application-level metadata MAC

**Status:** Deferred  
**Decision:** Do not add custom record-level HMAC in initial MVP.  
**Reason:** Avoid duplicating integrity logic within 24 hours. SQLCipher page-level integrity is retained.  
**Consequences:** Do not claim that SQLCipher detects every logical metadata edit through a valid DB connection. Revisit if the threat model requires that.

## ADR-010 — SSS library selection

**Status:** Accepted: `shamirs` 4.x. See `adr/ADR-010-sss-library.md` for the evidence and the items still unverified.  
**Decision:** Use a maintained, reviewed SSS library; do not implement polynomial/field arithmetic manually.  
**Required evaluation:** Supports byte payloads of our allowed size, 3 shares/threshold 2, cryptographically secure randomness, clean reconstruction validation, supported Python version, license, current maintenance, and adequate tests. Add selected package/version and reasoning as an ADR before using it in production paths.

## ADR-011 — SQLCipher Python driver selection

**Status:** Accepted: `sqlcipher3` 0.6.x. See `adr/ADR-011-sqlcipher-driver.md`.  
**Decision:** Select a SQLCipher-linked Python driver only after verifying installability on the team's OS. Standard `sqlite3` is not sufficient.  
**Required proof:** `PRAGMA cipher_version` reports SQLCipher; wrong key fails to open existing DB; correct key opens it; tampered copy fails an integrity check where supported. Document driver package/version and key application syntax.

## ADR-012 — Temporary three-provider integration profile

**Status:** Superseded by ADR-013
**Decision:** Preserve `full` as the default profile (five providers, 5 shares / threshold 3). Add an explicitly selected `three_provider` profile using Upstash, Neon, and Firestore with 3 shares / threshold 2 while MongoDB Atlas and Supabase are unavailable.
**Reason:** Allow a synthetic end-to-end integration test without silently changing the intended full deployment design.
**Consequences:** `FIREFLY_PROFILE=three_provider` was initially temporary and used separate metadata from the full profile. See ADR-013 for the current permanent three-provider decision.

## ADR-013 — Permanent three-provider demo configuration

**Status:** Accepted
**Decision:** Firefly supports only Upstash, Neon, and Firestore with 3 Shamir shares and a threshold of 2. The five-provider 5/3 profile is not available in the running application.
**Reason:** The hackathon demo is proceeding with the three providers that are configured and verified. Do not retain an inactive five-provider mode that can accidentally become the selected runtime.
**Consequences:** `three_provider` is the only accepted `FIREFLY_PROFILE`; it is also the default when the variable is omitted. All three provider writes must still be confirmed before a record is `AVAILABLE`, and any two distinct valid shares may recover it. The metadata database keeps the `.three-provider` suffix, so prior five-provider metadata is preserved but is neither migrated nor read. The application rejects `FIREFLY_ENV=production`; the demo authentication and secret-returning recovery API are not production identity/access controls. Restore a five-provider design only through a new explicit human decision and a reviewed migration plan.

## ADR-014 — Fail-closed metadata startup and crash reconciliation

**Status:** Accepted
**Decision:** Normal startup opens an existing, verified SQLCipher database and never creates one; first-time creation is the explicit `scripts.init_db`. On every start, the app reconciles `PENDING` and `CLEANUP_REQUIRED` records before serving requests. A `PENDING` record becomes `AVAILABLE` only if every share is re-read from its deterministic provider ID, authenticated by Akeyless, matches its metadata, and all shares agree on one secret; otherwise it is rolled back to `FAILED` or `CLEANUP_REQUIRED`. A `CLEANUP_REQUIRED` record is never promoted.
**Reason:** A missing database silently recreated as empty would orphan remote shares, and a crash mid-store would otherwise leave unresolved records indefinitely.
**Consequences:** Shares are never re-written during reconciliation (plaintext shares are not kept). The all-three-writes rule for a new record is unchanged; reconciliation can only confirm what is already stored. Services are built once in the FastAPI lifespan, so a bad database, key or token stops startup instead of failing per request.

## ADR-015 — Demo endpoints and a flagged deletion route

**Status:** Accepted (requested by the project owner for the hackathon demo)
**Decision:** Add a separate `/demo` router with share-by-share collection, rebuild, and a record cleanup route. It is off by default (`FIREFLY_DEMO_ENDPOINTS`), returns 404 when off, requires the bearer token when on, and is built from a service class (`DemoService`), not route logic. The existing endpoints are unchanged.
**Reason:** Show, through the real HTTP API, that one share cannot rebuild the secret and any two can.
**Consequences:** This is the one exception to ADR-008 (no public deletion endpoint); the delete route exists only under `/demo` and only when the flag is on. The endpoints expose decrypted shares to an authenticated caller, which is outside the production threat model; `FIREFLY_ENV=production` remains rejected. Held shares are in-memory, bounded and expiring.
