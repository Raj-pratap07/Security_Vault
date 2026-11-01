# Firefly

**Distributed Secret Storage with Threshold Recovery**

Firefly accepts one secret, splits it into three Shamir shares with a 2-of-3 threshold, protects each share through Akeyless AES-256-GCM encryption, and stores one encrypted share each in Upstash, Neon, and Firestore. SQLCipher-encrypted SQLite stores the metadata required to locate and manage the shares.

> **Agreed trust model:** the backend is trusted to handle the plaintext secret and plaintext shares during storage/recovery. Firefly is designed to reduce the risk from isolated storage-provider compromise and offline theft of the metadata database. It does **not** claim protection against a fully compromised backend process/host or an attacker with sufficient Akeyless permissions.

## Architecture at a glance

- **API:** FastAPI, request validation, authentication/authorization, safe responses.
- **Orchestrator:** coordinates store/recover operations, per-share progress, bounded retries, rollback.
- **Secret sharing:** Shamir's Secret Sharing (3 shares, threshold 2).
- **Share encryption:** Akeyless AES-256-GCM Encrypt/Decrypt APIs, one key reference per provider.
- **Storage:** Upstash Redis, Neon PostgreSQL, Firebase Firestore.
- **Metadata:** SQLCipher-encrypted SQLite; stores ownership, IDs, mappings, versions, operation states—not the original secret or plaintext shares.
- **Cleanup:** no intentional plaintext temporary files/logs; best-effort cleanup of mutable buffers in Python.

## Documentation map

1. [PRD](docs/PRD.md) — purpose, scope, users, requirements, acceptance criteria.
2. [Architecture](docs/ARCHITECTURE.md) — components and all primary workflows.
3. [Decision records](docs/DECISIONS.md) — locked choices and deferred items.
4. [Threat model](docs/THREAT_MODEL.md) — what Firefly protects and does not protect against.
5. [API contract](docs/API_CONTRACT.md) — endpoints, inputs, outputs, error semantics.
6. [Data model](docs/DATA_MODEL.md) — SQLCipher tables and provider envelope metadata.
7. [Failure handling](docs/FAILURE_HANDLING.md) — idempotency, uncertain outcomes, rollback, restart handling.
8. [Security requirements](docs/SECURITY.md) — secrets, Akeyless, SQLCipher, logs, cleanup.
9. [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — order of work and definition of done.
10. [Test plan](docs/TEST_PLAN.md) — unit, integration, fault-injection, acceptance tests.
11. [Operations](docs/OPERATIONS.md) — environment, local setup, service readiness, recovery limitations.
12. [AI coding workflow](docs/AI_AGENT_WORKFLOW.md) — how to use Copilot, Antigravity, or another code agent safely.
13. [Source references](docs/REFERENCES.md) — official documentation reviewed for this plan.

Mermaid source diagrams are in `docs/diagrams/`.

## Important implementation gates

Before running a live integration, verify these dependencies:

1. The active Akeyless account can use **three AES-256-GCM encryption keys** and supports the required Encrypt/Decrypt operations within its limits.
2. The chosen Python SQLCipher driver installs on the team's target OS and actually reports/uses SQLCipher—not standard unencrypted `sqlite3`.

The SSS package is deliberately not pinned in this documentation: the implementation agent must evaluate a maintained library for 3-of-2 sharing, arbitrary byte payload support, secure randomness, API correctness, licensing, and current compatibility. Do not implement Shamir arithmetic from scratch.

## Non-goals for the first hackathon MVP

- Defense against a fully compromised API server or backend host.
- Client-side encryption or splitting a separate data-encryption key (DEK).
- MPC, FHE, TEEs, or independent share-holder services.
- Automated background reconciliation worker/queue.
- Public deletion endpoint (adapter-level delete is required for rollback).
- Custom application-level metadata HMAC. SQLCipher page integrity remains in scope; custom record MAC is deferred.
- Guaranteed zeroization of every Python memory copy.

## Working rule

Read `AGENTS.md` before asking an AI code agent to make changes. Implement one milestone at a time, run its tests, inspect the diff, and commit only a verified step.
