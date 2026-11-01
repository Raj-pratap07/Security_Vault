# AI Coding Agent Workflow — Firefly

Use this process with GitHub Copilot, Antigravity, Gemini-based agents, or another coding assistant.

## 1. Do not ask for the entire app in one prompt

Give the agent one milestone and a small definition of done. Ask it to inspect the repository first and report any conflict with existing code before modifying files.

## 2. Standard task prompt

Copy and adapt this prompt:

> Read `AGENTS.md`, `docs/DECISIONS.md`, the relevant architecture/API/data-model docs, and inspect the existing repository and git status. Implement only **[one named milestone]**. Keep the only supported `three_provider` profile at 3 shares / threshold 2 on Upstash, Neon, and Firestore. Do not add a five-provider runtime profile or present the demo as production-ready. Preserve Akeyless AES-256-GCM, SQLCipher metadata, and the trusted-backend threat model. Do not add unrelated features. Use maintained libraries; do not write custom cryptographic algorithms. Add/update tests in the same change. Run the relevant test commands and report exact results. Show the files changed, assumptions, remaining risks, and any tests not run. Do not claim success without evidence.

## 3. Recommended prompt sequence

1. **Repository audit only:** inspect and report current tree, Python version, git status, existing dependencies, and conflicts. Do not edit.
2. **SQLCipher spike:** install/verify driver and provide a minimal test; do not implement the whole schema yet.
3. **SSS library spike:** compare viable packages and recommend one with evidence; do not implement SSS arithmetic manually.
4. **Akeyless spike:** prove one binary test payload round-trips with context; redact secrets from logs.
5. **Metadata schema/repository:** implement tables and short transaction methods; add unit/integration tests.
6. **One adapter:** choose one provider and prove put/get/delete/idempotency.
7. **Remaining adapters one by one:** reuse the same interface; no divergent API.
8. **Orchestrator store path:** use fake adapters/fault injection first.
9. **Orchestrator recovery path:** test all three 2-share combinations in `three_provider`.
10. **FastAPI endpoints:** wire schema/auth/errors to the tested services.
11. **Full system test:** run acceptance/failure tests and inspect logs/diff.

## 4. Review every agent change

Before accepting a diff, ask:
- Did it introduce an unapproved dependency or change a locked design decision?
- Are secrets, ciphertext payloads, tokens, connection strings or exception bodies logged?
- Are all network calls outside DB transactions?
- Does every write outcome update metadata?
- Is timeout/UNKNOWN handled without blind deletion/retry?
- Does recovery enforce the active profile's threshold of distinct valid shares from the same record/version?
- Does it use Akeyless's actual API format and exact encryption context?
- Is SQLCipher genuinely used, not standard sqlite3?
- Are tests meaningful and were they run?

## 5. When to stop and ask a human

Stop if:
- Akeyless plan cannot support five keys/required operations;
- no suitable SSS library passes evaluation;
- SQLCipher cannot be installed or verified on the target environment;
- the current account/provider free-tier limitations block a requirement;
- documentation conflicts with actual SDK behavior;
- a code change would alter cryptographic semantics, threshold, provider mapping, schema compatibility, or public API behavior.

## 6. Parallel agents

Parallelize research and independent adapter spikes only when interfaces are already frozen. Do not ask multiple agents to edit `orchestrator.py`, the schema, and provider contracts simultaneously. Assign an owner to integrate and test the shared interfaces.
