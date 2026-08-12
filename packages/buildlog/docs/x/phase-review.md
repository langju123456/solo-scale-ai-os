# BuildLog X Publisher Phase Review

## Decision

The X Publisher validation phase is closed.

BuildLog has validated a reusable Publisher Boundary across LinkedIn and X.
Platform-specific content generation remains separate. The X adapter consumes
an existing reviewed final artifact; it does not consume the LinkedIn-targeted
Publishing Package.

## Verified Capability

The following real workflow completed successfully on 2026-07-30:

```text
Reviewed final artifact
        |
        v
Exact preview
        |
        v
Explicit human approval
        |
        v
One client-side POST attempt
        |
        v
Durable receipt and event trace
```

Validation evidence:

- OAuth 2.0 Authorization Code with PKCE completed through a local loopback
  callback.
- `GET /2/users/me` resolved the expected X account.
- The token contained only `tweet.read`, `tweet.write`, and `users.read`.
- Preview reported no successful or indeterminate duplicate.
- `BL-X-SMOKE-001` received a successful HTTP 201 response.
- The external post ID, content hash, receipt, and append-only events were
  persisted locally.
- No automatic retry occurred.

The smoke run, token, database rows, and receipt payload remain local and are
not repository artifacts.

## Root Cause Found During Validation

The first authorization attempts failed before callback handling. The
implementation was not changed. Configuration review found two input errors:

- a visually ambiguous Client ID character had been transcribed incorrectly;
- the Developer Console callback value contained an accidentally duplicated
  path suffix.

Correcting the exact Client ID and callback configuration completed OAuth
without code changes. This confirms that the failure was Developer Console
configuration, not PKCE or callback-server behavior.

## Accepted Boundary

The shared Publisher Boundary owns:

- exact preview;
- explicit approval;
- successful and indeterminate duplicate blocking;
- at most one client-side POST attempt;
- no automatic retry;
- durable receipts and trace events.

The X adapter owns only:

- OAuth 2.0 PKCE authentication;
- authenticated identity lookup;
- X text validation and weighted length;
- `POST /2/tweets` transport;
- safe response parsing.

## Claims Not Accepted

This phase does not establish:

- exactly-once delivery by X;
- autonomous or scheduled publishing;
- refresh-token support;
- media, threads, replies, direct messages, or analytics;
- X support in the Publishing Package;
- channel-specific X content generation;
- multi-user product authentication.

The precise guarantee is:

> One explicitly approved client-side publication attempt, with no automatic
> retry, local duplicate suppression, and indeterminate-result blocking.

## Complexity Review

The X adapter reused the existing PublishingService rather than introducing a
second publication workflow. No platform registry, scheduler, queue, hosted
backend, or generalized content model was required.

No further Publisher abstraction is justified by this validation. Delivery is
not the current product bottleneck.

## Next Decision

Freeze X delivery implementation after this milestone. Do not add another
platform adapter until real use shows that transport is blocking value.

The next product validation should focus on whether the same reviewed
engineering evidence can produce distinct LinkedIn and X artifacts that the
user is willing to publish with low editing effort.
