# Security Requirements

## 1. Secrets and credentials

- Commit `.env.example` with placeholder names only; `.env` is gitignored.
- Keep the Akeyless Access Key/token, the three active provider credentials, and SQLCipher key in protected backend configuration. Never expose them in frontend assets or endpoints.
- For the hackathon, environment variables are acceptable for runtime secrets, but do not describe them as production-grade secret management. Akeyless API-key authentication is documented for quick POCs; Akeyless recommends workload identity/cloud IAM patterns for production deployments.
- The 3-of-2 configuration is the only supported hackathon demo setup. Use synthetic secrets only. The API authentication is demo-only, and production deployments are rejected.
- Use distinct provider credentials/projects where practical and least privilege for each provider. No provider needs access to another provider.
- The orchestrator must validate all configured key references against a fixed provider registry.

## 2. Akeyless requirements

- Create three enabled AES-256-GCM keys if the account's actual plan allows it.
- Use the documented Encrypt/Decrypt API format; don't guess parameter names or response formats.
- The Encrypt API accepts plaintext string and supports Base64 input format. The Decrypt API accepts Base64 ciphertext and supports Base64 output format. Define a consistent binary share round trip and verify it with tests.
- Include stable context fields: `record_id`, `version`, `share_index`, `provider_id`, `format_version`.
- Supply the same encryption context to Decrypt.
- Normalize Akeyless errors; do not expose response bodies/tokens to client or logs.
- The backend is allowed to receive plaintext share output in this agreed threat model. Therefore key-scoped permissions improve least privilege but do not make the backend resistant to full compromise.

## 3. SSS requirements

- Use a reviewed, maintained library with secure randomness.
- Do not roll custom SSS arithmetic or use Python `random` for shares/record IDs.
- Configure exactly `shares=3`, `threshold=2`.
- Reject malformed shares, duplicate indices, wrong versions and fewer than two shares.
- Test all three combinations of two shares with one synthetic test secret.
- Do not claim the library proves threshold security without verifying its implementation/tests/docs.

## 4. SQLCipher requirements

- Use a driver linked to SQLCipher, not standard Python sqlite3.
- Verify reported `cipher_version` during startup/health validation.
- Load stable SQLCipher key from protected config before normal queries.
- Use explicit first-run database initialization only. Existing DB open/key/integrity errors must fail closed.
- Run `cipher_integrity_check` if supported/configured, especially at startup for the small MVP DB.
- Restrict file permissions; keep database in persistent storage; protect backups separately.
- Keep DB key separate from Akeyless keys.
- Do not claim protection against a compromised process holding the DB key.

## 5. HTTP/API

- HTTPS in deployment.
- Authenticate every store/recover/status request; authorize by owner.
- Enforce a 4 KiB UTF-8 byte limit for the secret for MVP (application product limit, not a platform limit).
- `Cache-Control: no-store` for recovery responses.
- Never log request bodies, response bodies for recovery, plaintext, shares, tokens, keys, DB URLs or connection strings.
- Use stable public error messages; detailed provider errors only in redacted internal logs.
- Apply explicit connect and read timeouts and bounded retries.

## 5a. Demo endpoints

`/demo/...` (see `API_CONTRACT.md`) decrypts individual shares and returns a share's numeric preview and, once the threshold is met, the secret. They are disabled unless `FIREFLY_DEMO_ENDPOINTS=true`, require the bearer token, and exist for a synthetic-secret demo only. Treat enabling them as lowering the security posture to "anyone with the token can inspect shares". Never enable them with real secrets. `POST /demo/secrets/{id}/fetch` is deliberately reachable without the bearer token: whoever presents the threshold number of genuine shares could rebuild the secret anyway, and each presented card is verified against the real stored share, so an invalid card unlocks nothing. It has no rate limit, so it is acceptable only because it is demo-only and flag-gated.

## 6. Memory/temporary data

- No plaintext temporary files or persistent caches.
- Keep plaintext lifetime short; use `try/finally` and mutable `bytearray` buffers when compatible with library APIs.
- Do not add Rust.
- Explain that complete memory erasure is not guaranteed in Python because immutable objects and dependencies may create copies.

## 7. Provider-specific notes

- **Upstash Redis:** no TTL for shares; ensure eviction policy cannot evict shares. Store encrypted ciphertext only.
- **Neon PostgreSQL:** use TLS/secure connection string, dedicated table and least-privilege DB role.
- **Firebase Firestore:** use server-side Admin SDK credentials; do not bundle service-account key in client; use a dedicated collection and deterministic document IDs.

## 8. Claims to avoid

Never claim: fully compromised backend cannot access secrets; zeroization guaranteed; cross-provider atomic transaction; every provider backup is physically erased after delete; recovery works if Akeyless is unavailable; or the demo authentication provides production identity/tenant isolation.
