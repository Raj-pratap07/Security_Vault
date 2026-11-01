# Failure Handling and Idempotency

## 1. State model

Record statuses:

- `PENDING`: storage operation started, not yet fully committed.
- `AVAILABLE`: all three shares are confirmed stored and metadata transition committed.
- `FAILED`: storage did not complete and all writes/copies have been confirmed absent after rollback.
- `CLEANUP_REQUIRED`: at least one write/delete outcome remains uncertain or cleanup failed.
- `RECONCILIATION_REQUIRED`: reserved for a future/explicit recovery pass when persisted state cannot yet be resolved.

Per-share statuses:

- `NOT_STARTED`
- `STORED`
- `UNKNOWN` (timeout or response lost; provider might have acted)
- `WRITE_FAILED` (confirmed failed without a stored object)
- `DELETE_PENDING` (a delete was attempted and not confirmed)
- `DELETE_CONFIRMED`

Do not store plaintext share data or ciphertext copies in SQLite just to make retries easier. If the process dies and the share ciphertext was not stored remotely, the simplest safe policy is to clean up any other successfully stored shares and fail the operation rather than reconstruct or persist plaintext.

## 2. Write order

1. Generate record ID/version; create record and three per-share rows as `PENDING`/`NOT_STARTED` in a short transaction and commit.
2. Split in memory.
3. For each share, build stable context, call Akeyless Encrypt, call provider `put`, then read the deterministic ID back outside a metadata transaction.
4. Compare the complete returned envelope with the expected record/version/index/provider/key-reference/format/ciphertext values. Mark the share `STORED` only after an exact match.
5. On explicit failure, record `WRITE_FAILED` and begin rollback.
6. On timeout/ambiguous result, mark `UNKNOWN`; use the deterministic ID to query the provider before deciding whether to retry or delete.
7. After three `STORED` statuses, commit record `AVAILABLE`.

No cross-provider atomicity is assumed.

## 3. Retry semantics

- Bound retries and include backoff; no infinite loops.
- Do not retry authentication/permission failures blindly.
- Writes should use deterministic object IDs and overwrite/confirm the same logical object safely.
- For unknown writes, read/query by deterministic ID first. If found, compare the complete envelope and treat it as stored only on an exact match; if confirmed absent, retry the same operation once; if the lookup fails, retain `UNKNOWN` and do not promote the record.
- A successful provider response also requires read-back verification. A missing/mismatched envelope or unresolved read-back must not be recorded as `STORED`.
- Deletes should target the deterministic ID. An already-absent object may count as deletion confirmed if provider semantics make that conclusion reliable.
- Do not log ciphertext if not necessary; definitely do not log plaintext or credentials.

## 4. Rollback

On any storage/encryption failure after the pending record is created:

1. Attempt to resolve `UNKNOWN` write outcomes.
2. Delete objects that are confirmed or potentially stored.
3. Persist every result separately.
4. If every expected object is confirmed absent, mark the record `FAILED`.
5. Otherwise mark `CLEANUP_REQUIRED` and retain metadata needed for future reconciliation.

Do not delete the metadata needed to clean up remote objects before cleanup is confirmed.

## 5. Restart behavior (implemented at startup)

At startup, `reconcile_pending()` lists `PENDING` and `CLEANUP_REQUIRED` records (`UNKNOWN` is a share status, not a record status). For each `PENDING` record it reads every share back by deterministic ID, validates the envelope against metadata, decrypts through Akeyless with the stored context, and requires all shares to reconstruct the same secret. Only then is the record promoted to `AVAILABLE`. Otherwise (and always for `CLEANUP_REQUIRED`) it runs the normal rollback, ending in `FAILED` or `CLEANUP_REQUIRED` according to confirmed deletes. Reconciliation never re-writes shares and is idempotent, so repeated restarts are safe. A background worker is still deferred.

## 6. Recovery failure

- Try providers until the active profile threshold of distinct, valid shares has been acquired or all candidate providers have been exhausted.
- A provider timeout, invalid envelope, wrong context, wrong key reference, corrupted ciphertext, or duplicate index is not a valid share.
- Fail without reconstructing if fewer than the active profile threshold of valid shares are available.
- Clean plaintext buffers on every path as best-effort.

## 7. Public deletion

Public deletion is deferred. Internal adapter delete remains mandatory for rollback. If public deletion is added later, design `DELETING`, `DELETE_INCOMPLETE`, tombstones, backups and provider retention as a separate change.
