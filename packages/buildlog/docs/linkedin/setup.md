# LinkedIn Publishing Setup

This setup enables one local BuildLog user to publish one public, text-only
personal LinkedIn post after preview and explicit approval.

BuildLog treats the reviewed artifact as plain text. The adapter escapes
LinkedIn `little`-format control characters at the HTTP boundary so Markdown
punctuation remains visible text; mentions and structured hashtags are not
implemented in this baseline.

## 1. Developer App Products

In the LinkedIn Developer Portal, create an app or open the existing BuildLog
app, then enable:

1. **Share on LinkedIn**
2. **Sign In with LinkedIn using OpenID Connect**

The expected OAuth scopes are:

```text
openid profile w_member_social
```

Do not request email, Advertising API, Lead Sync API, Verified on LinkedIn,
Pages Data Portability, or organization-page permissions for this baseline.

In the app's **Auth** settings, copy the Client ID and generate or rotate the
Client Secret for local use. Keep both values out of screenshots, chat,
terminal transcripts, and tracked files.

## 2. Exact Redirect URI

In the app's **Auth** settings, add exactly:

```text
http://localhost:8765/auth/linkedin/callback
```

The scheme, host, port, path, and trailing-slash behavior must match. BuildLog
uses no trailing slash.

## 3. Local Credentials

Create `.env` in the repository root:

```env
LINKEDIN_CLIENT_ID=your_client_id
LINKEDIN_CLIENT_SECRET=your_new_client_secret
LINKEDIN_REDIRECT_URI=http://localhost:8765/auth/linkedin/callback
LINKEDIN_API_VERSION=202607
```

Restrict the file before login:

```bash
chmod 600 .env
```

BuildLog creates `~/.buildlog/credentials/` with mode `0700`. If that directory
already exists, keep it private:

```bash
chmod 700 ~/.buildlog/credentials
```

Never place real values in `.env.example`, source files, tests, screenshots,
issues, chat messages, Git commits, or GitHub repository settings. `.env` is
ignored by Git.

If a secret has ever been exposed, revoke it in LinkedIn Developer Portal and
use a newly generated value.

## 4. Install

```bash
cd BuildLog
.venv/bin/pip install -e '.[dev]'
```

The `.venv/bin/buildlog` console command is then available without activating
the virtual environment. The equivalent module form is
`.venv/bin/python -m buildlog.main`.

## 5. Safe Preflight

```bash
.venv/bin/buildlog linkedin status
```

This reports only:

- whether the local configuration is ready for login
- a safe configuration issue when it is not ready
- whether Client ID and Client Secret are configured
- configured redirect URI and API version
- local token-file path
- whether a token file exists
- whether the token is expired
- granted scopes when LinkedIn returned them

It never prints credential values.

## 6. Login

```bash
.venv/bin/buildlog linkedin login
```

BuildLog:

1. generates cryptographically secure OAuth state
2. stores only its hash temporarily
3. starts listening only on the configured localhost callback
4. opens the LinkedIn authorization URL
5. validates and consumes state once
6. exchanges the authorization code
7. stores the token under `~/.buildlog/credentials/linkedin.json`

The credential directory is permission-restricted and outside the repository.
If the browser does not open:

```bash
.venv/bin/buildlog linkedin login --no-browser
```

Open the printed URL yourself. Do not share it because it contains temporary
OAuth state.

## 7. Confirm Identity

```bash
.venv/bin/buildlog linkedin whoami
```

Expected safe output includes:

- display name
- redacted member identifier
- stable hashed account reference
- token expiration
- granted scopes when known

The controlled production smoke test confirmed that LinkedIn accepts this
app's OIDC-sub-derived Person URN. BuildLog still labels the mapping as
inferred because LinkedIn's official documents do not explicitly state that
OIDC `sub` is the Posts API Person ID. See the [research
note](../research/linkedin-publishing.md).

## 8. Preview an Existing Run

Use a completed run ID printed by the generation command:

```bash
.venv/bin/buildlog linkedin preview <run-id>
```

Preview displays:

- selected run and final artifact
- authenticated member
- exact full text
- text length and SHA-256 hash
- prior identical successful publication, if any
- prior matching indeterminate attempt, if any

Preview performs identity lookup but never sends a post request.

## 9. Publish Only After Review

```bash
.venv/bin/buildlog linkedin publish <run-id> --confirm
```

BuildLog shows the exact content again and asks:

```text
Type PUBLISH to submit this exact content to LinkedIn:
```

Only exact `PUBLISH` submits the post. A prior identical success is blocked.
A prior matching indeterminate attempt is also blocked because the post may
already exist. Use `--allow-duplicate` only after inspecting the prior receipt
and LinkedIn profile:

```bash
.venv/bin/buildlog linkedin publish <run-id> --confirm --allow-duplicate
```

## 10. Logout

```bash
.venv/bin/buildlog linkedin logout
```

This deletes the local token and pending state. It does not delete BuildLog
runs, receipts, or LinkedIn posts.
