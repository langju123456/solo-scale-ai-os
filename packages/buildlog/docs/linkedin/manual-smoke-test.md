# LinkedIn Manual Production Smoke Test

This is the only part of the Publishing Baseline that must contact LinkedIn.
Run it manually after reviewing the implementation, tests, and security model.

## Baseline Validation Result

The first controlled smoke test completed successfully on 2026-07-29:

- OAuth returned `openid`, `profile`, and `w_member_social`
- `whoami` resolved the intended personal account
- preview produced no network publication
- duplicate and indeterminate checks were clear
- one explicitly approved POST returned HTTP 201
- no automatic retry occurred
- the external post identifier and successful local receipt were persisted

The test used the dedicated ID `BL-LI-SMOKE-001`. Real account identifiers,
credentials, authorization data, Post IDs, and receipt IDs are intentionally
excluded from this document.

## Stop Conditions

Do not continue if:

- the Developer App does not show Share on LinkedIn and OIDC access
- the exact redirect URI is missing
- the local Client Secret is not a newly valid secret
- `.env` appears in `git status`
- the selected post contains private or unsupported information
- another identical post may already exist
- another `buildlog linkedin publish` command is running

## Preflight

```bash
cd BuildLog
git status --short
git check-ignore -v .env
.venv/bin/python -m pytest -q
.venv/bin/buildlog linkedin status
```

Expected:

- `.env` is ignored
- tests pass without network access
- Ready for login reports `yes`
- Client ID and Client Secret report `configured: yes`
- redirect URI is
  `http://localhost:8765/auth/linkedin/callback`
- API version is `202607`

## OAuth Login

```bash
.venv/bin/buildlog linkedin login
```

Expected browser flow:

1. LinkedIn displays the BuildLog app consent screen.
2. Requested permissions correspond to profile identity and member posting.
3. You approve manually.
4. The browser returns to the localhost callback.
5. It displays:

   ```text
   LinkedIn authorization response received. Return to the terminal to confirm completion.
   ```

Expected terminal output:

- authorization stored locally
- token expiration timestamp
- scopes, when returned
- no raw token, code, secret, or Authorization header

The browser message confirms only that the callback was received. The terminal
message confirms state validation, token exchange, and local token storage.

If the callback reports a redirect mismatch, make the Developer Portal and
`.env` values exactly equal and run login again.

## Identity Check

```bash
.venv/bin/buildlog linkedin status
.venv/bin/buildlog linkedin whoami
```

Confirm:

- the display name is your intended personal LinkedIn account
- the identifier is redacted
- the account reference is present
- token is not expired
- scopes include `openid`, `profile`, and `w_member_social` when LinkedIn
  returned scope data

This validates the OIDC userinfo response. The first post also validates the
documented OIDC subject-to-Person-URN mapping.

## Preview

Choose a completed, non-sensitive BuildLog run:

```bash
.venv/bin/buildlog linkedin preview <run-id>
```

Read the complete content. Confirm:

- it is the intended `06_final.md`
- the fixed human-review warning is not in the post body
- facts are supported by the engineering evidence
- no secrets or private data are present
- `Network publication from preview: no`
- duplicate status is `no`

Record the displayed content SHA-256 for comparison with the receipt.

## First Post

Use a low-risk, already reviewed technical post. Submit only when ready:

```bash
.venv/bin/buildlog linkedin publish <run-id> --confirm
```

The command shows the exact content again. Type:

```text
PUBLISH
```

Any other input cancels without submission.

Expected success:

- LinkedIn returns HTTP 201
- terminal shows the external post URN
- terminal shows publication time and receipt ID
- the post appears once on the intended personal profile

Do not use `--allow-duplicate` for the first smoke test.

## Inspect the Receipt

```bash
sqlite3 buildlog.db \
  "SELECT id, run_id, platform, account_reference, content_hash, status, external_post_id, published_at, api_version, http_status, error_category FROM publish_receipts ORDER BY created_at DESC LIMIT 5;"
```

The successful row should have:

- matching run and content hash
- `platform=linkedin`
- `status=succeeded`
- HTTP status `201`
- external post URN
- no token, secret, code, header, or post body

Inspect the append-only run event:

```bash
tail -n 10 "runs/<run-id>/events.jsonl"
```

Expect `publish_previewed`, `publish_approved`, `publish_started`, and
`publish_succeeded` with safe hashes and correlation IDs.

## Failure Guidance

### HTTP 401

The token is invalid, expired, or revoked.

```bash
.venv/bin/buildlog linkedin logout
.venv/bin/buildlog linkedin login
```

Then run `whoami` and preview again.

### HTTP 403

Confirm:

- Share on LinkedIn is enabled
- `w_member_social` was granted
- OIDC is enabled
- `whoami` resolves the intended account
- the author mapping is accepted for this app

Do not guess or hardcode another member URN.

### HTTP 429

Stop. Review Developer Portal Analytics and wait for the documented quota
window. Do not loop or automate retries.

### Indeterminate result

Timeouts, transport interruptions, a user interrupt during submission,
unexpected 2xx responses, HTTP 408, HTTP 5xx, and a 201 response without a
valid post ID are `indeterminate`.

1. Do not retry.
2. Inspect the latest receipt.
3. Inspect your LinkedIn profile for the exact content.
4. Compare the content hash.
5. Only after deciding whether the post exists should you make another
   manually approved attempt.

A matching indeterminate receipt is shown by preview and blocks publication
by default. `--allow-duplicate` is the explicit acknowledgement after this
manual inspection.

If a process crash or power loss leaves `publish_started` in `events.jsonl`
without a matching terminal publication event or receipt, treat the attempt as
indeterminate even though automatic duplicate detection cannot reconstruct the
missing outcome. Inspect LinkedIn before any new attempt.

### Duplicate blocked

Inspect the prior receipt and LinkedIn profile. Normally no action is needed.
Use `--allow-duplicate` only when publishing the same content twice is truly
intentional.

## Rollback

If the test post is incorrect, delete it manually through the LinkedIn UI.
BuildLog does not implement deletion. Keep the local receipt as an accurate
record of what happened, then correct the source artifact in a new reviewed
workflow rather than mutating the old receipt.
