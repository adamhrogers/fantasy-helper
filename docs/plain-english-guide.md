# Fantasy Helper in plain English

**Written:** 2026-11-15, for Adam, as a guide with no assumed technical background. **Where things stand:** this describes the project as it is on 2026-11-15 and says clearly which parts are built, which are running, and which are still to come. The technical documents (`session-handoff.md`, `architecture.md`, the ADRs) hold the details; this one holds the picture.

---

## 1. What this is

Fantasy Helper is a **fantasy basketball assistant**. Every morning it will send a short briefing to your phone: specific moves for each of your leagues, worked out using each league's own scoring rules. Then it **keeps score on itself**: every recommendation it makes is written down before the games start, can never be edited afterwards, and gets graded against a simple baseline all season. At the end you can see exactly how much it helped, or didn't.

Two rules shape everything:

1. **Code does all the math. An AI never makes up a number.** Every score, projection and win chance comes from ordinary, testable programs. (An AI will be added later to *explain* and *argue about* the numbers. It is not in the system at all yet.)
2. **Nothing it recommends can be quietly changed afterwards.** That is what makes the grading honest.

## 2. The whole thing in one picture

Think of a small newsroom that works while you sleep.

```
 THE WORLD                 THE FILING CABINET         THE DESK                  YOU
 ─────────                 ──────────────────         ────────                  ───
 ESPN, Sleeper   ──►  1. Collect: save a copy  ──►  3. Load it into  ──►  4. Do the math  ──►  5. Write the  ──►  6. Your phone
 (players, scores,       of everything twice         the organized          and write the         report, save        (not built yet)
  injuries, your         a day, never erase          notebook               calls down            it
  leagues)               it (Cloudflare R2)          (the database)         before tip-off

                                                  7. A watchdog and phone alerts tell you if anything breaks
```

Steps 1 to 5 are **built and running in the cloud**. Step 6 (the phone app) is **not built yet**. Step 7 is built.

## 3. Each piece, one at a time

### The sources (where the information comes from)
- **ESPN** and **Sleeper** are the two fantasy sites your three leagues live on. They publish a lot of public information: every NBA player, injury status, the schedule, each night's box scores, and projections (guesses at how many points a player will score).
- Your **league details** (your roster, who you're playing, transactions) are private. ESPN only shows them with your login "cookies", which you typed in once. Sleeper's league data is public, so it only needs your league ID.

### The snapshots (collecting, twice a day)
At **06:17 every morning** and **17:17 every evening** (Eastern), a small program visits ESPN and Sleeper and **saves an exact copy of what they say**. It never overwrites an old copy. Why so careful? A day that's missed **can never be recovered**: tomorrow's website doesn't remember what yesterday's said. Later, when we grade our advice, we need to know exactly what the world looked like when we gave it.

### R2 (the filing cabinet)
The copies go into an online storage bucket from **Cloudflare**, called R2. Think of it as a very large filing cabinet that keeps everything forever for pennies. It currently holds roughly 2,000 files. Both your laptop and the cloud can read from it.

### Google Cloud and "jobs" (so the laptop can be closed)
A **job** is a small program that Google runs for us, in one of its data centers, on a schedule. Five exist:

| Job | What it does |
|---|---|
| `snapshot-morning` | Collects the morning set (06:17). |
| `snapshot-evening` | Collects the evening set (17:17). |
| `daily` | Builds the daily report. |
| `weekly` | Builds the weekly overview. |
| `migrate` | Updates the database's structure when the code changes. |

**Cloud Scheduler** is Google's alarm clock. It starts the two snapshot jobs on the minute. We chose it after finding that GitHub's own alarm clock ran **hours late** every day for a week.

### The morning chain
The three morning jobs run **one after another, each starting the next only when it is truly finished**:

```
snapshot-morning ──(only if every dataset saved and looked sane)──► daily ──► weekly
```

If any piece of the morning's data failed, nothing after it runs, because a report built on partial data is worse than no report. You get an alert saying what failed. This is the "chain" you'll see mentioned in the docs.

### The database (the organized notebook)
The snapshots are raw copies, like photographs of pages. The **database** is the organized notebook the program actually works from: players, which team each plays for, the schedule, box scores, projections, your rosters, matchups, transactions, and, once built, the **list of every call the app has made** and **every saved report**. It's run by a company called **Supabase** (free plan, 500 MB).

There are **two databases**, on purpose:
- **dev**: a practice copy that your laptop uses. Safe to break.
- **prod** ("production"): the real one the cloud jobs use. Today it holds all the history and your three leagues.

It is currently about **45 MB of the 500 MB**. About 12 MB of that was waste caused by a bug (a table rewriting unchanged rows every day). The bug is fixed; the wasted space comes back once you run one cleanup command. At today's pace the season should end around 35 to 40% full.

Everything stored about your leagues is locked down: Supabase's public door can read none of it, and your **ESPN cookies are encrypted** with a key that lives separately from the database.

### The engine (the math desk)
The engine is a set of programs that only do arithmetic. They don't touch the internet or the database, which makes them easy to test. They work out, for each league's own rules:
- **Fantasy points** for any player's night (scoring differs by league, including bonuses).
- **Lock or pass** (your Sleeper league lets you "lock in" a night's score or wait for a later game; the engine works out which is better).
- **Win probability** for your weekly matchup.
- **Streaming moves**: which free agent to add and who to drop, within your league's limits.

Its numbers are checked against ESPN and Sleeper's own totals every morning.

### The reports
The daily and weekly jobs turn the engine's results into **reports made of short cards**, one decision per card, each with its reason:
- **Daily** (your Sleeper league only, today): "Lock this player's 48 points" or "Pass".
- **Weekly** (all three leagues): your matchup and win chance, how many games each side can actually start, and add/drop suggestions. The 9-category league only gets the basics for now.

Each report is saved in the database.

### The call log (the "never edited" notebook)
Every recommendation is written into a special table **before the relevant games begin**, and the database itself refuses any attempt to change or delete a row. This is what grading will use. Running the same job twice does not log the same call twice.

### Matching players across sites
ESPN and Sleeper give the same player different ID numbers. A part of the system called the **crosswalk** matches them up, mainly by name and birth date. All of your rostered players are matched.

### Alerts and the watchdog (so you hear about problems)
- **ntfy** is a free app on your phone. Every run sends it short status messages: started, finished, or what went wrong. Quiet when fine, loud when not. There's one channel for your laptop's practice runs and one for the real cloud jobs. These are for *you as the operator*; they are not the product's notifications.
- **Healthchecks** is a separate watchdog service. Each job "checks in" when it starts and finishes. If a check-in is late or missing, Healthchecks alerts your phone. This catches the worst kind of failure: a job that silently never ran. It's also what triggers the "your report is late" warning at 07:45.

### Secrets and safety
- **Passwords and keys are never in the code** (which is public on GitHub) or in chat. They live in **Bitwarden** (your own safe), and in **Google Secret Manager** (a locked cabinet only specific jobs can open).
- The cloud jobs run as **two separate "robot accounts"**: one for collecting public data (it can't see the database or your ESPN login), one for building reports (it can). That way a problem in the part that talks to the internet can't reach your logins.
- A secret scanner checks every change for accidental leaks.

### GitHub, tests and automatic updates
- **GitHub** holds the code, with its full history.
- Every change goes into a **branch** (a side copy), then a **pull request (PR)**, which is a proposal to merge it into the main version. **You merge them.**
- On every PR, **CI** (automatic tests) runs several hundred checks. A change can't merge if they fail.
- When something merges into the main version, an automatic **deploy** builds the new program, packages it (that package is called an **image**), updates the five jobs to use it, and first updates the database's structure if needed.
- **Dependabot** is a bot that proposes small updates to the libraries the code uses, and most merge on their own once the tests pass.

### Your laptop
Your laptop still saves its own copy of the snapshots at 06:30 and 17:30, as a backup, until the cloud has proven reliable (four on-time scheduled runs in a row; the count is in `m3-plan.md`). Then the laptop job and GitHub's late spare copy are switched off. The laptop is also where you run `fh` commands.

## 4. The `fh` commands

`fh` is **the program's own command-line tool**: you type `uv run fh something` in a terminal. It's the same program the cloud jobs run, just started by hand.

| Command | What it does |
|---|---|
| `fh doctor` | Checks the setup is healthy. |
| `fh league add` / `fh league list` | Adds one of your leagues (asks for cookies privately), or lists them. |
| `fh snapshot` | Collects and saves a copy of the day's public data. |
| `fh ingest` | Loads the saved copies into the database. |
| `fh run daily` / `fh run weekly` | Builds the reports. |
| `fh report open` | Opens the latest saved report in your browser. |
| `fh migrate` | Updates the database's structure. |
| `fh raw upload` | Copies local snapshot files into R2. |
| `scripts/fh-prod.sh ...` | Runs one of the above safely against the real database. |

In Google Cloud, names starting with **`fh-`** are just our naming style ("Fantasy Helper"): the robot accounts `fh-jobs` and `fh-reports`, and secrets like `fh-database-url`.

## 5. A morning, start to finish

Today (as built):

| Time (ET) | What happens |
|---|---|
| 06:17 | Google's alarm clock starts `snapshot-morning`. It saves nine datasets to R2 (30 seconds or so). |
| ~06:18 | If all nine are good, it writes a "complete" marker and starts `daily`. |
| ~06:20 | `daily` loads the new data, works out lock-or-pass, logs the calls, saves the daily report, then starts `weekly`. |
| ~06:22 | `weekly` builds the overview for all three leagues and saves it. |
| all along | Each job checks in with Healthchecks and sends status messages to your phone. |
| 07:45 | If `daily` hasn't finished, Healthchecks warns you. |

**What you can do with the result today:** run `scripts/fh-prod.sh report open` on the laptop and the reports open in your browser.

## 6. What's not built yet, and why the "API" matters

The reports exist but are stuck in the database, and only your laptop can pull them out. The remaining part of this milestone (M3) gets them to your phone:

1. **The API.** A small always-ready service that works like a **doorman**. Your phone asks it "show me today's report"; it checks you're really you (via a sign-in), then hands over only your reports. The phone app is never allowed to touch the database directly. **The code for this was written tonight on a side branch. It isn't merged or running.**
2. **The phone app.** A web app you add to your iPhone's Home Screen, with sign-in by a 6-digit emailed code and two tabs, Today and This Week.
3. **Push notifications.** The "your report is ready" message from the app itself.
4. **Backups.** A nightly copy of the irreplaceable tables (the call log, your leagues) to a separate place. (Supabase's free plan has no backups.)
5. **The final test:** laptop shut, report arrives on the phone; a deliberately broken run raises an alert; a backup restores.

## 7. Later milestones, in one line each

| Milestone | What it adds |
|---|---|
| **M4** | The AI brain: it reads the math results and writes the explanations. It can never invent numbers; guardrails reject any it can't trace to the engine. |
| **M5** | Your watchlist: tell it which players you believe in and it watches them; better lock-or-pass decisions. |
| **M6** | Grading and the scorecard: how good were its calls, with graphs over time. |
| **M7** | The friends beta: invite a few friends to use it. |
| **M8** | A panel of specialist AIs, kept only if they beat a single AI at being right. |
| **The buddy** | The long-term main goal (your words, 2026-11-15): a chat you can talk to all the time. You give it opinions, it remembers them as takes that get graded, and the grading keeps it honest. Placement is proposed in the roadmap, waiting on you. |
| After | Trade tracking, 9-category leagues, then the end-of-season case study. |

Target for the friends beta is **early to mid-December** (hard stop **15 December**). The exact dates are proposed in the roadmap and wait for your confirmation.

## 8. What only you do

- Merge pull requests (after tests pass).
- Type secrets and ESPN cookies at private prompts. They never go through chat.
- Create accounts (done for everything M3 needs).
- Decide the open questions (listed in `m3-plan.md`).
- Turn things on or off in dashboards: for example, **Secret Manager audit logs** (step 11) and, after tomorrow's run, removing two now-unused secrets from GitHub (step 10).

## 9. The jargon, decoded

| Word | Meaning |
|---|---|
| **Repo / repository** | The folder of code, with its history, stored on GitHub. |
| **Branch** | A side copy of the code where a change is built without affecting the main version. |
| **PR (pull request)** | A proposal to merge a branch into the main version. |
| **Merge** | Accept the proposal. For this project, it also triggers a deploy. |
| **Deploy** | Automatically packaging the new code and putting it into the cloud. |
| **CI** | Automatic tests that run on every proposed change. |
| **Image** | The packaged program that Google runs. |
| **Job** | A program Google runs on request or on a schedule, which finishes and stops. |
| **Service** | A program that stays ready to answer requests. The API will be one. |
| **Scheduler** | Google's alarm clock that starts jobs. |
| **Secret** | A password or key, kept in a locked place. |
| **Database** | The organized notebook of the app's data. |
| **Migration** | An update to the database's structure (adding a column or table). |
| **Prod / dev** | The real setup / the practice setup. |
| **API** | A doorman between an app and the database. Each thing it can be asked for is a **route**. |
| **Token / sign-in** | A short-lived proof of who you are, issued when you sign in. |
| **Push notification** | A message the app sends to your phone's lock screen. |
| **PWA** | A website you can install on your Home Screen so it behaves like an app. |
| **ADR** | A short note recording a design decision: what, why, and what was rejected. |
| **Runbook** | Step-by-step instructions for a task. |
| **Registry** | The never-edited log of every recommendation. |
| **Engine** | The math-only code. |
| **FactSheet** | A compact bundle of engine facts for the AI to read (comes with M4). |
| **Cookies (ESPN)** | The two login values that let the app read your private ESPN leagues. |
