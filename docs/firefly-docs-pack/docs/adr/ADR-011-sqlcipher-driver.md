# ADR-011: SQLCipher Python driver selection

**Status:** Accepted (verified on the development machine; see below)

## Context
Metadata must be encrypted at rest. Python's standard `sqlite3` is not sufficient for the production metadata connection; the driver must link to SQLCipher.

## Evaluation checklist
- Installable on the team's actual OS/Python version.
- Reports a real SQLCipher version via supported PRAGMA.
- Correct database key opens a database; incorrect key cannot read it.
- `cipher_integrity_check` supported and tested in the installed build/version.
- Can use parameterized SQL and transactions safely.
- Maintenance status and license checked.
- Build/install instructions documented for the team's operating system.

## Decision
Use **`sqlcipher3` 0.6.x** (`requirements.txt`: `sqlcipher3>=0.5,<1`; tested at 0.6.4 on Windows 11, Python 3.13). Its package metadata does not state a license; confirm it before any distribution.

### Verified key and open sequence (`app/db/sqlcipher.py`)
1. `sqlcipher3.connect(path)`, then `PRAGMA key = '<key>'` before any other statement.
2. `PRAGMA cipher_version` must return a non-empty value (observed `4.19.0 community`); otherwise startup fails because the driver is not linked to SQLCipher.
3. `SELECT count(*) FROM sqlite_master` forces a real read; a wrong key or damaged header page fails here.
4. The repository then checks that both tables exist and that `PRAGMA cipher_integrity_check` returns **no rows**.

### Fail-closed behavior (tested in `tests/test_sqlcipher_fail_closed.py`)
- Normal startup never creates the database. A missing file is an error.
- First-time creation is the separate, explicit `python -m scripts.init_db` (`SecretRepository.initialize`), which refuses to touch an existing file.
- A wrong key, an unencrypted SQLite file, a garbage file, a database without the expected schema, and a page-level corruption (caught by `cipher_integrity_check`) all stop startup, and the file is left byte-for-byte unchanged.

### Not verified
- Maintenance status of the package and its upstream.
- That the integrity check detects every possible corruption pattern. It covers page authentication, not logical edits made through a valid connection (see ADR-009).

## Consequences
Do not silently fall back to standard sqlite3 if SQLCipher fails to install or verify. Stop and ask for a decision rather than degrading to unencrypted metadata.
