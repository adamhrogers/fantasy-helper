-- A database login that can only look (ADR 0032). Run by scripts/readonly-setup.sh, as the
-- database's owner, with the password given as the psql variable :pw. Safe to run again: it
-- rotates the password and re-grants, so run it again after a migration adds a table.
--
-- What the login can do: read every table in `public` except the ones listed under "kept out"
-- below. What it can't do: write, delete, truncate, create anything, or read the encrypted
-- league logins. It's a plain role: not a superuser, no bypass of row-level security, no member
-- of any role, and three connections at most.
--
-- Every table has row-level security on and no policies (CLAUDE.md), so the owner is the only
-- one who sees rows. The login gets one SELECT-only policy per table it may read. A table that
-- doesn't exist yet gets nothing until this file runs again: new tables start out hidden.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fh_readonly') THEN
    CREATE ROLE fh_readonly NOLOGIN;
  END IF;
END
$$;

ALTER ROLE fh_readonly WITH LOGIN PASSWORD :'pw' CONNECTION LIMIT 3;

-- Belt and braces, not the lock: the lock is that the role has no write privilege at all (a
-- session may switch this setting off). The timeout keeps one careless query from running away.
ALTER ROLE fh_readonly SET default_transaction_read_only = on;
ALTER ROLE fh_readonly SET statement_timeout = '30s';
ALTER ROLE fh_readonly SET idle_in_transaction_session_timeout = '60s';

GRANT USAGE ON SCHEMA public TO fh_readonly;

DO $$
DECLARE
  -- Kept out: the encrypted ESPN logins, and (once part 3 adds it) each phone's push address
  -- and keys. Add a table here before it holds anything private.
  kept_out CONSTANT text[] := ARRAY['platform_credentials', 'push_subscriptions'];
  t record;
BEGIN
  -- Lock every table first, before changing any grant. A grant row-locks the table's catalog
  -- entry, and autovacuum's analyze (which holds a lighter lock on the table and then waits
  -- for that catalog row) would otherwise wait on this transaction while this transaction
  -- waits on it for the table lock: a deadlock. Taking the table locks first means the
  -- lock-holder is only waited on, never waiting on us (found in CI, 2026-11-16).
  FOR t IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
  LOOP
    EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', t.relname);
  END LOOP;
  REVOKE ALL ON ALL TABLES IN SCHEMA public FROM fh_readonly;
  REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM fh_readonly;
  FOR t IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS fh_readonly_select ON public.%I', t.relname);
    CONTINUE WHEN t.relname = ANY (kept_out);
    EXECUTE format('GRANT SELECT ON public.%I TO fh_readonly', t.relname);
    EXECUTE format(
      'CREATE POLICY fh_readonly_select ON public.%I FOR SELECT TO fh_readonly USING (true)',
      t.relname
    );
  END LOOP;
END
$$;
