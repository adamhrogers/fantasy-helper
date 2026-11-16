# Looking at the prod database safely (read-only access)

**Written:** 2026-11-16. **Design:** [ADR 0032](../decisions/0032-a-database-login-that-can-only-look.md). **Who:** Adam runs the setup once; after that, anyone at the laptop (Adam, or Claude with his approval) can ask the database questions without being able to change anything.

## What this is

A second database login, `fh_readonly`, that can **look** at the data tables and nothing else. It can't write, delete or create anything, and it can't see the table that holds the encrypted ESPN logins. `scripts/fh-sql.sh` runs one question through it. The owner's URL (the one that can do everything) is never used for looking.

## Set it up (once, about 3 minutes)

After the PR that adds this is merged, from `~/projects/fantasy-helper`:

```bash
git pull
```

```bash
scripts/readonly-setup.sh
```

It makes the login, **proves** it from the other side (it can read; it can't create a table; it can't read the encrypted logins), and only then saves its URL in Secret Manager. The password is never shown. Expect:

```
-> creating the login (PRODUCTION database)
-> checking it from the other side
  ✓ fh-readonly-database-url: new version saved
```

Anything starting with `✗` means the login was **not** saved: tell Claude. Then try it:

```bash
scripts/fh-sql.sh "select count(*) from players"
```

It needs `psql` installed (it's found at `/usr/local/opt/postgresql@17/bin/psql` even when it isn't on the path) and `gcloud` signed in.

## Using it

One question at a time, in quotes. It shows up to 200 rows (`--limit N` for more) and cuts long cells (`--wide` shows them whole):

```bash
scripts/fh-sql.sh "select kind, status, started_at, ended_at from runs order by started_at desc limit 10"
```

```bash
scripts/fh-sql.sh "select relname, pg_size_pretty(pg_total_relation_size(oid)) as size from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r' order by pg_total_relation_size(oid) desc"
```

```bash
scripts/fh-sql.sh "select kind, for_date, generated_at from reports order by generated_at desc limit 10"
```

**What comes back is your private data.** It stays out of the repository: the repo is public, so no query result, name or number from it goes into a doc, a commit or a pull request.

## Letting Claude use it

Claude Code asks you before it runs a command that reads prod, and that is your safety setting, not a limit of the tool. Approve a command once, or tell Claude you want `scripts/fh-sql.sh` allowed so it stops asking. Either way it can only read: the login has no way to write, and `fh sql` refuses any login that could.

## After a migration adds a table

A new table is hidden from the login until the setup runs again, so a missing table is the sign. Run `scripts/readonly-setup.sh` once more (it also rotates the password). A table that will hold anything private is added to `kept_out` in `scripts/readonly-login.sql` **before** it ships.

## Rotating or removing it

- **Rotate:** `scripts/readonly-setup.sh` again. Older secret versions are disabled.
- **Remove:** in Supabase's SQL editor, `DROP OWNED BY fh_readonly; DROP ROLE fh_readonly;`, then `gcloud secrets delete fh-readonly-database-url`.

## When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `✗ the new login can't read the data` | The grants didn't take | Tell Claude; nothing was saved |
| `✗ ... can CREATE a table` or `... can read the encrypted league logins` | The login has too much power | Tell Claude; nothing was saved |
| `permission denied for table ...` from `fh-sql.sh` | The table is kept out on purpose, or it's new | See "After a migration" above |
| `refusing to run: this login isn't read-only` | `FH_READONLY_DATABASE_URL` holds a login that can write | Run the setup again; never put the owner's URL there |
| `canceling statement due to statement timeout` | The query ran past 30 seconds | Ask for less (a narrower `where`, a smaller `limit`) |
