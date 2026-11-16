# ADR 0032: A database login that can only look

**Status:** Accepted · 2026-11-16 · extends ADR 0027 (operating the prod database) and ADR 0003 (one read-only tool server)

## Decision
- **A second database login, `fh_readonly`.** It can `SELECT` from every table in `public` except `platform_credentials` (the encrypted ESPN logins) and `push_subscriptions` (each phone's push address and keys, once part 3 adds them). It can't write, delete, truncate or create anything, isn't a superuser, can't bypass row-level security, belongs to no role, and may hold three connections.
- **It works through row-level security, not around it.** Every table has RLS on and no policies (CLAUDE.md), so the owner is the only one who sees rows. The login gets one `SELECT`-only policy per table it may read. The policies are made by `scripts/readonly-login.sql`, not by a migration: the migrations stay policy-free, and the login's reach is in one file.
- **New tables start hidden.** A table a later migration adds is invisible to the login until `scripts/readonly-setup.sh` runs again. A table that holds anything private is added to the file's `kept_out` list first.
- **The setup is one command that no secret passes through.** `scripts/readonly-setup.sh` reads the owner's URL from Secret Manager, makes a random password, runs the SQL as the owner, proves the login from the other side (it can read; it can't create a table; it can't read the encrypted logins), and only then saves `fh-readonly-database-url`. The password is never printed, never in a command line, never on disk. Each run rotates it.
- **The reader is `fh sql`**, run by `scripts/fh-sql.sh`. It reads `FH_READONLY_DATABASE_URL` (fetched from Secret Manager for that one process), never the owner's URL or the encryption key. It goes through the database driver, so psql's backslash commands (which can run shell commands) don't exist, it asks for read-only transactions, and it refuses any login that is a superuser, bypasses RLS or can write to a table.
- **Nothing in the cloud reads `fh-readonly-database-url`.** No job's service account is granted it.

## Why
Looking at the data is something we do all the time (is the report right, why does a count differ, is the database filling up), and the one login we had is the owner's: it can change and delete everything, including the call log's tamper protection (a trigger an owner could drop) and the encrypted logins. Reading never needs that. A login that can only look makes it safe to let a tool, a script or an assistant read prod, and it's the first piece of the read path the chat will use (ADR 0003's tool server will connect with it, not with the owner's URL).

The lock is that the login has **no write privilege at all**. The role-level `default_transaction_read_only` and the statement timeout in the setup file are belts and braces: a session can switch the setting off, and a test proves the writes are refused anyway. Supabase's documentation says the same of its own read-only mode, which also notes that a `SELECT` can call a function that writes; ours has no `SECURITY DEFINER` function in `public`, and a test keeps it so.

## Rejected
- **Supabase's built-in read-only mode** (`read_only=true` on its tool server): it runs as `supabase_read_only_user`, which has `pg_read_all_data`, so it reads the encrypted logins too, and it needs an account-wide access token. Ours reads only what we choose.
- **Giving an assistant the owner's URL** and trusting it not to write: an instruction is not a permission.
- **`BYPASSRLS` on the login**, instead of per-table policies: simpler, but a table added later would be readable the moment it exists, including a private one. Policies make "hidden until listed" the default.
- **psql as the reader:** its backslash commands run shell commands, and it isn't on the laptop's path.
- **A grant on a list of table names in the setup script:** the list would rot. The file grants every table it finds except the ones it keeps out, and a test checks the result.

## Consequences
- **After a migration adds a table, run `scripts/readonly-setup.sh` again** if the login should see it. The runbook says so; forgetting means the login can't see the table, never that it sees too much.
- **The login sees users' emails and league names** (tables `users` and `leagues`). Fine for one user; before friends join (M7), decide whether those tables stay readable or move to `kept_out`.
- **Production data leaves the database only through a person's own command**, run from a laptop that holds no database secret: the URL is fetched, used by one process and gone.
