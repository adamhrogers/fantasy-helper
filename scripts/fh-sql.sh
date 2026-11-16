#!/usr/bin/env bash
# Looks at the PRODUCTION database through the read-only login (ADR 0032), from this laptop:
#
#   scripts/fh-sql.sh "select count(*) from players"
#   scripts/fh-sql.sh --limit 20 "select * from runs order by started_at desc"
#
# The login's URL is read from Google Secret Manager into this one process. Nothing is written to
# disk, and this script never fetches the owner's URL or the encryption key: the login can read
# the data tables and nothing else, so a query here can't change or delete anything, and can't
# reach the encrypted league logins. `fh sql` also refuses a login that could write.
# Set it up once with scripts/readonly-setup.sh (docs/runbooks/read-only-access.md).
set -euo pipefail

[[ $# -gt 0 ]] || { echo 'usage: scripts/fh-sql.sh [--limit N] [--wide] "select ..."' >&2; exit 2; }
command -v gcloud >/dev/null || { echo "gcloud isn't on PATH (setup section 5)" >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FH_READONLY_DATABASE_URL="$(gcloud secrets versions access latest --secret fh-readonly-database-url)"
export FH_READONLY_DATABASE_URL

echo "-> PRODUCTION database (read-only login)" >&2
exec uv run fh sql "$@"
