# Firefly Threat Model

## 1. Security objective

Reduce the chance that theft or compromise of one storage provider—or offline theft of the closed metadata database—reveals the original secret. Preserve recovery when up to two providers are unavailable after successful storage.

## 2. In scope

- Read-only theft of one provider's stored share ciphertext.
- Attempted tampering with a stored ciphertext or encryption context.
- Theft of a closed SQLCipher database file without its encryption key.
- Provider outage after a successful write.
- Partial writes, ambiguous network timeouts, process crash during storage.
- Unauthorized API request under the chosen authentication/authorization policy.
- Accidental logging or persistence of plaintext data.

## 3. Explicitly out of scope

- Fully compromised FastAPI process or backend host.
- Attacker obtains backend credentials that can invoke Akeyless decrypt for enough shares.
- Attacker obtains three decrypted shares.
- Loss/compromise of the SQLCipher key or Akeyless service identity.
- Malicious/compromised cloud provider operator or provider control plane.
- Guaranteed erasure from Python memory, provider replicas, provider backups, swap, screenshots, or client memory.
- Multi-instance concurrency and active-active failover.

## 4. Assets

- Original secret (highest sensitivity).
- Plaintext SSS shares and reconstructed secret in backend memory.
- Akeyless authentication credentials and the three encryption key references/permissions.
- SQLCipher database key.
- Provider credentials/connection strings.
- Metadata: owner, record ID, provider mapping, version, statuses.

## 5. Trust boundaries

1. Client ↔ FastAPI: HTTPS required; backend sees plaintext after request parsing in the agreed design.
2. FastAPI/orchestrator ↔ Akeyless: HTTPS and authenticated API calls; Akeyless receives plaintext share during encrypt/decrypt operations.
3. Orchestrator ↔ three storage providers: send/store only ciphertext; use distinct credentials and TLS.
4. Backend ↔ local metadata file: SQLCipher protects at-rest file contents; backend has access to its key at runtime.

## 6. Threat/control table

| Threat | Main control | Residual limitation |
|---|---|---|
| One provider database dumped | Store Akeyless ciphertext only | Compromised Akeyless permission or backend can decrypt |
| Ciphertext modified | Akeyless AES-GCM authentication/context | Metadata/provider routing must also be validated |
| Provider outage after create | 2-of-3 SSS across Upstash, Neon, and Firestore | Create requires all three writes; demo uses synthetic secrets only |
| Provider write timeout | `UNKNOWN`, deterministic ID, reconciliation | Provider API may not make outcome instantly observable |
| Partial create | Per-share durable statuses, rollback, `CLEANUP_REQUIRED` | Cross-provider deletion is not atomic; cleanup can remain incomplete |
| Offline metadata-file theft | SQLCipher | Not protection from running-process compromise or key loss |
| Database page tampering | SQLCipher page HMAC/integrity check | Does not detect every logical edit made through a valid SQL connection |
| Secret in logs/files | Redaction, no request-body logs, no plaintext temp files | Frameworks/third-party tools must be configured/tested |
| Python memory copies | Short lifetime, `finally`, mutable buffers where possible | No guaranteed complete zeroization |

## 7. Security invariants

- Original secret, plaintext shares and raw Akeyless keys are never written to metadata.
- Provider payload contains only the Akeyless ciphertext and minimal non-secret envelope fields.
- The configured provider-to-key map is trusted configuration; untrusted metadata cannot select an arbitrary key.
- Encryption context binds record ID, version, share index, provider ID and format version.
- A recovery share is counted only after successful Akeyless decryption/authentication and validation.
- No secret is returned unless the active profile threshold of distinct valid shares reconstructs successfully and the caller is authorized.
- Never log plaintext or credentials.
- Do not claim protection against a compromised API server.
