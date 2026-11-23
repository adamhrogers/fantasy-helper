# The phone app: setup and day to day

**Written:** 2026-11-16. **Who:** Adam, with Claude checking each step. **Design:** [ADR 0029](../decisions/0029-the-phone-apps-api.md) (the API), [ADR 0009](../decisions/0009-pwa-vite-react.md) (the app) and [ADR 0030](../decisions/0030-push-the-report-jobs-tell-the-phone.md) (push, Part 3). The plan is [m3-plan.md](../m3-plan.md).

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

Then, under the Worker's **Settings → Build** (checked 2026-11-22):
- **Previews Base** tab (next to **Production** at the top): turn **Builds for Preview branches** **off**. It is on for a new Worker, so every pull-request branch, Dependabot's included, would build: unreviewed code would run its install scripts on Cloudflare, and the build would fail anyway, because `npx wrangler preview` wants a `previews` block that `web/wrangler.jsonc` doesn't have (found on the first docs-only PR). Only `main` builds.
- **Build watch paths:** leave the default include `*`. Every push to `main` rebuilds the app, which is harmless (about a minute, free, and the build code is a hash of the app's own files, so the phone sees no "new version" unless the app changed). Cloudflare's docs don't say whether a path like `web/*` counts from the repository or from the root directory, and a wrong guess would mean an app change never deploys.

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

## Part 3: notifications (Phase 4). About 15 minutes of your time

Every morning, a few minutes after 06:17 Eastern, your phone says **Today's report is ready**; on Mondays it also says **This week's overview is ready**. A tap opens the app on that tab. The report jobs send it (after the report is saved, once a day), the app only asks for permission and says where to send it. Design and the reasons: [ADR 0030](../decisions/0030-push-the-report-jobs-tell-the-phone.md).

**It needs an iPhone on iOS 16.4 or later, with the app on the Home Screen** (Part 2, step 5). Safari's own tabs can't get notifications, and the app shows a hint instead of a button when it isn't running from the Home Screen.

**Do these in order.** The key (step 3) has to exist before the merge, because the deploy mounts it and a secret with no value fails the deploy. Nothing here costs money, and nothing is sent to your phone until step 7.

### 1. Check out the PR's branch, without taking it over
Until the PR is merged, the new scripts exist only on its branch. (After the merge they are on `main`, and you can skip this step and the "back to main" step.)

```bash
git worktree list
```

If a line shows `claude/m3-part5-push-7f9cf6`, a Claude session still holds that branch, so a normal checkout would fail. This works either way:

```bash
git fetch && git checkout --detach origin/claude/m3-part5-push-7f9cf6
```

### 2. Create the two empty secrets
```bash
scripts/gcp-setup.sh infra
```

It only adds: the two secrets (`fh-vapid-private-key`, which only the morning report jobs may read, and `fh-vapid-public-key`, which the API serves to the app) and the permissions around them. It is safe to run again.

### 3. Make the keys
```bash
scripts/vapid-setup.sh
```

It makes the key pair and sends each half straight to Google. **You type nothing and there is nothing to paste.** It prints the public key (that one isn't secret: every browser is handed it) and says where each half went. It refuses to run a second time, on purpose: a new pair breaks every phone that has notifications on (see "Changing the keys" below).

### 4. Back to main

```bash
git checkout main
```

### 5. Merge the PR
Merge it in GitHub (Claude can do it when you say so). The deploy migrates the database (migration 0008 adds `reports.notified_at`), then moves the API and the report jobs onto the new image. Watch it:

```bash
gh run watch
```

Cloudflare rebuilds the app from `main` at the same time (about a minute).

### 6. Turn notifications on, in the app, on your iPhone
1. Open **Fantasy** from the Home Screen icon (not Safari). If a bar says "A new version is ready", tap **Update**. If it doesn't show, close the app from the app switcher and open it twice.
2. On **Today**, scroll to the bottom, above "Signed in as…". Tap **Enable notifications**.
3. iOS asks to allow notifications. Tap **Allow**. (iOS asks once. If you tap Don't Allow, the app says notifications are blocked and how to allow them in Settings.)

**Right looks like:** the button turns into "Notifications are on." with **Turn off** beside it.

### 7. Check the whole path
```bash
gcloud run jobs execute daily --region us-east4 --args=push,test --wait
```

This runs the `daily` job with its command replaced, so it has the same key and database as the morning run. It sends one **Test notification** to every phone that has notifications on. It isn't a morning run: it changes no report and starts nothing after it.

**Right looks like:** the command ends with "Execution ... has successfully completed" and your phone shows **Test notification** within a few seconds. The answer also arrives as an ops notification on your phone ("Push test: sent"), with a line per device. If it fails, the same notification says why ("Push test: not delivered"); "No notification came" below has the meanings.

### 8. Wait for a real morning
Leave it. The next morning, a few minutes after 06:17 Eastern, **Today's report is ready** arrives (the chain finishes around 06:20), and a tap opens Today. A rerun the same day stays quiet. **Right looks like:** one notification, a tap that lands on Today, and no second one. On a Monday there is a second, for the weekly overview, with a tap that lands on This Week.

To see whether a report was announced (the `notified_at` column is empty when it wasn't):

```bash
scripts/fh-sql.sh "select kind, for_date, notified_at from reports order by generated_at desc limit 6"
```

## No notification came

Start with step 7's answer (the ops notification on your phone): it names the push service, the status and what it means. For a morning run, the run's warning says the same ("Report built, but the push service refused the push…"). The job's own log has the same lines:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="daily"' --freshness 2h --limit 40 --format 'value(textPayload)'
```

| What you see | Meaning and fix |
|---|---|
| The app shows a hint about the Home Screen and no button | It was opened from Safari. Open it from the Home Screen icon. If there is no icon, Part 2, step 5. |
| "This browser can't show notifications from this app" | The iPhone is older than iOS 16.4, or the browser isn't Safari-based. Update iOS. |
| "Notifications are blocked for this app" | iOS was told no. iPhone Settings → Notifications → **Fantasy** → Allow Notifications, then open the app again. |
| Tapping Enable says "Couldn't turn on notifications on this phone (AbortError)" | Apple's push service didn't register the device. Close and reopen the app and tap again; if it repeats, remove the Home Screen icon and add it again (Part 2, step 5). Tell Claude if it still fails. |
| Tapping Enable says "The server didn't accept this request (status 422)" | This browser's push service isn't one the app sends to. The API saves only Apple's, Google's, Mozilla's and Microsoft's push addresses (ADR 0030): Safari on the iPhone and the Mac, Chrome and other Chromium browsers, Firefox and Edge work. Tell Claude which browser it was. |
| Tapping Enable says "The server had a problem (status 503)" | The API has no public key: step 3 hasn't run, or the API hasn't restarted since. Actions → Deploy → Run workflow (the API reads its secrets when it starts). |
| The push test says "no device has notifications on" | Step 6 hasn't worked yet, or it was done in a copy of the app that has since been removed (a re-added icon is a new device). Do step 6 again. |
| The push test says `FH_VAPID_PRIVATE_KEY isn't set` | The job doesn't have the key: step 3 hasn't run, or the deploy hasn't run since. Run Actions → Deploy → Run workflow and try again. |
| The push test says `FH_APP_ORIGINS has no https address` | `fh-app-origins` still holds the placeholder. Part 2, step 4 puts the app's address in it. |
| `refused HTTP 403` or `401` (the reason is often `BadJwtToken`) | Apple didn't accept our key or our address. Two causes: **the address**: the **first** address in `fh-app-origins` must be the app's own `https://…` address, exactly (no path, no trailing `/`): `scripts/gcp-secrets.sh fh-app-origins`, then run the test again (the jobs read secrets each time they start; the API needs Actions → Deploy → Run workflow too); or **the key pair**: the phone subscribed with a different pair than the job holds. Turn notifications off and on again in the app. If it still refuses, `scripts/vapid-setup.sh --rotate` (see below). |
| `blocked … not a known push service` (or a run's warning that a device "isn't on a known push service") | That device's address isn't on the list of push services, so nothing was sent to it and it was deleted. A device turned on in the app can't get here (the API refuses such an address), so it is an old row or one written another way. If the host it names is a real browser's push service, tell Claude: it is added to the list in `src/fantasy_helper/domain/push.py`. |
| `refused HTTP 413` | A message over the size limit, which can't happen with counts only. Tell Claude. |
| `gone HTTP 410` (or 404) | The phone's subscription is dead (the icon was removed, or notifications were withdrawn). The job deleted it. Open the app and tap Enable notifications again. |
| `failed … couldn't be reached or is having trouble` (the reason may be `timed out`, `out of time` or `TooManyRedirects`) | A push service outage, a timeout, or a push service that answered with a redirect (never followed). Each send has 8 seconds and the whole push 30, so a slow service can't hold the morning up. Run the test again in a minute. A morning run that hit this left the report unannounced, so a rerun of that run announces it. |
| The push test passes but the phone shows nothing | The phone is hiding it. iPhone Settings → Notifications → **Fantasy**: Allow Notifications on, with Lock Screen and Banners ticked; check Focus and Do Not Disturb. |
| The morning report was built but nothing came, and no warning | Check `notified_at` (step 8). **Empty** and no warning means no phone had notifications on at 06:20 (the app's button, step 6). **Set** means a push was accepted: look at the phone's settings (the row above). On a day that isn't Monday the weekly overview is never announced, on purpose. |
| Two notifications at the same time | One per phone that has notifications on. A phone and a laptop each get one. |
| A tap opens the app, but not on Today | Tell Claude: the tap's path is one of two fixed ones, and a test pins it. |

## Turning notifications off
In the app, **Turn off** at the bottom of Today: the phone drops its subscription and the server forgets it. (Removing the Home Screen icon does the same, one push later: the push service answers "gone" and the job deletes the device.)

## Which days the weekly overview notifies
Mondays, in your time zone. The weekday is one number, `WEEKLY_NOTIFY_WEEKDAY` in `src/fantasy_helper/pipeline/notify.py` (0 is Monday, 6 is Sunday). It is yours to change: tell Claude, or change it and merge. The daily report notifies every morning.

## Changing the keys
Almost never needed, and it costs you a few taps: **a new pair stops every phone that has notifications on from getting them,** until each is turned off and on again in the app. Do it only if the private key may have leaked, or the table above sends you here.

```bash
scripts/vapid-setup.sh --rotate
```

It tells you what it will break and waits for you to type `rotate`. Afterwards run **Actions → Deploy → Run workflow** (the API reads its key when it starts), then on each phone open the app: it says notifications are off (it noticed its subscription belongs to the old key) and the button makes a new one. Then run step 7 again.

## Changing the app's address
The push subscription belongs to the app's address, and so does the installed app (Part 2): moving to a domain means a reinstall, and then step 6 again. The job takes the address it presents to the push service from the first address in `fh-app-origins`, so put the new address first.
