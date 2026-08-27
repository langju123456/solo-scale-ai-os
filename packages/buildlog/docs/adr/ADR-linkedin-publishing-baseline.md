# ADR: LinkedIn Publishing Baseline

- Status: Accepted
- Date: 2026-07-29
- Iteration: BuildLog v0.2 LinkedIn Publishing Baseline

## Context

BuildLog needs its first external publishing destination. Generation,
evaluation, revision, and human review already produce an inspectable final
artifact. Publishing must consume that existing artifact without coupling
LinkedIn to the AI pipeline.

Human approval is mandatory. A failed or indeterminate publication must not
invalidate a completed BuildLog run. Future destinations are planned, but only
text-only personal-member LinkedIn publishing is in this iteration.

## Decision

1. Publishing is an additive downstream output capability.
2. A small `Publisher` boundary separates application orchestration from the
   LinkedIn HTTP adapter.
3. OAuth configuration, callback handling, identity resolution, and credential
   storage are isolated from publishing-domain logic.
4. Credentials live in a restricted user-level file, never in run artifacts
   or SQLite.
5. Publishing resolves an existing completed run's `final` artifact and never
   reruns generation. The resolved path must remain under the configured
   `runs/` root and its raw file SHA-256 must match the artifact index.
6. Preview displays the exact publishable text, account, length, hash, and
   duplicate state without a network submission.
7. Publication requires an explicit approval flag and an interactive
   `PUBLISH` confirmation.
8. Successful, failed, and indeterminate attempts produce safe receipts.
9. A previous successful receipt for the same platform, account, and content
   hash blocks publication by default.
10. A timeout, transport interruption, user interrupt during submission,
    unexpected 2xx, HTTP 408, HTTP 5xx, or missing success identifier after
    submission is indeterminate and is never retried automatically. A matching
    unresolved receipt blocks a later manual attempt unless the human inspects
    LinkedIn and explicitly overrides it.
11. Publishing events append to the run's existing `events.jsonl` stream using
    the current `ObservationEvent` schema.
12. The LinkedIn adapter uses the current `/rest/posts` endpoint with a
    centralized API version and Rest.li headers.

## Alternatives Considered

### Direct HTTP call from the CLI

Rejected because it would combine user interaction, credentials, payload
construction, error mapping, persistence, and network behavior in one module.

### LinkedIn logic inside the generation pipeline

Rejected because publishing has different failure semantics and must not
change a completed generation run.

### Generic multi-platform framework

Rejected as premature. The domain boundary includes only the minimum stable
request, result, service, and adapter contract needed for LinkedIn.

### MCP integration

Rejected because it belongs to a future tool-integration baseline and adds no
value to this local OAuth vertical slice.

### Automatic publishing

Rejected because human review and explicit publication approval are product
invariants.

### Credentials inside run directories

Rejected because runs are inspectable engineering artifacts and are not a
credential boundary.

### Legacy `/v2/ugcPosts`

Not selected because the current Posts API states that it replaces
`ugcPosts`. The self-service guide still shows the legacy endpoint, so the
first manual smoke test must validate `/rest/posts` with the actual app
products and scopes.

## Consequences

- The implementation adds several small modules and one additive receipt
  table.
- Generation remains platform-agnostic.
- OAuth and API behavior are independently mockable.
- Tokens can be deleted without touching runs or SQLite.
- Duplicate and indeterminate outcomes can be reviewed before another attempt.
- Future publisher adapters can implement the same narrow boundary.
- A controlled real smoke test confirmed that LinkedIn accepted the
  OIDC-sub-derived Person URN for this app. The mapping remains labeled as
  inferred because the official documents do not explicitly state the
  equivalence.

## Compatibility Fix

`06_final.md` contains a fixed human-review warning. The artifact contract
remains unchanged. A downstream resolver removes only that exact footer from
the text submitted to a publisher so the safety instruction itself is not
posted.

## Future Integration Notes

- A future approved tool may call `PublishingService`; this iteration does not
  implement tool calling.
- Evidence collection remains upstream and independent.
- Media support may extend the LinkedIn adapter without changing generation.
- Scheduling may orchestrate previously approved requests in a future workflow
  baseline.
- Publishing receipts are operational records, not engineering memory.
- OAuth credentials are security state, not retrieval or memory data.
