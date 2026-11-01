# ADR-010: SSS library selection

**Status:** Accepted (dependency adopted; evaluation gaps listed below)  
**Decision owner:** Firefly team

## Context
Firefly requires Shamir Secret Sharing over secret bytes with 3 shares and threshold 2. It must support a 4 KiB maximum synthetic demo secret and use cryptographically secure randomness. Implementing finite-field/polynomial operations from scratch is out of scope.

## Evaluation checklist
- Actively maintained package/repository and release cadence.
- License suitable for hackathon/demo use.
- Supported Python version and operating systems.
- Arbitrary byte input support at the required size, or documented safe framing.
- User-configurable total shares and threshold.
- Cryptographically secure RNG documented and implemented.
- Correct duplicate/malformed share handling.
- Tests or reviewable reference implementation.
- Reconstruct behavior and error behavior are explicit.

## Decision
Use **`shamirs` 4.x** (`requirements.txt`: `shamirs>=4,<5`; installed and tested at 4.0.0, MIT license per its package metadata). Firefly never implements finite-field or polynomial arithmetic itself.

### How it is used
- `shamirs.shares(int, 3, threshold=2)` and `shamirs.interpolate` operate on integers modulo the library default prime `2**127 - 1`.
- The secret is split into **15-byte (120-bit) chunks** so every chunk integer is below the modulus. Each provider's share is a JSON `ShareBundle` (`index` plus one `{length, share}` fragment per chunk); the explicit `length` preserves leading zero bytes.
- A 4 KiB secret produces about 274 chunks, roughly 22 KB per share (about 30 KB Base64-encoded for Akeyless). The size test in `tests/test_redaction_and_limits.py` pins this.
- `SSSService` accepts only 3 shares / threshold 2 and rejects fewer than two shares, duplicate indices, mismatched fragment counts or lengths, and malformed bundles (`decode_share` accepts only the bundle format).

### Verified in this repository
- Source inspection: randomness is `secrets.randbelow` (a CSPRNG).
- Tests: all three two-share combinations recover exact bytes, including leading and embedded zero bytes; one share is rejected before reconstruction.

### Not verified (do not claim)
- Current upstream maintenance cadence and release history.
- An independent review or formal proof of the library's threshold-security properties.
- Behavior on Python versions other than the 3.13 used for development.

## Consequences
No custom SSS math. The wrapper exposes only split/rebuild operations with fixed configuration. Chunking and the bundle format are Firefly code covered by Firefly tests; they are not part of the library's guarantees.
