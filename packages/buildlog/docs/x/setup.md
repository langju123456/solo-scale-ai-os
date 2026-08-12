# X Publishing Setup

BuildLog's X baseline publishes one standard text post from an existing,
reviewed final artifact. It does not generate X copy, upload media, create
threads, schedule posts, or retry a publication automatically.

## Developer App

In the X Developer Console:

1. Create or select a Project and App with OAuth 2.0 enabled.
2. Configure the App as a public client suitable for a local application.
3. Register this exact callback URL:

   ```text
   http://127.0.0.1:8766/auth/x/callback
   ```

4. Enable these scopes:

   ```text
   tweet.read tweet.write users.read
   ```

5. Ensure the developer account has API credits and a spending limit you
   accept before the first real publication.

X currently documents pay-per-use API access. Review the current
[X pricing](https://docs.x.com/x-api/getting-started/pricing) before a live
test.

## Local Configuration

Add only the OAuth 2.0 Client ID to the ignored local `.env`:

```dotenv
X_CLIENT_ID=your_client_id
X_REDIRECT_URI=http://127.0.0.1:8766/auth/x/callback
```

The baseline uses Authorization Code with PKCE and does not require a client
secret. Keep `.env` private:

```bash
chmod 600 .env
git check-ignore -v .env
```

## Authentication

Run:

```bash
.venv/bin/buildlog x status
.venv/bin/buildlog x login
.venv/bin/buildlog x whoami
```

The token is stored under `~/.buildlog/credentials/x.json` with private file
permissions. This first baseline does not refresh expired tokens; run login
again when the access token expires.

## Safe Publication Flow

The selected run must already contain a reviewed final artifact that fits the
280 weighted-character baseline:

```bash
.venv/bin/buildlog x preview <run-id>
.venv/bin/buildlog x publish <run-id> --confirm
```

The publish command previews the exact text again and requires the interactive
confirmation `PUBLISH`. After approval, BuildLog makes exactly one request to
`POST https://api.x.com/2/tweets`.

If the request ends without a reliable response, BuildLog records an
indeterminate receipt and blocks an identical retry until X and the local
receipt have been inspected.

## Manual Validation Boundary

Implementation and mocked validation can complete without credentials.
Manual OAuth, `whoami`, preview, and a controlled real post require the account
owner. The first controlled smoke test completed successfully on 2026-07-30:
OAuth 2.0 PKCE resolved the expected account through `GET /2/users/me`, and
one explicitly approved text post returned HTTP 201 with a persisted local
receipt.

This validates exactly one client-side POST attempt with no automatic retry.
It does not claim exactly-once delivery by X. Do not publish until the exact
payload and account shown by preview have been reviewed.
