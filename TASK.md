# Current Sprint — Learning Traceability golden case

The bounded branch goal is one inspectable Conversation RAG chunking/retrieval chain. It
must preserve existing Resume, Casebook, and Conversation RAG boundaries.

## Learning Traceability gate

- [x] Shared typed contracts preserve seven distinct truth stages.
- [x] One real capability resolves to current branch, commit, files, symbols, and tests.
- [x] Contribution ownership stays unknown where repository evidence cannot prove it.
- [x] Engineering state and L0 human mastery render separately.
- [x] Explain and Trace exercises start without auto-completing mastery.
- [x] Explain and Trace responses save privately as pending review without mastery promotion.
- [x] Target-JD relevance and resume-claim eligibility are separate gates.
- [x] Twelve non-overwriting private artifacts and an evidence-hash cache are produced.
- [x] The existing local UI exposes the clickable graph and bounded grounded source views.
- [x] Final deterministic checks and local browser E2E.
- [ ] Human review; no commit or push is authorized for this branch.

## Previous gate — Resume Workspace boundary hardening

Conversation RAG v0.2 is merged and remains the knowledge-plane foundation. The current
local follow-up gate is the separately human-triggered Resume Workspace candidate slice.

## Resume Workspace gate

- [x] Resume facts derive only from an operator-supplied Candidate Profile.
- [x] Retrieval matches are labeled lexical candidates and retain full hash/source lineage.
- [x] The legacy Evidence-Agent-to-resume action is disabled.
- [x] Internal and external destinations use private, symlink-rejecting, atomic writes.
- [x] External bundles publish from staging without overwriting an existing application.
- [x] `delivery.json` records internal-ready, pending, saved, or failed state.
- [x] Application libraries inside the Git repository are rejected by the UI workflow.
- [ ] Fresh review and human Push gate.

No Resume candidate may update Casebook, BuildLog, an application system, or a publishing
surface automatically.

## Foundation — Private Conversation RAG v0.2

## Foundation carried forward

Casebook v0.1 is implemented and locally verified. Its real learning gate remains open:

```text
source-grounded-citations
Engineering: RESOLVED
Evidence integrity: PASS (2/2 files)
Learning: CAPTURED (0/5)
Next action: EXPLAIN
```

Conversation RAG must not mark any practice gate complete or convert an LLM candidate
into a confirmed case automatically.

## Sprint goal

Turn the operator's growing local AI-assisted work history into a private, searchable,
citation-backed discovery plane:

```text
observed Codex local JSONL + operator-supplied ChatGPT export
+ bounded BuildLog evidence scope
→ defensive full-source rescan and normalization
→ private checksum-backed index
→ deterministic retrieval
→ custom code-controlled LLM search/refinement
→ evidence-linked candidates and explicit gaps
```

## In scope

- [x] Inspect local source formats without exposing transcript content.
- [x] Freeze the private ingestion, retrieval, and evidence-agent contract.
- [x] Add strict document, chunk, sync, retrieval, and agent-run contracts.
- [x] Rescan observed Codex JSONL defensively with stable thread identity.
- [x] Accept operator-supplied ChatGPT JSON/ZIP exports; follow valid `current_node`
  ancestry and exclude sibling branches.
- [x] Split long messages and artifacts into deterministic overlapping segments.
- [x] Index BuildLog narrative Markdown and schema-specific safe projections from events,
  plans, evaluations, run metadata, and timelines.
- [x] Apply best-effort control-plane and common-secret redaction before persistence.
- [x] Add an idempotent, permission-restricted SQLite FTS index.
- [x] Fuse full-text and metadata retrieval with bounded CJK bigrams and mixed-script
  splitting.
- [x] Verify stored body/FTS projection integrity and repair it through approved resync.
- [x] Add a custom bounded Evidence Agent with structured output and citation checks.
- [x] Persist the exact fitted excerpts actually visible to the model.
- [x] Expose sync, status, reset, search, and agent commands through the SoloScale CLI.
- [x] Add Conversation RAG state, counts, run status, and exact next action to Control Tower.
- [x] Dogfood retrieval over SoloScale and BuildLog history.
- [x] Add a public synthetic bilingual retrieval/context golden gate.
- [x] Verify the full suite, Ruff, mypy, package build, privacy, and lineage.

## Local preparation completed

- Baseline commit created on local `main`.
- Hardening revision `9fd720b` passes locally across Ruff, `mypy src tests`, 28 tests, the installed demo, and isolated package builds.
- Planning contracts, evidence-backed transitions, and approval enforcement are covered by tests.
- GitHub Project setup and Vercel evolution are documented.
- Public-safe conversation distillation, X/LinkedIn drafts, and editable architecture source are prepared.

Push, PR creation, release, deployment, cloud sync, and publishing remain separate
human-gated actions.

## Definition of done

- Re-running sync does not duplicate a Codex thread moved into archives.
- Codex selects observed user/assistant message records. A valid ChatGPT `current_node`
  selects only active ancestry; known hidden flags and sibling branches are excluded.
- BuildLog indexes three narrative Markdown bodies plus schema-specific projections from
  `events.jsonl`, `02_plan.json`, `04_evaluation.json`, `run_metadata.json`, and
  `timeline.json`; raw prompt/tool/stdout/stderr bodies are excluded from structured
  projections, while narrative Markdown remains operator-reviewed searchable text.
- Control-plane blocks and common credential shapes receive best-effort redaction; this is
  not a completeness guarantee, so promotion still requires human review.
- Every stored chunk has document and content-hash lineage.
- Search results resolve to stored chunks and use stable deterministic ordering.
- CJK bigrams and mixed Latin/CJK splitting stay within the query-token budget.
- Search detects stored-body or FTS-projection corruption; an approved source resync
  rebuilds the affected projection.
- Agent loops stop within their configured budgets.
- Every candidate factual claim cites an in-context chunk from the same agent run, and the
  receipt retains the exact fitted excerpt visible to the model.
- Unsupported conclusions remain explicit gaps.
- No raw conversation, private index, or agent run is committed.
- No candidate is promoted, published, deployed, or written to a resume automatically.

## Current gate

Implementation and real local dogfood attempts exist on a separate local branch. Final
verification and review are still open. Push, PR, model download, hosted service,
deployment, publishing, and automatic promotion are not part of this gate.

```text
Source adapters → IMPLEMENTED
Private index → IMPLEMENTED
Evidence Agent → IMPLEMENTED; FRESH REVIEWS PASS
Dogfood sync/search → COMPLETED LOCALLY
Retrieval-only golden gate → PASS; SEMANTIC QUALITY NOT EVALUATED
Full verification → PASS — 229 tests, Ruff, strict mypy, sdist and wheel
Human promotion → BLOCKED
```

The synthetic bilingual retrieval-only gate currently records Recall@5 `1.0`, MRR `1.0`,
store neighbor-expansion recall `1.0`, neighbor-expansion forbidden-context precision
`1.0`, and deterministic repeated/rebuilt
rankings. One targeted local run measured a maximum search latency of `1.863 ms`; it is a
single local maximum, not a percentile or service commitment. Semantic faithfulness,
answer relevancy, and reasoner-output quality are not evaluated and remain human-gated or
future opt-in evaluation work.
