# M3 setup: accounts and secrets Adam creates while M2 runs

**Written:** 2026-11-06. **Who:** Adam, by hand. **Time:** about an hour in total. **Cost:** $0 (every service stays inside its free tier; Google Cloud and Cloudflare R2 may ask for a card on file).

This prepares everything M3 needs, so that M3 can start building as soon as M2's first pieces land. It doesn't build or deploy anything.

**Checked 2026-11-06, at the start of M3:** the repository secret `LANGSMITH_API_KEY` is set, and CI's LangSmith smoke trace passes on `main`. Nothing else is done yet: there's no `prod` environment and `gcloud` isn't installed. Two fixes since the first version: the gcloud install command (section 5), and a new section 6 for the missed-run watchdog that docs/runbooks/run-events.md plans for M3.

**Two rules for every step:**
- **Secrets never go into the chat.** As soon as a value appears, save it in your password manager. Where a step ends in `gh secret set`, run it in your own Terminal app and paste the value at its hidden prompt.
- **Names, regions and project IDs aren't secret.** Tell me those when you're done.

All production secrets go into a GitHub **environment** called `prod`, not into plain repository secrets. Only the deploy and scheduled jobs get access to it, so CI runs on pull requests can't touch production.

---

## 1. GitHub: the `prod` environment (5 min)

- [ ] On GitHub, open the repo and go to **Settings → Environments → New environment**. Name it `prod`.
- [ ] Under **Deployment branches and tags**, choose **Selected branches and tags** and add `main`.
- [x] Finish the open item from M1: set the LangSmith key as a *repository* secret so CI can send its smoke trace. **Done** (set 2026-11-06; the smoke trace passes on `main`). For reference, the command was:

```bash
gh secret set LANGSMITH_API_KEY
```

- [ ] Add the same key to `prod`, because production jobs trace too:

```bash
gh secret set LANGSMITH_API_KEY --env prod
```

## 2. Supabase: the production project (10 min)

- [ ] Open [supabase.com/dashboard](https://supabase.com/dashboard). Go to **Organization settings → Billing** and check that it says **Free plan**. This confirms the limits in ADR 0021: a 500 MB database, 2 active projects, no automatic backups.
- [ ] Create a **New project** in the same organization as dev:
  - **Name:** `fantasy-helper-prod`
  - **Region:** **East US (North Virginia)**, which is `us-east-1`, the same region as dev (ADR 0020)
  - **Database password:** click Generate, then save it in your password manager right away. Supabase won't show it again.
- [ ] Don't create a third project. The free plan allows two active projects, and dev plus prod uses both.
- [ ] When the project is ready, click **Connect** and copy two connection strings into your password manager, each with your password filled in:
  - **Session pooler** (port 5432): this is the prod `DATABASE_URL`
  - **Transaction pooler** (port 6543): the API may use this one
- [ ] Put the session pooler URL into the `prod` environment:

```bash
gh secret set DATABASE_URL --env prod
```

- [ ] Save the **Project URL** and the **publishable (anon) key** from **Project Settings → API** in your password manager. They aren't secret (the phone app ships them), but they stay out of the public repo. M3 puts them where they're needed.
- [ ] Go to **Authentication → Sign In / Providers**. Keep **Email** on, and turn **off** "Allow new users to sign up". The app is invite-only.
- [ ] Leave Site URL, redirect URLs and email templates alone. M3 sets them once the app has an address.

**Good to know:** Supabase's built-in email only delivers to members of your Supabase organization, and at most 2 emails an hour. That's fine for you as user one, as long as you sign in to the app with **the same email your Supabase account uses**. Friends (M7) will need a custom email sender, which needs a domain you own (see section 6).

## 3. Encryption key and alerts for production (5 min)

Production gets its **own** encryption key, different from dev (ADR 0007). Losing it isn't a disaster: you would re-enter your ESPN cookies. Keep it in your password manager anyway.

- [ ] In your own Terminal app (not the Claude app), from the project folder, generate the key and copy it into your password manager:

```bash
uv run fh keygen
```

- [ ] Paste it into the `prod` environment:

```bash
gh secret set FH_ENCRYPTION_KEY --env prod
```

- [ ] Give production its **own** ntfy topic, separate from the laptop's (decided 2026-11-07). You can tell at a glance which machine is talking, mute one without the other, and a leaked dev topic doesn't expose prod. Generate one in your own Terminal app and copy it into your password manager:

```bash
uv run fh keygen --topic
```

- [ ] Put it into the `prod` environment:

```bash
gh secret set FH_NTFY_TOPIC --env prod
```

- [ ] In the ntfy app on your iPhone, tap **+**, subscribe to the new topic, and name it "Fantasy Helper prod". Keep the old subscription: the laptop still uses it.

## 4. Cloudflare: R2 for the raw archive and backups (10 min)

R2 holds every raw snapshot forever, the end-of-season archives and the nightly database backups (ADR 0021). The free tier gives 10 GB of storage, 1 million writes and 10 million reads a month, and downloads are free. We expect to use about 0.3 GB a season.

- [ ] Sign up at [dash.cloudflare.com](https://dash.cloudflare.com) on the Free plan, and turn on two-factor authentication.
- [ ] Open **R2 Object Storage** and finish the R2 checkout. It's free; Cloudflare may ask for a card on file.
- [ ] **Create bucket:**
  - **Name:** `fantasy-helper-raw`
  - **Location:** Automatic, with the hint **Eastern North America (ENAM)**
  - **Default storage class:** **Standard**. Infrequent Access isn't covered by the free tier.
  - **Public access:** leave it off
- [ ] Go to **R2 → Manage API tokens → Create API token**:
  - **Permissions:** **Object Read & Write**
  - **Scope:** **Apply to specific buckets only**, then pick `fantasy-helper-raw`
  - **TTL:** forever
- [ ] Copy the **Access Key ID**, the **Secret Access Key** (shown only once) and the S3 endpoint (`https://<account id>.r2.cloudflarestorage.com`) into your password manager. Then run these three commands one at a time, pasting each value:

```bash
gh secret set FH_R2_ACCESS_KEY_ID --env prod
```

```bash
gh secret set FH_R2_SECRET_ACCESS_KEY --env prod
```

```bash
gh secret set FH_R2_ENDPOINT --env prod
```

- [ ] Skip Cloudflare Pages for now. In M3 you'll click **Connect to Git** once and authorize Cloudflare's GitHub app. M3 also asks you to put the three R2 values into your local `.env` for the one-time upload of the laptop's snapshot backlog.

## 5. Google Cloud: Cloud Run for the API (15–20 min)

The Cloud Run free tier covers 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds a month. `us-east4` (Northern Virginia) is a Tier 1 pricing region, next to the database.

- [ ] At [console.cloud.google.com](https://console.cloud.google.com), create a project called `fantasy-helper`. **Write down the project ID**; it can differ from the name.
- [ ] Go to **Billing** and link a billing account. Cloud Run requires one, even when usage stays inside the free tier.
- [ ] Go to **Billing → Budgets & alerts → Create budget**. Scope it to this project, set **$5 a month**, and turn on email alerts at 50%, 90% and 100%. A budget only warns you; it doesn't stop spending. M3 also caps the service at one or two instances.
- [ ] Update Homebrew first. Yours is from November 2025, and it can't install the current Google Cloud package, which has also been renamed from `google-cloud-sdk` to `gcloud-cli`:

```bash
brew update
```

- [ ] Install the Google Cloud command-line tool:

```bash
brew install --cask gcloud-cli
```

- [ ] Sign in. This opens your browser:

```bash
gcloud auth login
```

- [ ] Point it at the new project, using your project ID:

```bash
gcloud config set project YOUR_PROJECT_ID
```

- [ ] Set the default region:

```bash
gcloud config set run/region us-east4
```

- [ ] Turn on the four services M3 uses (Cloud Run, Artifact Registry for images, Secret Manager, and keyless deploys from GitHub):

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com
```

GitHub Actions will deploy without a key file, using Workload Identity Federation. M3 gives you the exact commands to run for that.

## 6. Healthchecks.io: the missed-run watchdog (5 min)

A run that never starts can't report itself: the GitHub scheduler skipped it, or scheduled workflows were turned off. Healthchecks.io expects a ping from each scheduled run and alerts you when one doesn't arrive on time. The same check covers the 7:45 a.m. late-report alert (docs/runbooks/run-events.md). It runs outside both GitHub and Google Cloud, so it still works when they don't. The free Hobbyist plan has 20 checks; M3 uses about five.

- [ ] Sign up at [healthchecks.io](https://healthchecks.io) on the free plan, and turn on two-factor authentication.
- [ ] Open your project and go to **Integrations → ntfy → Add integration**:
  - **Server URL:** `https://ntfy.sh`
  - **Topic:** the prod topic from section 3
  - **Priority for "down" events:** High
- [ ] Remove the **email** integration that's on by default, unless you also want these alerts by email.
- [ ] Go to **Settings** (the project's, not your account's) → **Ping key** → **Create**. Copy it into your password manager, then run:

```bash
gh secret set FH_HEALTHCHECKS_PING_KEY --env prod
```

- [ ] Don't create any checks. The scheduled jobs create them on their first ping, and in M3 I'll give you each one's schedule to set (about two minutes).

## 7. Your phone, and the later items (2 min)

- [ ] Check that your iPhone runs **iOS 16.4 or later** (Settings → General → About). Web Push needs it, and the app has to be added to the Home Screen; we'll do that together in M3.
- [ ] **Later, not now:**
  - **Before M4:** the Claude API key goes into `prod` with `gh secret set FH_ANTHROPIC_API_KEY --env prod`, with a monthly spend limit set in the Anthropic Console.
  - **Before M7:** friend invites need a custom email sender (for example Resend's free tier). That requires a domain you own, about $10–15 a year. Decide whether you want one before the beta.
  - **Before M8:** an API key from the second model provider, for the Skeptic.

---

## When you're done, tell me

- "Prod Supabase created in us-east-1, sign-ups off"
- "R2 bucket and token done"
- "GCP project ID: …, billing and the $5 budget on, gcloud signed in"
- "Healthchecks ntfy integration and ping key done"

I'll check with `gh secret list --env prod`, which shows names only, never values. The list should have these 8 names:

| Secret (`prod` environment) | From |
|---|---|
| `DATABASE_URL` | Supabase prod, session pooler |
| `FH_ENCRYPTION_KEY` | `uv run fh keygen` (new, prod only) |
| `FH_NTFY_TOPIC` | `uv run fh keygen --topic` (new, prod only) |
| `LANGSMITH_API_KEY` | LangSmith (also set as a repository secret, for CI) |
| `FH_R2_ACCESS_KEY_ID` | Cloudflare R2 API token |
| `FH_R2_SECRET_ACCESS_KEY` | Cloudflare R2 API token |
| `FH_R2_ENDPOINT` | Cloudflare R2 API token |
| `FH_HEALTHCHECKS_PING_KEY` | Healthchecks.io project settings |

**I add these myself in M3:** the Web Push (VAPID) keys, generated straight into secrets, and the Cloud Run deploy identity.
