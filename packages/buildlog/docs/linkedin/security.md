# LinkedIn Publishing Security

## Security Boundary

BuildLog separates four kinds of data:

1. generation artifacts under `runs/`
2. queryable metadata and publication receipts in SQLite
3. OAuth credentials under `~/.buildlog/credentials/`
4. temporary OAuth state in the same restricted credential directory

Credentials never belong to an engineering run or publishing receipt.

Before preview, BuildLog resolves the indexed final artifact to its real
filesystem path, requires it to remain under the configured `runs/` root, and
verifies the raw file against the SHA-256 stored at generation time. A moved,
replaced, or modified trace is rejected rather than silently published.

## OAuth Threat Model

### Cross-site request forgery

Each login generates cryptographically secure OAuth state. BuildLog stores only
the SHA-256 hash, compares it in constant time, enforces a short lifetime, and
deletes it after one callback. Missing, repeated, expired, or mismatched state
fails login.

### Redirect interception

The callback is a fixed localhost HTTP URI with an explicit port and path. The
same exact URI must appear in `.env`, the authorization request, token
exchange, and LinkedIn Developer Portal.

The local callback server has a timeout, accepts only the configured path, and
closes after the callback or timeout.

### Token disclosure

The token store:

- lives outside the repository
- creates its directory with mode `0700` where supported
- refuses credential directories that grant any group or world access
- refuses an immediate parent directory that grants group or world write access
- writes a same-directory temporary file with mode `0600`
- flushes and atomically replaces the final file
- sets the final file to mode `0600`
- refuses symbolic links and group- or world-readable token files
- persists only the access token, expiry, and scope metadata required by the
  implemented workflow; unused ID and refresh tokens are not retained
- never prints raw access, refresh, or ID tokens

When credentials are loaded from the repository-local `.env`, BuildLog also
requires a regular, non-symlink file with mode `0600` on POSIX systems.

LinkedIn self-service access tokens normally require reauthorization at
expiry. BuildLog does not invent programmatic refresh support.

### Identity confusion

BuildLog distinguishes:

- Developer App identity
- Company Page associated with the app
- authenticated member identity
- Person URN used as post author

OIDC identity comes from the authenticated `/v2/userinfo` response. BuildLog
does not decode and trust an unverified ID token. Organization publishing is
not implemented.

### Accidental publication

Generation and publication are separate commands. Preview cannot publish.
Publication requires:

1. an existing completed final artifact
2. authenticated member identity
3. duplicate check
4. `--confirm`
5. exact interactive input `PUBLISH`

There is no automatic approval, schedule, job, retry queue, or post-on-generate
behavior.

### Duplicate and timeout behavior

Successful receipts are queried by platform, hashed account reference, and
normalized content SHA-256. An identical success is blocked by default.

A local user must run only one `buildlog linkedin publish` command at a time.
This baseline does not claim concurrent or distributed exactly-once delivery;
two processes that pass the receipt check simultaneously could both submit.

A timeout, transport interruption, user interrupt during submission,
unexpected 2xx, HTTP 408, or 5xx response after submission is
`indeterminate`. BuildLog persists that state and does not retry because the
post may already exist. A later preview shows the unresolved receipt, and
publication of the same platform/account/content tuple is blocked by default.
Use `--allow-duplicate` only after checking LinkedIn and the receipt.

A hard process termination can occur after `publish_started` is flushed but
before a receipt is saved. A started attempt without a terminal publication
event or receipt must be treated manually as indeterminate; BuildLog does not
claim exactly-once delivery.

## Redaction Policy

The following must never appear in terminal errors, run events, receipts,
tests, source control, or documentation examples:

- Client Secret
- access token
- refresh token
- authorization code
- raw ID token
- full Authorization header
- OAuth state except inside the temporary authorization URL

Publishing events contain content hash and length, not the full post.
Publication receipts contain operational metadata, not post content.

## Human Content Review

OAuth safety does not make content safe to publish. Before approval, review the
exact preview for:

- secrets and API keys
- employer or client confidential information
- customer data
- private repository details
- unpublished product or business information
- unsupported technical or business claims
- personal information not intended for a public post

The fixed warning in `06_final.md` remains part of the run artifact. The
publication resolver strips only that exact warning from the post body.

## Incident Response

If a Client Secret or token is exposed:

1. revoke or rotate it in LinkedIn Developer Portal
2. run `.venv/bin/buildlog linkedin logout`
3. remove the exposed material from any untracked local files or screenshots
4. inspect Git status and history before pushing
5. authorize again only with the replacement secret

If an unexpected post appears, delete it manually through LinkedIn. API
deletion is intentionally not implemented in this baseline.
