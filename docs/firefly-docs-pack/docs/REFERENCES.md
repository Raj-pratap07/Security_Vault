# Official references

These references informed the architecture and implementation constraints. Check the current documentation before choosing SDK-specific parameters or pinning package versions.

## FastAPI
- Response models and filtering: https://fastapi.tiangolo.com/tutorial/response-model/
- Lifespan/startup/shutdown: https://fastapi.tiangolo.com/advanced/events/
- Testing: https://fastapi.tiangolo.com/tutorial/testing/
- Dependencies/security: https://fastapi.tiangolo.com/tutorial/security/

## Akeyless
- Encryption keys and supported key types (includes AES256GCM): https://docs.akeyless.io/docs/encryption-keys
- Encrypt API (`/encrypt`, `input-format`, encryption context): https://docs.akeyless.io/reference/encrypt
- Decrypt API (`/decrypt`, Base64 ciphertext, output format, context): https://docs.akeyless.io/reference/decrypt
- API key authentication (POC caveat): https://docs.akeyless.io/docs/auth-with-api-key
- Best practices: https://docs.akeyless.io/docs/best-practices
- Access roles/RBAC: https://docs.akeyless.io/docs/access-roles

## SQLCipher / SQLite
- SQLCipher API and `cipher_integrity_check`: https://www.zetetic.net/sqlcipher/sqlcipher-api/
- SQLCipher overview: https://www.zetetic.net/sqlcipher/
- Python sqlite3 reference (comparison only; standard sqlite3 is not SQLCipher): https://docs.python.org/3/library/sqlite3.html

## Providers
- Upstash Redis docs: https://upstash.com/docs/redis/overall/getstarted
- Neon Python/connection guides: https://neon.tech/docs/guides/python
- Firestore server SDK quickstart (Python): https://firebase.google.com/docs/firestore/quickstart-server
- Firestore add/update data: https://firebase.google.com/docs/firestore/manage-data/add-data

## Cryptography and secrets
- AES-GCM semantics, AAD, nonce uniqueness: https://cryptography.io/en/stable/hazmat/primitives/aead/
- OWASP Secrets Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html

## AI coding instructions
- GitHub Copilot custom instructions: https://docs.github.com/en/copilot/concepts/prompting-and-context/custom-instructions
- VS Code custom instructions / `AGENTS.md`: https://code.visualstudio.com/docs/agent-customization/custom-instructions
- Google Antigravity rules: https://antigravity.google/docs/rules/

## Source selection policy
Use official docs for API behavior. Verify third-party crypto libraries independently (maintenance, license, secure randomness, byte handling, and tests). This documentation intentionally does not prescribe a specific SSS or SQLCipher Python package before the compatibility spikes are completed.
