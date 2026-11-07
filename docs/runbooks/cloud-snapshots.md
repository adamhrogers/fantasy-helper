# Moving snapshots to the cloud (M3 part 1)

**Written:** 2026-11-06. **Who:** Adam, with Claude checking each step. **Time:** about 15 minutes, then two days of overlap. **Design:** ADR 0023.

The snapshot job moves from the Mac's schedule (launchd) to GitHub Actions, and the raw files move from `data/raw` to the R2 bucket. No day may be missed (rule 8), so the Mac keeps running until the cloud has two clean days behind it.

**Before you start:**
- Sections 1, 3 and 4 of [m3-account-setup.md](m3-account-setup.md) are done: the `prod` environment exists with the three `FH_R2_*` secrets, and **`FH_NTFY_TOPIC` is set in `prod`** (a topic of its own). Without the topic, cloud runs can't reach your phone, so the job goes red on purpose.
- `FH_R2_ENDPOINT` in `prod` is exactly the value in your `.env` (checked live on 2026-11-07): `https://<account id>.r2.cloudflarestorage.com`, with **nothing after `.com`**. Cloudflare's bucket page shows the address with `/fantasy-helper-raw` on the end; with that ending, writes would land in the wrong place. If you're not sure, set it again from `.env`: `gh secret set FH_R2_ENDPOINT --env prod`.
- The M3 part 1 PR is merged, and your main checkout is up to date (`git pull` in `~/projects/fantasy-helper`).

All commands run in your own Terminal app, from `~/projects/fantasy-helper`.

## 1. Give the laptop the R2 values (2 min)

Open `.env` in an editor and add the three values from your password manager. Don't paste them into the chat. Leave `FH_RAW_STORE` out for now, so the laptop still writes locally:

```
FH_R2_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
FH_R2_ACCESS_KEY_ID=...
FH_R2_SECRET_ACCESS_KEY=...
```

Check that the bucket answers and refuses overwrites:

```bash
uv run fh doctor --only raw
```

Expect `✓ raw  R2 bucket 'fantasy-helper-raw' configured (FH_RAW_STORE=local); it refuses to overwrite objects`. Anything else: stop and tell Claude.

## 2. Upload the backlog (2 min)

See what would go up. It should be every file, about 1,700 on 2026-11-06:

```bash
uv run fh raw upload --dry-run
```

Upload them. Each file is checked against R2's listing afterwards:

```bash
uv run fh raw upload
```

Run it once more. It should say every file is already in R2, and upload nothing:

```bash
uv run fh raw upload
```

## 3. Switch the laptop to R2 (1 min)

Add this line to `.env`:

```
FH_RAW_STORE=r2
```

From now on the Mac's scheduled snapshots, `fh ingest` and `fh run` all read and write the bucket. `data/raw` stays where it is as a backup copy and stops growing. If a snapshot ran between steps 2 and 3, catch it with one more upload:

```bash
uv run fh raw upload
```

## 4. The first cloud run (3 min)

On GitHub: **Actions → Snapshot → Run workflow**, choose **evening** (it's safe at any hour), and run it. This is the first time ESPN and Sleeper are called from GitHub's servers.

- The run should finish green in a minute or two.
- Your phone gets a quiet `Evening snapshot succeeded` on the **prod** topic.
- A wrong endpoint or bad keys still save the day's files, then fail loudly: `Evening snapshot: raw store check failed`.
- If it fails, the phone says why. Tell Claude; the Mac is still saving every day.

## 5. Two days of overlap

Both schedules now run: the cloud at 06:17 and 17:17 Eastern, the Mac at 06:30 and 17:30. You'll get two quiet success notifications per set. On mornings, the second one says `1 already saved today by another run`: Sleeper's player list is fetched only once a day.

After **four clean cloud runs** (two mornings, two evenings), tell Claude. If any cloud run failed, the overlap restarts once it's fixed.

## 6. Remove the Mac's schedule (1 min, with Claude's go-ahead)

```bash
scripts/snapshot-schedule.sh uninstall
```

```bash
scripts/snapshot-schedule.sh status
```

Both jobs should say `not installed`. Keep `FH_RAW_STORE=r2` in `.env`, so commands you run by hand use the same history as the cloud.

**To roll back** at any point before step 6: delete the `FH_RAW_STORE=r2` line, and the Mac writes locally again. The files it saved to R2 stay there, and nothing is lost either way.
