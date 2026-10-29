# Setup Guide

How to set up MyWhoosh2Garmin from scratch, as it is configured in this fork.

## How it works

```
MyWhoosh ──▶ Strava ──webhook──▶ Cloudflare Worker ──▶ GitHub Actions (run.yml) ──▶ Garmin Connect
```

1. MyWhoosh uploads the ride to Strava.
2. Strava notifies the Cloudflare Worker (`cloudflare/strava-webhook-worker.js`).
3. The Worker starts the **Run MyWhoosh2Garmin** workflow (`.github/workflows/run.yml`), which waits 30 s for Strava to finish processing.
4. `myWhoosh2Garmin.py` downloads new MyWhoosh rides from Strava (Virtual Rides whose device is MyWhoosh, or with "MyWhoosh" in the name), builds a FIT file, uploads it to Garmin, then sets:
   - **Title:** the exact Strava name, e.g. `MyWhoosh - Train with Tadej: Four by Four`
   - **Notes:** `Metrics: NP: 162W | IF: 0.81 | TSS: 46.5 | Work: 367kJ`
5. If Garmin refreshed its login token during the run, the last workflow step saves the new token back to the `GARMIN_TOKENS` secret.

NP and Work come from Strava's own figures. IF and TSS use the FTP on your Strava profile (falling back to the `ATHLETE_FTP` variable).

## What you need

- GitHub account (this repo, forked from `MarcChen/MyWhoosh2Garmin`)
- Strava account, with MyWhoosh set to upload to Strava
- Garmin Connect account
- Cloudflare account (free plan) — only for instant sync
- A Mac with [`uv`](https://docs.astral.sh/uv/) and the GitHub CLI: `brew install uv gh`, then `gh auth login`

## Step 1: Get the code

```bash
cd ~
gh repo clone finch6/MyWhoosh2Garmin
cd MyWhoosh2Garmin
uv sync
```

## Step 2: Strava API application

1. Go to <https://www.strava.com/settings/api>.
2. If there's no application yet, create one:
   - **Category:** Training (it's only a label)
   - **Authorization Callback Domain:** `localhost`
3. Note the **Client ID** (a short number) and **Client Secret**.

Strava allows one API application per account, and one webhook per application (see step 9).

## Step 3: Set your FTP on Strava

Strava → **Settings → My Performance** (<https://www.strava.com/settings/performance>) → enter your FTP → **Save**.

The script reads it on every run, so when your FTP changes, update it here and nothing else.

## Step 4: Strava tokens

```bash
uv run python setup_strava_auth.py
```

1. Enter the Client ID and Client Secret.
2. Open the printed **Authorize here** link. Strava's approval page must mention your **profile** as well as your activities — that permission (`profile:read_all`) is what lets the script read your FTP. Leave every box ticked.
3. The browser lands on a broken `localhost` page — that's expected. Its address should contain `scope=read,activity:read_all,profile:read_all`. Copy the whole address and paste it back into the terminal.
4. The script prints the six `STRAVA_…` values for step 7.

It also writes `.env` and `strava_tokens.json` (both gitignored). They contain your credentials, so delete them once the secrets are saved:

```bash
rm .env strava_tokens.json
```

<details>
<summary>Manual alternative (if the script misbehaves)</summary>

```bash
CID=your_client_id
SECRET=your_client_secret
```

Open in a browser (replace `YOUR_CLIENT_ID`):

```
https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost/exchange_token&approval_prompt=force&scope=activity:read_all,profile:read_all
```

Copy the `code=` value from the `localhost` address and **straight away** (it expires in minutes and works once):

```bash
CODE=paste_code_here
curl -s -X POST https://www.strava.com/oauth/token \
  -d client_id=$CID -d client_secret=$SECRET \
  -d code=$CODE -d grant_type=authorization_code > ~/strava_new.json
jq '{access_token, refresh_token, expires_at, expires_in}' ~/strava_new.json
```

Check it can see your FTP (should print your FTP, not `null`):

```bash
curl -s -H "Authorization: Bearer $(jq -r .access_token ~/strava_new.json)" \
  https://www.strava.com/api/v3/athlete | jq '{ftp, weight, measurement_preference}'
```

Use the four values for the secrets in step 7, then `rm ~/strava_new.json`.
</details>

## Step 5: Garmin tokens

```bash
uv run python setup_garmin_auth.py
```

Enter your Garmin email, password and MFA code (if asked). It prints one line of JSON:

```
{"di_token": "eyJ…", "di_refresh_token": "eyJ…", "di_client_id": "GARMIN_CONNECT_MOBILE_…"}
```

That **whole line**, braces included, is the `GARMIN_TOKENS` secret. A copy is also saved in `.garminconnect/` (gitignored); delete it once the secret is saved: `rm -rf .garminconnect`.

The login uses the [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library (`garth` is deprecated and no longer works).

## Step 6: GitHub token

One fine-grained token is used by the Cloudflare Worker (to start the workflow) and by the workflow itself (to save refreshed Garmin tokens).

GitHub → avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**:

- **Name:** `strava-webhook`
- **Expiration:** up to a year. **Put the expiry date in your diary** — when it expires, rides stop syncing without any warning.
- **Repository access:** Only select repositories → `finch6/MyWhoosh2Garmin`
- **Permissions:**
  - **Actions:** Read and write
  - **Secrets:** Read and write
  - (Metadata: Read-only is added automatically)

Copy the `github_pat_…` value — GitHub shows it only once.

## Step 7: GitHub secrets and variables

Repo → **Settings → Secrets and variables → Actions**.

**Secrets** tab:

| Secret | Value |
|---|---|
| `STRAVA_CLIENT_ID` | Strava Client ID |
| `STRAVA_CLIENT_SECRET` | Strava Client Secret |
| `STRAVA_ACCESS_TOKEN` | from step 4 |
| `STRAVA_REFRESH_TOKEN` | from step 4 |
| `STRAVA_EXPIRES_AT` | from step 4 |
| `STRAVA_EXPIRES_IN` | from step 4 |
| `GARMIN_TOKENS` | the JSON line from step 5 |
| `SECRETS_WRITE_TOKEN` | the `github_pat_…` token from step 6 |

**Variables** tab (optional):

| Variable | Value |
|---|---|
| `ATHLETE_FTP` | your FTP, e.g. `200` — only used if Strava returns no FTP |

## Step 8: Test a manual run

Repo → **Actions → Run MyWhoosh2Garmin → Run workflow** (manual runs start immediately).

In the **Run myWhoosh2Garmin.py** step, a working run shows:

```
Found 1 new virtual cycling activities to upload to Garmin.
Using FTP from Strava profile: 200W
Renamed Garmin activity 24884221489 to 'MyWhoosh - …'
```

To have something to upload, delete a recent MyWhoosh ride from Garmin Connect first. The script checks your last 10 Strava activities and skips any already on Garmin.

**Save refreshed Garmin tokens** is normally *skipped*. When it runs, it should end with `GARMIN_TOKENS secret updated.`

## Step 9: Instant sync (Cloudflare Worker + Strava webhook)

### 9a. Webhook secret

```bash
openssl rand -hex 24
```

Keep this 48-character string; it goes in three places that must match exactly (Worker variable, end of the webhook URL, Strava `verify_token`).

### 9b. Create the Worker

1. <https://dash.cloudflare.com> → **Workers & Pages → Create → Start with Hello World** → name it `strava-webhook` → **Deploy**.
2. **Edit code** → replace everything with `cloudflare/strava-webhook-worker.js` from this repo → **Deploy**.
3. **Settings → Variables and Secrets** → add, then **Deploy**:

| Name | Type | Value |
|---|---|---|
| `WEBHOOK_SECRET` | Secret | string from 9a |
| `GITHUB_TOKEN` | Secret | `github_pat_…` from step 6 |
| `GITHUB_REPO` | Text | `finch6/MyWhoosh2Garmin` |
| `WORKFLOW_FILE` | Text | `run.yml` |
| `STRAVA_ATHLETE_ID` | Text | number from your Strava profile URL (`strava.com/athletes/12345678`) |

Optional: `DELAY_SECONDS` (default `30`), `GITHUB_BRANCH` (default `main`).

A message saying *"Update your Wrangler configuration…"* can be ignored — it's for people deploying from the command line.

The webhook URL is the Worker address + `/` + the secret, e.g.
`https://strava-webhook.duncans-account.workers.dev/<WEBHOOK_SECRET>`

### 9c. Test the Worker

```bash
WSECRET=your_webhook_secret
WURL="https://strava-webhook.duncans-account.workers.dev/$WSECRET"

# Strava's handshake — expect HTTP 200 and {"hub.challenge":"test123"}
curl -i "$WURL?hub.mode=subscribe&hub.verify_token=$WSECRET&hub.challenge=test123"

# A pretend new ride — expect "OK" and a new run in the Actions tab
curl -X POST "$WURL" -H "Content-Type: application/json" \
  -d '{"object_type":"activity","aspect_type":"create","object_id":1,"owner_id":YOUR_ATHLETE_ID}'
```

The pretend ride's run waits 30 s and finds nothing to upload — that's correct.

### 9d. Register the webhook with Strava

Set your Strava app details (if not already set from step 4), then check for an existing subscription (only one is allowed per Strava app):

```bash
CID=your_client_id
SECRET=your_client_secret
```


```bash
curl -s "https://www.strava.com/api/v3/push_subscriptions?client_id=$CID&client_secret=$SECRET" | jq
```

If one exists and its `callback_url` isn't your Worker, delete it (use its `id`):

```bash
curl -X DELETE "https://www.strava.com/api/v3/push_subscriptions/THE_ID?client_id=$CID&client_secret=$SECRET"
```

Then register the Worker (expect `{"id":…}`):

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=$CID -F client_secret=$SECRET \
  -F callback_url="$WURL" -F verify_token="$WSECRET"
```

Every new Strava activity now triggers a run. Non-MyWhoosh activities (outdoor rides, walks) also trigger one; it just finds nothing to do.

## Troubleshooting

Check in this order: **Cloudflare Worker → Logs** (did it fire?), then **GitHub → Actions** (did it run, what did it say?).

| Symptom | Cause / fix |
|---|---|
| Rides stop syncing, Worker log shows `GitHub dispatch failed: 401` | GitHub token expired. Regenerate it (step 6), update **both** `GITHUB_TOKEN` in Cloudflare and `SECRETS_WRITE_TOKEN` on GitHub. |
| Worker log: `GitHub dispatch failed: 404` | `GITHUB_REPO` / `WORKFLOW_FILE` wrong, or the token doesn't cover this repo. |
| Worker log: `GitHub dispatch failed: 422` | `run.yml` is missing the `delay_seconds` input. |
| Worker log: `Ignored event … owner_id=… (expected …)` | `STRAVA_ATHLETE_ID` doesn't match your Strava ID. |
| Strava: `GET to callback URL does not return 200` | Webhook secret mismatch — run the handshake test in 9c. |
| Strava: `subscription already exists` | Another webhook is registered — see 9d. |
| `No FTP returned by Strava…` then `Using FTP from ATHLETE_FTP variable` | Strava token lacks `profile:read_all`, or no FTP saved on Strava. Check step 3, then redo step 4 and update the `STRAVA_…` secrets. |
| `Garmin is rate-limiting logins` | Too many attempts. Wait a few hours (ideally until the next day) before retrying. |
| `Garmin authentication failed…` | Redo step 5 and update `GARMIN_TOKENS`. |
| **Save refreshed Garmin tokens** step fails | `SECRETS_WRITE_TOKEN` expired or lacks *Secrets: Read and write*. Fix it, then redo step 5 (the refreshed token wasn't saved). |
| `Found 0 new virtual cycling activities` but a ride is missing | The ride must be a Strava *Virtual Ride* recorded by MyWhoosh (device shown on the activity), and among your last 10 Strava activities. |

## Security

- Never commit `.env`, `strava_tokens.json`, `cookie.json`, `strava.db` or `.garminconnect/` (all gitignored).
- `GARMIN_TOKENS` and the Strava tokens give access to your accounts — keep them in GitHub Secrets only.
- Forks of public repos are public, so this repo's workflow logs are public too. The scripts never print tokens; keep it that way when changing them.
