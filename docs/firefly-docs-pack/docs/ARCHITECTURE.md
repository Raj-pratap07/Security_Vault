# Firefly Architecture

## 1. Component responsibilities

- **FastAPI (`app/api`)**: request/response models, authentication dependency, authorization, HTTP errors. No crypto orchestration in route handlers.
- **Storage Orchestrator (`app/services/orchestrator.py`)**: owns store/recover sequencing, state transitions, retry/rollback decisions, cleanup calls.
- **SSS service**: thin wrapper around a vetted library; the only supported configuration is 3 shares / threshold 2.
- **Akeyless service**: performs Encrypt/Decrypt operations, Base64 conversion and encryption-context construction. It never accepts a caller-provided arbitrary key name without checking the configured mapping.
- **Adapters**: one provider-specific implementation each, all with the same contract.
- **SQLCipher repository**: owns short transactions for record and share status. No network call from inside a DB transaction.

## 2. Locked provider/key mapping

| Provider ID | Store | Akeyless key reference |
|---|---|---|
| `upstash` | Upstash Redis | configured reference for share 1 |
| `neon` | Neon PostgreSQL | configured reference for share 2 |
| `firebase` | Firebase Firestore | configured reference for share 3 |

The only supported `three_provider` profile uses the `upstash`, `neon`, and `firebase` mappings as shares 1, 2, and 3, respectively.

Key names/references are configuration, not cryptographic key material. The registry validates the mapping. SQLite may persist the configured reference used for a record/version, but it must never contain the raw key or Akeyless credentials.

## 3. Provider adapter contract

Each adapter should expose a small equivalent of:

- `put_share(share_id, envelope) -> PutResult`
- `get_share(share_id) -> ShareEnvelope | None`
- `delete_share(share_id) -> DeleteResult`
- optional `health_check() -> HealthResult`

The exact interface should encode outcomes explicitly: confirmed success, confirmed absence/failure, or unknown outcome. Exceptions must be translated into safe adapter errors and must not include payloads or credentials in logs.

**Provider identity:** use deterministic IDs based only on random record ID, version and share index, for example `firefly:<record_id>:v<version>:share:<index>`. Never derive IDs from the secret itself.

**Stored payload:** store the Akeyless ciphertext exactly as returned, plus non-secret routing/format fields if needed. Treat Akeyless ciphertext as opaque; don't assume or invent a local nonce/tag layout. Use provider-native values (string, `BYTEA`, BSON document, Firestore fields). No TTL/expiration for stored shares.

## 4. Shared object contracts

`ShareEnvelope` logically contains:

- `record_id`
- `version`
- `share_index` (1–3)
- `provider_id`
- `key_ref` (reference only)
- `format_version`
- `ciphertext` (opaque Akeyless return value)

The record ID, version, index, provider ID and format version must be part of a stable Akeyless encryption context. Exactly the same context must be supplied at decryption. Before using a key reference read from metadata, compare it against the trusted provider registry.

## 5. Synchronous MVP

Use a synchronous implementation end-to-end initially: sync FastAPI route handlers, sync adapters/SDKs, and sync orchestration. This keeps error handling straightforward. Apply explicit connect/read/operation timeouts to external calls. Do not call synchronous network clients directly inside an `async def` handler. If async is introduced later, migrate the complete path consistently.

## 6. Four diagrams

### Diagram A — whole system

```mermaid
flowchart TB
    U([User / Client]) --> API[FastAPI: auth, validation, safe responses]
    API <--> O[Storage Orchestrator]
    O <--> SSS[SSS: profile-configured threshold]
    O <--> AK[Akeyless: AES-256-GCM Encrypt / Decrypt]
    O <--> DB[(SQLCipher + SQLite metadata)]
    O <--> REG[Provider registry and adapters]
    REG <--> P1[(Upstash Redis)]
    REG <--> P2[(Neon PostgreSQL)]
    REG <--> P3[(Firebase Firestore)]
```

### Diagram B — store/distribute

```mermaid
flowchart TD
    A[Client submits secret over HTTPS] --> B[Authenticate, authorize, validate size]
    B --> C[Create random record ID/version]
    C --> D[Commit PENDING record and 3 share rows]
    D --> E[SSS split: profile-configured shares and threshold]
    E --> F[For each share: select fixed provider/key]
    F --> G[Akeyless Encrypt with stable context]
    G --> H{Encryption succeeded?}
    H -- No --> X[Failure handler]
    H -- Yes --> I[Provider put using deterministic share ID]
    I --> J{Confirmed outcome?}
    J -- Yes --> K[Commit share status STORED]
    K --> L{All active-profile providers done?}
    L -- No --> F
    L -- Yes --> M[Verify all active-profile writes STORED; commit AVAILABLE]
    M --> N[Return record ID/status]
    J -- Timeout --> U[Commit UNKNOWN; reconcile deterministic ID]
    U --> X
    J -- Failure --> X
    X --> Y[Rollback confirmed/possibly stored copies]
    Y --> Z{All copies confirmed absent?}
    Z -- Yes --> Q[Commit FAILED]
    Z -- No --> R[Commit CLEANUP_REQUIRED]
    N --> C1[Best-effort buffer cleanup]
    Q --> C1
    R --> C1
```

### Diagram C — recovery

```mermaid
flowchart TD
    A[Recovery request] --> B[Authenticate caller]
    B --> C[Load SQLCipher metadata and verify ownership]
    C --> D{AVAILABLE and authorized?}
    D -- No --> E[Reject safely]
    D -- Yes --> F[Select unused provider]
    F --> G[Fetch ciphertext]
    G --> H[Validate record/version/provider/index]
    H --> I[Akeyless Decrypt with matching key/context]
    I --> J{Valid authenticated share?}
    J -- No --> K[Reject candidate; try another]
    J -- Yes --> L[Collect distinct plaintext share]
    L --> M{2 valid shares?}
    M -- Yes --> N[SSS rebuild original secret]
    M -- No --> O{Unused providers remain?}
    K --> O
    O -- Yes --> F
    O -- No --> P[Fail safely]
    N --> Q[Return secret to authorized caller; no-store]
    E --> R[Best-effort cleanup]
    P --> R
    Q --> R
```

### Diagram D — SQLCipher lifecycle

```mermaid
flowchart TD
    A[Application startup] --> B[Load stable SQLCipher DB key from protected config]
    B --> C{DB file exists?}
    C -- No --> D{Explicit first-time setup?}
    D -- Yes --> E[Create encrypted DB and schema]
    D -- No --> F[Fail closed; do not recreate]
    C -- Yes --> G[Open existing DB with SQLCipher]
    E --> H[Validate schema]
    G --> H
    H --> I[Run supported cipher_integrity_check]
    I --> J{Valid?}
    J -- No --> K[Stop sensitive operations; preserve file]
    J -- Yes --> L[Application ready]
    L --> M[Short metadata transactions]
    M --> N[SQLCipher encrypts/authenticates pages at rest]
```

## 7. Data flow notes

- Input secret is plaintext inside the trusted FastAPI/backend process in this agreed scope.
- SSS shares are plaintext in backend memory only until sent to Akeyless for encryption.
- Akeyless returns opaque ciphertext; each storage provider receives only its designated ciphertext.
- During recovery, Akeyless returns plaintext shares to the trusted backend; SSS reconstructs the original secret there.
- Python cleanup is best-effort. Never promise total memory erasure.
