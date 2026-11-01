# AI Agent Task Board — Firefly

Use the tasks in order. Do not ask one agent to build the entire system.

## T0 — Repository audit (no edits)
**Prompt:** Inspect current repo, git status, Python version, dependencies, tests and existing architecture. Compare with `AGENTS.md`/docs. Report conflicts and a proposed file plan. Do not edit files.

## T1 — SQLCipher compatibility proof
**Prompt:** Evaluate SQLCipher Python drivers for current OS/Python. Implement only a minimal isolated test that creates an encrypted DB, verifies SQLCipher is active, closes/reopens with the correct key, fails with wrong key, and runs integrity check. Document package/version and exact commands. No schema yet.

## T2 — SSS dependency decision
**Prompt:** Research maintained Python SSS packages that support 3 shares/threshold 2 and byte payloads up to 4 KiB. Compare docs/license/maintenance/randomness/tests. Do not write SSS math. Propose a package and add tests only after human approves selection.

## T3 — Akeyless binary round-trip
**Prompt:** Using a synthetic test byte payload only, test Akeyless authentication, Encrypt with Base64 input and context, then Decrypt with Base64 output and identical context. Verify byte-for-byte equality and context-mismatch failure. Never print tokens or payload plaintext. Stop if account limits prevent three keys.

## T4 — Metadata repository
**Prompt:** Implement the tables and repository from `DATA_MODEL.md` using the verified SQLCipher driver. Add tests for transactions, status transitions, owner lookup, and fail-closed open behavior. Don't integrate provider calls.

## T5 — Provider contract and one adapter
**Prompt:** Implement the shared interface and one adapter with deterministic IDs, idempotent put/get/delete, timeouts and normalized unknown outcomes. Test it with test credentials or a fake. No orchestrator yet.

## T6 — Remaining provider adapters
**Prompt:** Add one provider adapter at a time against the frozen interface. Add isolated integration tests. Do not change orchestrator or schema unless necessary; report any interface mismatch for human approval.

## T7 — Store orchestration
**Prompt:** Implement storage state machine from `FAILURE_HANDLING.md` with fake adapters and injected failures first. Ensure status commits are short transactions and `AVAILABLE` requires all three confirmed writes.

## T8 — Recovery orchestration
**Prompt:** Implement recovery with mocked providers/Akeyless first. Validate distinct indexes/version/context; collect two distinct valid shares; fail below threshold; clean mutable buffers on all paths.

## T9 — API wiring
**Prompt:** Implement routes/schemas/auth according to `API_CONTRACT.md`. Routes stay thin. Test status codes and ensure sensitive response/log handling.

## T10 — Final audit
**Prompt:** Run the tests in `TEST_PLAN.md`, search logs/test output for a synthetic canary, inspect git diff and `.gitignore`, verify no secrets tracked, and produce a truthful pass/fail matrix with exact commands.
