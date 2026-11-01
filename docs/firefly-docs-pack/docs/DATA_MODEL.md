# Data Model — SQLCipher Metadata and Provider Objects

## 1. Metadata schema concept

Use two tables so the record lifecycle and per-share progress can be updated independently. Exact SQL types/naming may be adapted by implementation, but preserve constraints and invariants.

### `secret_records`

| Column | Type/constraint | Purpose |
|---|---|---|
| `record_id` | TEXT PRIMARY KEY | Random opaque ID; never derived from secret |
| `owner_id` | TEXT NOT NULL | Authorization ownership |
| `version` | INTEGER NOT NULL, default 1 | Share-set version |
| `share_count` | INTEGER NOT NULL, check equals 3 | Fixed |
| `threshold` | INTEGER NOT NULL, check equals 2 | Fixed |
| `status` | TEXT NOT NULL | `PENDING`, `AVAILABLE`, `FAILED`, `CLEANUP_REQUIRED`, `RECONCILIATION_REQUIRED`, optionally `DELETING`, `DELETE_INCOMPLETE` |
| `created_at` | UTC timestamp text | Created time |
| `updated_at` | UTC timestamp text | Last state update |
| `operation_id` | TEXT | Correlates one storage attempt, no secret included |
| `last_error_code` | TEXT nullable | Safe normalized code; never raw exception |

### `share_operations`

| Column | Type/constraint | Purpose |
|---|---|---|
| `record_id` | TEXT FK → secret_records | Parent record |
| `version` | INTEGER NOT NULL | Share set version |
| `share_index` | INTEGER NOT NULL, check 1–3 | Unique share index |
| `provider_id` | TEXT NOT NULL, allow-listed | Fixed provider identity |
| `share_id` | TEXT NOT NULL UNIQUE | Deterministic provider object ID |
| `key_ref` | TEXT NOT NULL | Akeyless key reference, not key material |
| `status` | TEXT NOT NULL | `NOT_STARTED`, `STORED`, `UNKNOWN`, `WRITE_FAILED`, `DELETE_PENDING`, `DELETE_CONFIRMED` |
| `attempt_count` | INTEGER default 0 | Number of provider write attempts; incremented only when a write is actually issued, not on every status change |
| `last_attempt_at` | UTC timestamp nullable | Progress tracking |
| `last_error_code` | TEXT nullable | Normalized safe error code |
| `PRIMARY KEY` | `(record_id, version, share_index)` | No duplicate index per version |

Add constraints/indexes for owner/status lookup and enforce exactly `share_count` rows per record in application logic or a carefully designed transaction (SQLite cannot easily enforce an exact related-row count as a simple row check).

## 2. What is prohibited from metadata

Never store:
- original secret or recovered secret;
- plaintext SSS share;
- raw Akeyless encryption keys;
- Akeyless API access key/token;
- provider connection strings/passwords;
- full request/response bodies;
- exception strings that may contain request payloads.

## 3. Provider object/envelope

Each provider stores one envelope with fields equivalent to:

- `record_id`
- `version`
- `share_index`
- `provider_id`
- `format_version`
- `ciphertext` (exact opaque return from Akeyless)

The Akeyless key reference can be stored in metadata and/or copied as a non-secret envelope field, but it must be validated against the trusted registry. The encryption context must bind record ID, version, index, provider ID and format version. Treat ciphertext as opaque: do not invent a local nonce/tag layout unless the chosen documented Akeyless API explicitly requires one.

## 4. Deterministic ID

Use a deterministic storage key/object ID based on record ID, version and share index, e.g. `firefly:{record_id}:v{version}:share:{share_index}`. It supports safe retry, lookup after uncertain writes, and rollback. Do not include secret contents or hashes of secrets in the key.

## 5. SQLCipher details

- Use a SQLCipher-linked driver, not Python's standard unencrypted `sqlite3` module.
- The database key is stable across restarts and stored outside the database.
- Do not start a network request inside a DB transaction.
- Commit `PENDING` before external work; commit per-share progress as operations finish; commit `AVAILABLE` only after all active-profile writes are confirmed.
- The only supported `three_provider` profile uses a separate SQLCipher file from the legacy five-provider database; old records are preserved but not migrated or shared.
- Do not silently recreate an existing DB that fails to open.
- For a small MVP DB, run supported `PRAGMA cipher_integrity_check` at startup/maintenance and fail closed if it reports problems.
- Do not implement custom metadata MAC in MVP. Do not claim the SQLCipher page HMAC detects logical changes made through a valid DB connection.
