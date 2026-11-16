# The phone app: setup and day to day

**Written:** 2026-11-16. **Who:** Adam, with Claude checking each step. **Design:** [ADR 0029](../decisions/0029-the-phone-apps-api.md) (the API); the app itself and its push come in M3 Phases 3 and 4 ([m3-plan.md](../m3-plan.md)) and add sections here.

All commands run in your own Terminal app, from `~/projects/fantasy-helper`. Values come from Bitwarden and go in at hidden prompts; none is ever typed into the chat.

## Part 1: the API (Phase 2). About 10 minutes of your time

The API is a Cloud Run service that serves your saved reports to your signed-in token and to nobody else. Do these in order. The PR with the code stays unmerged until step 5, because the merge deploys.

**You sign in with your email and a password.** Supabase sends no email for that, so there is no email service to set up. (The 6-digit emailed code is for friends at the beta: see "Later" at the bottom.)

### 1. Prod's Supabase project URL (done 2026-11-16)
Claude checked its public keys (`<project URL>/auth/v1/.well-known/jwks.json`): prod publishes an `ES256` key, which the API accepts. If the smoke check later says a token's algorithm isn't accepted, the project is still signing with the old shared secret: Project Settings → **JWT Keys**, create an **ES256** signing key and rotate to it (prod has no sessions, so nobody is signed out).

### 2. Your sign-in, with a password
Authentication → **Users** → **Add user**: the email your Supabase account uses, a long random password (save it in Bitwarden; the app's sign-in screen asks for it, and the iPhone's keychain can remember it), and **Auto Confirm User** on.

### 3. Sign-ups off
Authentication → **Sign In / Providers**: *Allow new users to sign up* is **off**. The smoke script (step 7) proves it.

### 4. Create the cloud side, from the PR's branch
It only adds: the `fh-api` account, three empty secrets, a placeholder service and the permissions around them.

```bash
git fetch && git checkout --detach origin/m3-part3-api
```

```bash
scripts/gcp-setup.sh infra
```

If it stops at `allUsers`, an organization policy is blocking public access: tell Claude (ADR 0029 has the fallback). Then go back:

```bash
git checkout main
```

### 5. Fill the three secrets
At the hidden prompt, each:

```bash
scripts/gcp-secrets.sh fh-api-database-url fh-supabase-url fh-app-origins
```

- `fh-api-database-url`: prod's **transaction pooler** string, **port 6543** (Supabase → Connect → Transaction pooler). It's a different string from the jobs'; the script refuses the session pooler's port 5432.
- `fh-supabase-url`: the project URL from step 1 (`https://<ref>.supabase.co`).
- `fh-app-origins`: type `https://not-set.invalid` for now. Phase 3 replaces it with the app's address.

Check (versions only, never values): `scripts/gcp-setup.sh show`.

### 6. Merge the PR
Merge it in GitHub (Claude can do it when you say so). The deploy builds the image, migrates the database (migration 0007), and then moves the API last. Watch it:

```bash
gh run watch
```

Then find the address:

```bash
gcloud run services describe api --region us-east4 --format 'value(status.url)'
```

### 7. Check it
```bash
scripts/api-smoke.sh https://<the address from step 6>
```

It asks for your email, Supabase's publishable key (`sb_publishable_...`, Project Settings → API Keys) and your Supabase password. It prints only statuses and counts. **Right looks like:** 200s with your token and your real report counts, 401 without a token, and "sign-ups refused" for an email that isn't yours. Anything else: send Claude the output (it has no secrets in it).

## If something is wrong

| What you see | Meaning and fix |
|---|---|
| The deploy fails on `api` with `PERMISSION_DENIED` | The deployer needs a narrower permission on the service. Tell Claude; don't grant anything project-wide. |
| The deploy says the service doesn't exist | Step 4 hasn't run (the deployer can update a service, never create one). Run it, then **Actions → Deploy → Run workflow**. |
| A secret has no value, and the deploy fails mounting it | Step 5. `scripts/gcp-setup.sh show` lists versions. |
| The smoke script says the password isn't accepted | The password in step 2, or the email: Authentication → Users shows the user and whether it's confirmed. |
| `/v1/me` answers 401 with your token | The API couldn't verify it: the project may still sign with the shared secret (step 1), or `fh-supabase-url` isn't this project's. |
| `/v1/me` answers 403 | The token is valid but its email matches no user of ours. The email in step 2 must be the one on your user row. |
| `/v1/me` answers 503 "sign-in isn't configured" | `fh-supabase-url` is empty. |
| The smoke script says an unknown email got a 200 | Sign-ups are on (step 3). |

## Rotating or changing a value
`scripts/gcp-secrets.sh <name>` saves a new version and turns the older ones off. The service reads a secret when it starts, so run **Actions → Deploy → Run workflow** afterwards.

## Later: the emailed code, for friends at the beta
Friends can't be given a password you made for them, so the beta signs people in with a 6-digit code emailed to them (a tapped link would open Safari, not the app on the Home Screen). That needs, in Supabase: a **custom email sender** (Authentication → SMTP Settings; Supabase's built-in one reaches only your own organization's members, 2 an hour, and no longer lets you edit the templates), and the **Magic Link** template showing `{{ .Token }}`. A sender on a domain you own is the right one for friends (Resend's free test address, `onboarding@resend.dev`, delivers only to its own account's email). This is part of the beta's setup, with the domain decision in [m3-plan.md](../m3-plan.md), and none of it is needed for you alone. `scripts/api-smoke.sh` already supports it: press Enter at the password prompt.
