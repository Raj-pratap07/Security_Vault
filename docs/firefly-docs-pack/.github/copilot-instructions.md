# Firefly repository instructions for GitHub Copilot

Follow `../AGENTS.md` and the relevant documents in `../docs/` before changing code. These are the source of truth for project scope and security constraints.

Firefly is a trusted-backend distributed secret store: FastAPI + Python orchestrator; SSS 3 shares/threshold 2; Akeyless AES-256-GCM with three provider-specific key references; storage in Upstash Redis, Neon PostgreSQL, and Firebase Firestore; SQLCipher-encrypted SQLite metadata.

Do not redesign the project around client-side encryption or DEK splitting. Do not add Rust. Do not implement cryptography manually. Never put secrets in logs or metadata. Use short SQLCipher transactions, deterministic share IDs, per-share statuses, `UNKNOWN` for uncertain timeouts, rollback on failure, and only mark a record `AVAILABLE` when all three writes are confirmed. Recovery must use two distinct valid shares from the same record/version.

Work incrementally: inspect first, implement one milestone, add tests, run tests, inspect the diff, and report what was actually verified. If docs and existing repository behavior conflict, explain the conflict instead of silently changing architecture.
