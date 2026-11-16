# The phone app: setup and day to day

**Written:** 2026-11-16. **Who:** Adam, with Claude checking each step. **Design:** [ADR 0029](../decisions/0029-the-phone-apps-api.md) (the API); the app itself and its push come in M3 Phases 3 and 4 ([m3-plan.md](../m3-plan.md)) and add sections here.

All commands run in your own Terminal app, from `~/projects/fantasy-helper`. Values come from Bitwarden and go in at hidden prompts; none is ever typed into the chat.

## Part 1: the API (Phase 2). About 15 minutes of your time

The API is a Cloud Run service that serves your saved reports to your signed-in token and to nobody else. Do these in order. The PR with the code stays unmerged until step 7, because the merge deploys.

### 1. Tell Claude prod's Supabase project URL
Prod's project (`fantasy-helper-prod`): Project Settings → API → **Project URL**, `https://<ref>.supabase.co`. It isn't secret. Claude checks `https://<project>.supabase.co/auth/v1/.well-known/jwks.json`:
- **`keys` has an entry:** the project already signs tokens with a public key. Go on.
- **`keys` is empty:** the project still signs with the old shared secret, which the API refuses. In the dashboard, Project Settings → **JWT Keys**, create an **ES256** signing key and rotate to it. Prod has no sessions yet, so nobody is signed out. Tell Claude when it's done and it checks again.

### 2. Show the sign-in code in the email
Authentication → **Emails** (Templates) → **Magic Link**: make the body show `{{ .Token }}` (the 6-digit code) and remove the link. A tapped link opens Safari, not the app on your Home Screen. Leave the code's lifetime at an hour or less.

### 3. Create your own sign-in
Authentication → **Users** → **Add user**: the email your Supabase account uses (the built-in email sender delivers only to people in your Supabase organization, and only 2 an hour, which is enough for you alone), **Auto Confirm User** on, and a long random password you never use (generate it, don't save it).

### 4. Confirm sign-ups are off
Authentication → **Sign In / Providers**: *Allow new users to sign up* is **off**. The smoke script (step 8) proves it; this is where to turn it off if it says otherwise.

### 5. Create the cloud side, from the PR's branch
It only adds: the `fh-api` account, three empty secrets, a placeholder service and the permissions around them.

```bash
git fetch && git checkout m3-part3-api
```

```bash
scripts/gcp-setup.sh infra
```

If it stops at `allUsers`, an organization policy is blocking public access: tell Claude (ADR 0029 has the fallback). Then `git checkout main` again.

### 6. Fill the three secrets
At the hidden prompt, each:

```bash
scripts/gcp-secrets.sh fh-api-database-url fh-supabase-url fh-app-origins
```

- `fh-api-database-url`: prod's **transaction pooler** string, **port 6543** (Supabase → Connect → Transaction pooler). It's a different string from the jobs'; the script refuses the session pooler's port 5432.
- `fh-supabase-url`: the project URL from step 1.
- `fh-app-origins`: type `https://not-set.invalid` for now. Phase 3 replaces it with the app's address.

Check (versions only, never values): `scripts/gcp-setup.sh show`.

### 7. Merge the PR
Merge it in GitHub. The deploy builds the image, migrates the database (migration 0007), and then moves the API last. Watch it:

```bash
gh run watch
```

Then find the address:

```bash
gcloud run services describe api --region us-east4 --format 'value(status.url)'
```

### 8. Check it
```bash
scripts/api-smoke.sh https://<the address from step 7>
```

It asks for your email, Supabase's publishable key (`sb_publishable_...`, Project Settings → API Keys) and the 6-digit code Supabase emails you. It prints only statuses and counts. **Right looks like:** 200s with your token and your real report counts, 401 without a token, and "sign-ups refused" for an email that isn't yours. Anything else: send Claude the output (it has no secrets in it).

## If something is wrong

| What you see | Meaning and fix |
|---|---|
| The deploy fails on `api` with `PERMISSION_DENIED` | The deployer needs a narrower permission on the service. Tell Claude; don't grant anything project-wide. |
| The deploy says the service doesn't exist | Step 5 hasn't run (the deployer can update a service, never create one). Run it, then **Actions → Deploy → Run workflow**. |
| A secret has no value, and the deploy fails mounting it | Step 6. `scripts/gcp-setup.sh show` lists versions. |
| `/v1/me` answers 401 with your token | The API couldn't verify it: the project may still sign with the shared secret (step 1), or the URL in `fh-supabase-url` isn't this project's. |
| `/v1/me` answers 403 | The token is valid but its email matches no user of ours. The email in step 3 must be the one on your user row. |
| `/v1/me` answers 503 "sign-in isn't configured" | `fh-supabase-url` is empty. |
| The smoke script says an unknown email got a 200 | Sign-ups are on (step 4). |

## Rotating or changing a value
`scripts/gcp-secrets.sh <name>` saves a new version and turns the older ones off. The service reads a secret when it starts, so run **Actions → Deploy → Run workflow** afterwards.
