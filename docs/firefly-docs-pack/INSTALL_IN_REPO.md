# Add this documentation pack to your existing Firefly repository

This pack is a documentation/agent-instructions scaffold, not an implementation commit. Apply it to the existing cloned repo carefully.

1. Inspect `git status` and create a branch, e.g. `docs/firefly-implementation-plan`.
2. Copy/merge `docs/` into your repository. If files with the same names already exist, compare them instead of overwriting blindly.
3. Add `AGENTS.md` at the repository root if no project-level file exists. If one exists, merge its useful instructions with the Firefly constraints; do not replace unrelated project guidance.
4. Merge `.github/copilot-instructions.md` with any existing Copilot instructions.
5. Merge `GEMINI.md` and `.agents/rules/firefly.md` only if these filenames/locations are not already used by your project; preserve existing local rules.
6. Merge `.env.example` with the project's existing example. Do not replace any real local `.env` file.
7. Append entries from `.gitignore.additions` to the existing `.gitignore`. Do not replace an existing `.gitignore`.
8. Review `docs/DECISIONS.md` and confirm the pending SSS-library and SQLCipher-driver spikes before implementation.
9. Commit the documentation separately before starting code changes.

Suggested first AI-agent prompt:

> Read `AGENTS.md`, `docs/PRD.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, and `docs/IMPLEMENTATION_TASKS.md`. Inspect this repository and git status. Do not edit any files yet. Report the current project tree, Python/runtime, existing dependencies, what conflicts with the Firefly documentation, and a proposed first implementation milestone. The current demo design is 3 shares / threshold 2 across Upstash, Neon, and Firestore, Akeyless AES-256-GCM, SQLCipher SQLite metadata, Python-only, trusted backend. Do not implement crypto from scratch or change the architecture without asking.
