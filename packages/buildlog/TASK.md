# Current Sprint

## Objective

Close the BuildLog v0.2 X Publisher Validation Baseline.

Validate that the existing human-controlled Publisher Boundary can authenticate
a real X account and deliver one explicitly approved, text-only smoke post
without changing generation or Publishing Package behavior.

## Current Task

- [x] Preserve the shared preview, approval, duplicate, receipt, and
  indeterminate-result workflow
- [x] Add an isolated OAuth 2.0 PKCE X adapter
- [x] Configure a Native App with read and write permission
- [x] Complete real OAuth through the local loopback callback
- [x] Verify the authenticated account through `GET /2/users/me`
- [x] Create a dedicated `BL-X-SMOKE-001` local artifact
- [x] Confirm no successful or indeterminate duplicate
- [x] Obtain separate human approval for the exact payload
- [x] Make one client-side POST attempt with no automatic retry
- [x] Persist the successful HTTP 201 receipt and append-only events
- [x] Record the phase boundary and freeze further delivery expansion

## Definition of Done

- [x] Real OAuth 2.0 PKCE authentication succeeds
- [x] The expected X account is resolved from the official identity endpoint
- [x] Preview performs no publication request
- [x] Publication requires exact interactive `PUBLISH` confirmation
- [x] The smoke test uses a unique Test ID and dedicated local artifact
- [x] Exactly one client-side POST attempt is made
- [x] Automatic publication retries remain disabled
- [x] A successful local receipt records HTTP status and external post ID
- [x] Tokens, `.env`, database state, runs, and smoke artifacts remain outside
  Git
- [x] No LinkedIn, generation, prompt, evaluator, or Publishing Package
  behavior changes

## Out of Scope

- Additional social platform adapters
- Automatic, scheduled, or background publishing
- Threads, replies, media, direct messages, analytics, or post management
- Token refresh or hosted OAuth infrastructure
- Channel-specific content generation
- Publishing Package support for X
- Evidence Capture, RAG, retrieval, knowledge graphs, or agent frameworks
- Exactly-once server-side delivery guarantees
