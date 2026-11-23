# The phone app: setup and day to day

**Written:** 2026-11-16. **Who:** Adam, with Claude checking each step. **Design:** [ADR 0029](../decisions/0029-the-phone-apps-api.md) (the API) and [ADR 0009](../decisions/0009-pwa-vite-react.md) (the app); its push comes in M3 Phase 4 ([m3-plan.md](../m3-plan.md)) and adds a section here.

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
- `fh-app-origins`: type `https://not-set.invalid` for now. Part 2 (below) replaces it with the app's address.

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

## If something is wrong with the API (Part 1)

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

## Part 2: the app (Phase 3). About 20 minutes of your time

The app is a static site. Cloudflare builds it from the `web/` folder of `main` and serves it from a Worker, for free. It holds no secrets and no data of its own: it asks the API (Part 1) for your saved reports, with your sign-in. Do these in order. Nothing here touches Google Cloud until step 4.

**The app's address is `workers.dev` for now** (decided 2026-11-16; no domain until the beta). An installed web app belongs to its address, so moving to a real domain later means reinstalling it. That is fine for one person.

### 1. Gather three values (not secret, but kept out of git)
The app is built with three values that say where the API and Supabase are. None is a password, and the app hands them to every browser that opens it, but they are yours, so they live in Cloudflare and not in this repo.

The API's address (from Part 1, step 6):

```bash
gcloud run services describe api --region us-east4 --format 'value(status.url)'
```

- `VITE_API_URL`: that address, like `https://api-xxxxx-uk.a.run.app`.
- `VITE_SUPABASE_URL`: the project URL, `https://<ref>.supabase.co` (the same value as the `fh-supabase-url` secret).
- `VITE_SUPABASE_PUBLISHABLE_KEY`: Supabase → Project Settings → **API Keys** → the **publishable** key, `sb_publishable_...` (you used it for the smoke check). **Never** the secret key (`sb_secret_...`) or a `service_role` key: those must never reach a browser. (The app refuses to start, with a screen saying so, if it is given one.)

Keep the three together in Bitwarden. You'll paste them into Cloudflare in step 3.

### 2. Merge the PR
Cloudflare builds from `main`, so the PR merges first. Merging deploys nothing in Google Cloud (it touches no file the Deploy workflow watches), and nothing in Cloudflare until step 3 connects it. Claude can merge it when you say so.

### 3. Connect Cloudflare
In the Cloudflare dashboard (the account that holds your R2 bucket): **Workers & Pages** → **Create application** → **Import a repository** (Cloudflare moves these labels around; what you want is "a Worker built from a GitHub repository").

1. Sign in to GitHub when it asks. Install Cloudflare's GitHub app on **Only select repositories**, and pick `fantasy-helper` and nothing else.
2. Choose the `fantasy-helper` repository.
3. **Project name:** `fantasy-helper`. It must match `name` in `web/wrangler.jsonc`; a different name fails the build with a message that says so.
4. **Production branch:** `main`.
5. **Root directory** (under Advanced settings): `web`.
6. **Build command:** `npm ci && npm run build`.
7. **Deploy command:** `npx wrangler deploy`.
8. **Build variables:** add the three from step 1, spelled exactly `VITE_API_URL`, `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. They are *build* variables (Settings → Build → Variables and secrets), not the Worker's runtime ones: the app reads them when it's built.
9. **Save and Deploy.** The first build takes about a minute.

Cloudflare's default npm is 10, which can't install this app's test tools. `web/package.json` pins the npm that can (`packageManager`), and the build reads it. If the log says it used another npm, tell Claude.

Optional, under Settings → Build: **Build watch paths** `web/*`, so a push that doesn't touch the app doesn't rebuild it. Cloudflare also builds other branches by default (a preview each). That is **not** harmless: a branch's build runs its dependencies' install scripts, and Dependabot opens such branches before anyone has reviewed them (the hold only stops the merge, not the build). Cloudflare's **Branch control** screen (checked 2026-11-22) only offers a switch for the production branch, none for other branches, so Dependabot branches do build. That is accepted for now: the build holds no secret of ours (the three variables are public by nature), and a dependency update still can't merge without a person.

When the build finishes, the Worker's page shows its address, `https://fantasy-helper.<your-subdomain>.workers.dev`. Open it. **Right looks like:** a sign-in screen. Signing in will say it can't reach the server until step 4.

### 4. Tell the API the app's address
The API answers only the web addresses in its `fh-app-origins` secret. Put the app's address in, with `https://` and nothing after `.workers.dev` (no path, no trailing `/`):

```bash
scripts/gcp-secrets.sh fh-app-origins
```

The service reads a secret when it starts, so then re-run the deploy: GitHub → **Actions** → **Deploy** → **Run workflow**, branch `main`. Wait for it to go green (`gh run watch` in a Terminal does the same).

### 5. Put it on your iPhone
1. Open the app's address in **Safari**. It has to be Safari: other iPhone browsers can't add an app to the Home Screen.
2. Tap **Share** (the square with an arrow), scroll down, **Add to Home Screen**, **Add**.
3. Close Safari and open **Fantasy** from the Home Screen icon.
4. Sign in there with your email and your password (Part 1, step 2; the iPhone's keychain can remember it). **Sign in inside the Home Screen app, not in Safari:** the installed app has its own storage and doesn't see a Safari sign-in.

**Right looks like:** two tabs, **Today** and **This Week**, a section per league, each card with a title, a line of detail and the reason underneath. The footer says who you're signed in as. On a laptop it's the same address in any browser, with nothing to install.

## The app day to day

- **Today** shows the latest daily report and **This Week** the latest weekly one. The ↻ button at the top loads them again; the app also reloads when you come back to it after a few minutes.
- **A yellow banner at the top** means the newest report isn't from today (in your time zone, which the app reads from your profile). It names the day the report is from. Nothing is hidden: the older report is shown, labeled.
- **Updates:** when a new version has been built, a bar says "A new version is ready." Tap **Update** and the app reloads on it. The footer's `build` code is a hash of the app's own files, so it changes only when the app does, not on every push to `main`.
- **Sign out** is at the bottom. A sign-in that expired signs you out with a note; sign in again.
- A card kind the app has never seen shows as a plain card (title, detail, reason), so the API can add kinds without breaking an installed app.

## The app shows yesterday

Start with the banner: it says which day the newest report is from.

| What you see | Meaning and fix |
|---|---|
| A banner: the report is from yesterday (or older) and today's hasn't been built | The morning chain hasn't produced today's report. Before about 06:20 Eastern, wait: the chain starts at 06:17. Otherwise look at Healthchecks, then the newest report per league: `scripts/fh-sql.sh "select league_id, kind, for_date, generated_at from reports order by generated_at desc limit 6"`. A failed report job alerts your phone and is the usual cause; `gcloud run jobs executions list --job daily --region us-east4` and the job's log say why. |
| One league says "No report was built for this league…" while the others are current | That league's report failed or was skipped in the run. The job's log says why. |
| "Couldn't refresh. Showing what loaded at …", with the old cards still there | The phone had no connection, or the API is down. The cards are the last ones the app loaded, with the time it loaded them. Tap ↻ again. |
| It shows yesterday's games' report with no banner | That's right: a morning report covers the night before, and its date is today's. |
| An old version (the footer's `build` code is behind) | Close the app from the app switcher and open it again, or tap **Update** when the bar shows. Cloudflare's latest build is on the Worker's **Deployments** tab. |

## If something is wrong with the app

| What you see | Meaning and fix |
|---|---|
| "This build isn't set up", naming a variable | The build is missing one of the three values (Part 2, step 1), or one isn't a web address. Fix it in Cloudflare (Settings → Build → Variables and secrets), then **Deployments** → **Retry build**. A changed variable only reaches the app when the app is rebuilt. |
| Sign-in says "Couldn't reach the sign-in service" | `VITE_SUPABASE_URL` is wrong in the build, or the phone is offline. |
| Sign-in works, then both tabs say "Couldn't reach the server" | Almost always the API refusing the app's address: `fh-app-origins` isn't exactly this address, or the Deploy wasn't re-run (Part 2, step 4). On a laptop, the browser's console says "CORS" when that is it. `VITE_API_URL` wrong in the build looks the same. |
| "That email and password don't match." | The password from Part 1, step 2 (Supabase → Authentication → Users shows the user, and whether it's confirmed). |
| "This sign-in isn't set up" | The sign-in worked, but the API found no user of ours with that email (a 403). It must be the email on your user row. |
| "Your sign-in expired. Please sign in again." | The API refused the token (a 401). Sign in again. If it happens every time, the API's `fh-supabase-url` isn't this Supabase project's. |
| The Worker's build fails on `npm ci` | The lockfile and `package.json` disagree, or the wrong npm ran (see step 3). Tell Claude with the log. |
| The Worker's build fails with a message about the Worker's name | The name in step 3 differs from `name` in `web/wrangler.jsonc`. Rename the Worker in Cloudflare to `fantasy-helper`. |
| The Home Screen app asks you to sign in again after you removed and re-added it | Normal: a re-added app starts with empty storage. |
| A new version never arrives | Close the app completely and open it twice: a new version installs in the background on one open and takes over on the next, or when you tap **Update**. |

## Changing a build variable or the app's address
- **A build variable:** change it in Cloudflare and rebuild (the table above). Every push to `main` rebuilds anyway.
- **The app's address** (a domain, later): add it to `fh-app-origins` the same way as step 4 (the secret takes several addresses, separated by commas), re-run the Deploy, and **reinstall the app on the iPhone** from the new address.

## Rotating or changing a value
`scripts/gcp-secrets.sh <name>` saves a new version and turns the older ones off. The service reads a secret when it starts, so run **Actions → Deploy → Run workflow** afterwards.

## Later: the emailed code, for friends at the beta
You sign in with a password. Friends can't be given a password you made for them, so the beta signs people in with a 6-digit code emailed to them (a tapped link would open Safari, not the app on the Home Screen). **The code is already in the app**: set the build variable `VITE_SIGN_IN_WITH_CODE` to `true` in Cloudflare and rebuild, and the sign-in screen gains "Email me a code instead". It never creates an account (sign-ups stay off), so an email that isn't a user is refused. What it needs first, in Supabase: a **custom email sender** (Authentication → SMTP Settings; Supabase's built-in one reaches only your own organization's members, 2 an hour, and no longer lets you edit the templates), and the **Magic Link** template showing `{{ .Token }}`. A sender on a domain you own is the right one for friends (Resend's free test address, `onboarding@resend.dev`, delivers only to its own account's email). This is part of the beta's setup, with the domain decision in [m3-plan.md](../m3-plan.md), and none of it is needed for you alone. `scripts/api-smoke.sh` already supports it: press Enter at the password prompt.
