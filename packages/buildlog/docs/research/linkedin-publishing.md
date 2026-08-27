# LinkedIn Publishing Research

Research date: 2026-07-29

This document distinguishes confirmed LinkedIn behavior from implementation
inference and manual-verification requirements.

## Confirmed Official Behavior

### Product and permissions

The self-service [Share on LinkedIn
product](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)
grants `w_member_social`, which is required to publish on behalf of an
authenticated member.

BuildLog requests:

```text
openid profile w_member_social
```

`openid` and `profile` support authenticated identity through LinkedIn OIDC.
`email` is unnecessary and is not requested. OIDC is useful for identity; the
publishing permission itself is `w_member_social`.

### OAuth flow

LinkedIn documents the OAuth 2.0 Authorization Code flow at:

- authorization: `https://www.linkedin.com/oauth/v2/authorization`
- token exchange: `https://www.linkedin.com/oauth/v2/accessToken`

The token response includes `access_token` and `expires_in`; it may include
the granted `scope` and, for specially enabled partners, refresh-token fields.
LinkedIn documents a typical access-token lifetime of 60 days.

[Programmatic refresh
tokens](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens)
are limited to approved Marketing Developer Platform partners. BuildLog does
not assume that capability. Expired tokens require a new authorization flow.
Because v0.2 consumes identity through userinfo and implements no refresh flow,
unused ID and refresh tokens are not retained in the local token file.

### Identity

LinkedIn OIDC documents:

- `GET https://api.linkedin.com/v2/userinfo`
- `sub` as the subject identifier
- `name`, `given_name`, `family_name`, `picture`, and `locale` profile claims
- a pairwise subject type

BuildLog uses userinfo rather than decoding an unverified ID token. Raw ID
tokens are not consumed as trusted identity data.

### Post endpoint

The current [Posts
API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
states that it replaces `ugcPosts`, supports `w_member_social`, and creates a
text post with:

```text
POST https://api.linkedin.com/rest/posts
```

Required headers are:

```text
Authorization: Bearer <token>
Content-Type: application/json
Linkedin-Version: YYYYMM
X-Restli-Protocol-Version: 2.0.0
```

BuildLog centralizes the endpoint and version. The initial documented version
is `202607`, matching the current official documentation view; it remains
environment-configurable because LinkedIn versions are time-bounded.

The selected endpoint requires a Person URN for a member author. The [Post
schema](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/post-api-schema)
defines the form as:

```text
urn:li:person:{id}
```

A successful create returns HTTP `201`; the post identifier is returned in
the `x-restli-id` response header.

The Posts API models `commentary` using LinkedIn's
[`little` text format](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format?view=li-lms-2026-07).
Its reserved control characters must be escaped when they are intended as
plain text. BuildLog v0.2 does not implement mentions or structured hashtags;
the adapter deterministically escapes reserved characters so the reviewed
artifact is rendered as literal text.

### Rate limits

LinkedIn applies application and member limits and returns HTTP `429` when a
limit is exceeded. The legacy self-service Share guide publishes daily UGC
limits, but the current Posts API documentation does not establish that those
same numeric limits are the `/rest/posts` contract. BuildLog therefore does not
hardcode a quota. The app's actual allowance must be reviewed in Developer
Portal Analytics.

## Implementation Inference

The self-service Share guide still demonstrates legacy `/v2/ugcPosts`, while
the newer Posts API explicitly says it replaces `ugcPosts` and lists
`w_member_social`. BuildLog therefore selects `/rest/posts` as the current
adapter contract and records the exact endpoint and API version in receipts.

The Share guide directs developers to OIDC for the Person URN, while the OIDC
userinfo contract exposes `sub` rather than a field named `person_id`. Both
the OIDC subject and LinkedIn person identifiers are application-scoped. The
adapter therefore derives `urn:li:person:{sub}` and labels the identity source
as `oidc_userinfo_sub_inferred`. The mapping remains labeled as inferred even
after runtime acceptance because the official documents do not explicitly
state the equivalence.

## Manual Verification Result

The controlled OAuth and publication smoke test completed on 2026-07-29 and
confirmed:

1. the Developer App had both Share on LinkedIn and OIDC products enabled
2. the returned scopes included `openid`, `profile`, and `w_member_social`
3. userinfo returned a stable non-empty `sub`
4. `urn:li:person:{sub}` was accepted as the author by `/rest/posts`
5. `Linkedin-Version: 202607` was supported for this application
6. the single explicitly approved request returned HTTP 201 and `x-restli-id`
7. the successful local receipt matched the approved content hash

This validates the current app and API behavior, not a general documentary
guarantee that OIDC `sub` always equals a Posts API Person ID. BuildLog must
continue to identify the mapping source as inferred and must not guess another
identifier if a future request is rejected.

## Data That Must Never Be Logged or Committed

- client secret
- authorization code
- access token
- refresh token, if unexpectedly returned
- raw ID token
- full `Authorization` header
- OAuth state except in its restricted temporary state file and the one-time
  authorization URL shown only for manual browser opening
- credential-file payload
- full post content in events or receipts

Credentials are stored only in the user-level BuildLog credential directory.
Run artifacts, SQLite receipts, terminal diagnostics, exceptions, and tests
must contain only redacted or non-secret metadata.
