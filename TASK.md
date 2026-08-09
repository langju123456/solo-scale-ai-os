# Current Sprint — Casebook v0.1 Dogfood

## Sprint goal

Turn one completed AI-assisted engineering incident into a private, evidence-backed
learning case without confusing agent execution speed with human mastery:

```text
explicitly selected evidence
→ byte-for-byte archive + SHA-256 receipts
→ strict LearningCase
→ interview practice packet
→ five append-only practice gates
→ visual current position + next action
```

## In scope

- [x] Freeze the local-first Casebook product contract.
- [x] Add strict case, evidence, attempt, and derived-mastery contracts.
- [x] Archive explicitly selected evidence with SHA-256 receipts.
- [x] Generate a deterministic interview practice packet.
- [x] Record append-only Explain, Trace, Rebuild, Debug, and Defend attempts.
- [x] Show evidence integrity, mastery position, and the next action.
- [x] Generate an accessible, zero-JavaScript local Control Tower.
- [x] Dogfood one sanitized real engineering case at `0/5` without claiming mastery.
- [x] Verify the full existing and new test suite, Ruff, mypy, and package build.

## Local preparation completed

- Baseline commit created on local `main`.
- Hardening revision `9fd720b` passes locally across Ruff, `mypy src tests`, 28 tests, the installed demo, and isolated package builds.
- Planning contracts, evidence-backed transitions, and approval enforcement are covered by tests.
- GitHub Project setup and Vercel evolution are documented.
- Public-safe conversation distillation, X/LinkedIn drafts, and editable architecture source are prepared.

Push, PR creation, release, deployment, cloud sync, and publishing remain separate
human-gated actions.

## Definition of done

- CI is green.
- One real case can be created in under ten minutes.
- Archived evidence passes checksum and byte-size verification.
- The case begins at `0/5` with Explain as its next practice action.
- Practice attempts are append-only and a later `needs-work` result removes readiness.
- No secrets or raw private chats are committed.
- At least three practice stages are completed during dogfood before SaaS expansion.

## Current gate

Implementation is complete locally. The real learning gate remains open:

```text
source-grounded-citations
Engineering: RESOLVED
Evidence integrity: PASS (2/2 files)
Learning: CAPTURED (0/5)
Next action: EXPLAIN
```

No practice gate was marked complete by the implementing agent. The operator must produce
the receipt for each pass.
