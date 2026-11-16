#!/usr/bin/env bash
# Creates (or refreshes) the database login that can only look, and saves its URL in Secret
# Manager as fh-readonly-database-url (ADR 0032, docs/runbooks/read-only-access.md). Run it
# yourself, in your own Terminal:
#
#   scripts/readonly-setup.sh
#
# It reads the owner's URL from Secret Manager, makes a fresh random password, runs
# scripts/readonly-login.sql as the owner, then proves the new login from the other side: it can
# read the data, and it can neither create a table nor read the encrypted league logins. Only
# then is the new URL saved, and older versions are disabled. No password is printed, put in a
# command line or written to disk. Run it again after a migration adds a table (a new table stays
# hidden from the login until then); each run rotates the password.
set -euo pipefail

SECRET=fh-readonly-database-url
cd "$(dirname "${BASH_SOURCE[0]}")/.."

command -v gcloud >/dev/null || { echo "gcloud isn't on PATH (setup section 5)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 isn't on PATH" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl isn't on PATH" >&2; exit 1; }
PSQL="$(command -v psql || true)"
for candidate in /usr/local/opt/postgresql@17/bin/psql /opt/homebrew/opt/postgresql@17/bin/psql; do
  [[ -n "$PSQL" || ! -x "$candidate" ]] || PSQL="$candidate"
done
[[ -n "$PSQL" ]] || { echo "psql isn't installed (brew install postgresql@17, or any psql)" >&2; exit 1; }

# The other login's URL: the same server and database, only the user and password swapped. On
# Supabase's pooler the user is <role>.<project>, so postgres.<project> becomes fh_readonly.<project>.
login_url_for() { # $1 = the owner's URL, $2 = the new password
  URL="$1" PW="$2" python3 - <<'PY'
import os
from urllib.parse import quote, unquote, urlsplit, urlunsplit

u = urlsplit(os.environ["URL"])
user = unquote(u.username or "")
user = "fh_readonly" + (user[user.index("."):] if "." in user else "")
netloc = f"{quote(user, safe='')}:{quote(os.environ['PW'], safe='')}@{u.hostname}"
print(urlunsplit((u.scheme, netloc + (f":{u.port}" if u.port else ""), u.path, u.query, "")), end="")
PY
}

# A URL's parts as psql's environment variables, which a process list doesn't show.
psql_env_for() { # $1 = a postgresql:// URL
  URL="$1" python3 - <<'PY'
import os, shlex
from urllib.parse import unquote, urlsplit

u = urlsplit(os.environ["URL"])
pairs = {"PGHOST": u.hostname or "", "PGPORT": str(u.port or 5432),
         "PGUSER": unquote(u.username or ""), "PGPASSWORD": unquote(u.password or ""),
         "PGDATABASE": u.path.lstrip("/") or "postgres"}
print("\n".join(f"export {k}={shlex.quote(v)}" for k, v in pairs.items()))
PY
}

owner_url="$(gcloud secrets versions access latest --secret fh-database-url)"
password="$(openssl rand -hex 24)"
login_url="$(login_url_for "$owner_url" "$password")"
owner_env="$(psql_env_for "$owner_url")"
login_env="$(psql_env_for "$login_url")"
unset owner_url

echo "-> creating the login (PRODUCTION database)" >&2
# psql reads the setup file from stdin, after the password is set as a variable; an error
# message that quotes the statement can't show the password.
{ printf '%s\n' "\\set pw '$password'"; cat scripts/readonly-login.sql; } \
  | ( eval "$owner_env"; "$PSQL" -X -q -v ON_ERROR_STOP=1 >/dev/null ) \
    2> >(sed "s/$password/****/g" >&2)
unset owner_env

probe() { ( eval "$login_env"; "$PSQL" -X -q -v ON_ERROR_STOP=1 -c "$1" >/dev/null 2>&1 ); }
echo "-> checking it from the other side" >&2
probe "select count(*) from users" || { echo "  ✗ the new login can't read the data" >&2; exit 1; }
if probe "create table zz_probe (a int)"; then
  echo "  ✗ the new login can CREATE a table: not saved. Tell Claude." >&2; exit 1
fi
if probe "select 1 from platform_credentials limit 1"; then
  echo "  ✗ the new login can read the encrypted league logins: not saved. Tell Claude." >&2; exit 1
fi

if ! gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --replication-policy automatic >/dev/null
fi
previous="$(gcloud secrets versions list "$SECRET" --filter 'state=ENABLED' --format 'value(name.basename())')"
printf '%s' "$login_url" | gcloud secrets versions add "$SECRET" --data-file=- >/dev/null
for version in $previous; do
  gcloud secrets versions disable "$version" --secret "$SECRET" --quiet >/dev/null
done
unset login_url login_env password
echo "  ✓ $SECRET: new version saved${previous:+, older version(s) disabled}"
echo "Try it: scripts/fh-sql.sh \"select count(*) from players\""
