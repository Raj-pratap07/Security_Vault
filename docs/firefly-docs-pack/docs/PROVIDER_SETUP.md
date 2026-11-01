# Provider setup — Firefly

The application loads `.env` from the repository root and does not print its
values. Keep `.env`, provider credentials, and Firebase service-account files
out of source control.

The only supported `FIREFLY_PROFILE` is `three_provider`, which uses Upstash,
Neon, and Firestore with 3 shares / threshold 2. It is the default when unset;
other profile values are rejected. The app rejects production environment
configuration because its authentication is demo-only. The profile uses a
separate SQLCipher metadata database file (based on `SQLCIPHER_DATABASE_PATH`
with `.three-provider` added to the filename); old metadata is preserved but
not migrated or read.

## Akeyless

Use API-key authentication for this local proof of concept. Configure
`AKEYLESS_ACCESS_ID`, `AKEYLESS_ACCESS_KEY`, and the `upstash`, `neon`, and
`firebase` key references. Each active key must be enabled for AES-256-GCM and
accessible to the configured Akeyless identity.

The service calls the documented `/auth`, `/encrypt`, and `/decrypt` REST
operations. Share bytes are Base64-encoded before Encrypt with
`input-format=base64`; Decrypt requests `output-format=base64`. The opaque
ciphertext returned by Akeyless is stored unchanged. Both operations receive
the same context fields: `record_id`, `version`, `share_index`, `provider_id`,
and `format_version`.

References:

- [Akeyless `/auth`](https://docs.akeyless.io/reference/auth)
- [Akeyless `/encrypt`](https://docs.akeyless.io/reference/encrypt)
- [Akeyless `/decrypt`](https://docs.akeyless.io/reference/decrypt)

## Upstash

Set `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` for a dedicated
database. The adapter issues Redis `SET`, `GET`, `DEL`, and `PING` commands
through the REST endpoint. Share writes use no TTL. Configure the database
eviction policy so ciphertext records are not evicted.

## Neon

The adapter uses psycopg over TLS. Provision a dedicated table before running
the application; the application does not create or migrate provider schemas.
Set the schema and table variables in `.env`. The defaults are
`public.firefly_shares`.

Create a table with this shape:

```sql
CREATE TABLE public.firefly_shares (
    share_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    share_index SMALLINT NOT NULL CHECK (share_index BETWEEN 1 AND 3),
    provider_id TEXT NOT NULL,
    key_ref TEXT NOT NULL,
    format_version SMALLINT NOT NULL,
    ciphertext TEXT NOT NULL,
    UNIQUE (record_id, version, share_index)
);
```

If custom schema/table names are configured, substitute those identifiers in
the DDL and in `NEON_SCHEMA` / `NEON_TABLE`. Grant the database role access
only to its dedicated table. The adapter fails closed when the configured
table is absent or incompatible.

## Firebase Firestore

Set `FIREBASE_PROJECT_ID`, `FIRESTORE_COLLECTION`, and
`GOOGLE_APPLICATION_CREDENTIALS` to a local service-account file, or configure
Application Default Credentials. Do not commit the service-account file. Grant
the identity access only to the dedicated collection.

Firestore collections are logical paths rather than standalone resources: an
empty collection cannot be provisioned independently. The configured path is
used as the dedicated collection; its first real share write materializes it.

## Credential preflight and test safety

Run unit tests with:

```powershell
python -m pytest -q tests
```

Unit tests use fake clients and never contact live providers. A live end-to-end
store/recover test creates remote ciphertext records; use only dedicated test
projects and a synthetic secret, then verify cleanup. Do not use production
credentials or secrets for integration tests.
