# Firefly — instructions for coding agents

Read `docs/PRD.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and the relevant API/data-model/workflow doc before editing code.

## Non-negotiable architecture

- Project name: Firefly.
- Backend: Python + FastAPI. Keep route handlers thin; orchestration belongs in the service layer.
- The only supported secret sharing configuration is Shamir Secret Sharing, **3 shares / threshold 2**, mapped to Upstash, Neon, and Firestore. Do not reintroduce a five-provider profile without explicit human approval.
- Split the original secret directly for this MVP. Do not introduce a DEK/envelope-encryption redesign unless a human explicitly changes the decision.
- Protect each of the three shares via Akeyless AES-256-GCM using fixed provider-to-key-reference mapping:
  1. `upstash` → its Akeyless key reference
  2. `neon` → its Akeyless key reference
  3. `firebase` → its Akeyless key reference
- Store one ciphertext per provider: Upstash Redis, Neon PostgreSQL, Firebase Firestore.
- Metadata: SQLCipher-encrypted SQLite. Never store the original secret, plaintext shares, or raw Akeyless AES keys in metadata.
- The backend is trusted with plaintext in this threat model. Do not add client-side encryption, holder microservices, MPC, FHE, or TEEs.
- No Rust dependency. Python-only implementation.
- Keep all external calls out of an open SQLite transaction. Use short transactions for metadata state updates.
- An operation is `AVAILABLE` only when all active-profile provider writes are confirmed and the state transition has committed.
- Recovery requires two **distinct, valid** shares from one record/version. Akeyless authenticates each ciphertext; the orchestrator enforces the SSS threshold.
- A timeout means `UNKNOWN`, not definitely failed. Reconcile by deterministic share ID before retry/delete.
- All writes/deletes must be idempotent where the provider supports it. Never claim rollback is complete without confirmation.
- No plaintext secrets, shares, raw keys, tokens, request bodies, or connection strings in logs, exceptions, telemetry, test snapshots, or error responses.
- Python memory cleanup is best-effort. Do not claim full zeroization.

## Crypto and package rules

- Never implement cryptographic algorithms or Shamir math from scratch.
- Before selecting a package, verify current maintenance, license, supported byte lengths, secure randomness, Python compatibility, and tests. Document the selection in an ADR.
- Use Akeyless's documented Encrypt/Decrypt API format. Binary shares must be encoded/decoded consistently with Akeyless's documented Base64 input/output options. Treat the returned ciphertext as opaque; do not invent a nonce/tag format.
- Use the identical encryption context on Encrypt and Decrypt. Context should bind at least record ID, version, share index, provider ID, and format version.
- Key references are not key material. Validate key references against trusted config/registry; do not blindly trust a metadata row to select an arbitrary key.
- Never put real credentials into examples, fixtures, docs, commits, or `.env.example`.
- Do not use Python's `random` module for security-sensitive IDs or randomness.

## Coding workflow

1. Inspect the repository and current git status first. Do not overwrite existing work or assume an empty repo.
2. Read the relevant docs and summarize the intended change before editing.
3. Implement one small milestone/vertical slice at a time.
4. Add or update tests in the same change.
5. Run targeted tests and report exact commands/results. Do not say tests passed unless they were run.
6. Inspect the diff for secrets, accidental scope expansion, dead code, unrelated formatting and architecture drift.
7. If an API/SDK detail is uncertain, consult official docs or write a small isolated integration spike; do not guess.
8. Ask for human confirmation before changing crypto design, threshold, providers, key mapping, schema migration strategy, or public API contract.

## Style

- Type hints for public functions.
- Small functions with explicit inputs/outputs.
- Provider adapters implement the common interface in `docs/ARCHITECTURE.md`.
- Inject dependencies; avoid hidden global clients.
- Use structured, redacted logs.
- No unnecessary abstractions, background queues, agent frameworks, or generic plugin systems.
