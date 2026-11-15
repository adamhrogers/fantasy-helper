#!/usr/bin/env bash
# Runs one `fh` command against the PRODUCTION database, from this laptop (ADR 0027):
#
#   scripts/fh-prod.sh league add espn 1234567     # add a league (ESPN cookies at a hidden prompt)
#   scripts/fh-prod.sh league list
#   scripts/fh-prod.sh report open                 # this morning's report, built in the cloud
#   scripts/fh-prod.sh doctor
#
# The database URL and the key that decrypts league logins are read from Google Secret Manager
# into this one process. Nothing is written to disk, there's no prod .env on the laptop, and the
# laptop's own .env keeps pointing at dev for everything else.
#
# Only commands that read or edit your own data run from here. The runs and the migration are Cloud
# Run jobs (docs/runbooks/prod-database.md): a daily run started from a laptop at 2 p.m. would log
# calls against games already played.
set -euo pipefail

case "${1:-}" in
  league|report|doctor) ;;
  *)
    echo "usage: scripts/fh-prod.sh league|report|doctor ..." >&2
    echo "Other commands don't run against prod from here: see docs/runbooks/prod-database.md." >&2
    exit 2
    ;;
esac

command -v gcloud >/dev/null || { echo "gcloud isn't on PATH (setup section 5)" >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/.."

secret() { gcloud secrets versions access latest --secret "$1"; }
DATABASE_URL="$(secret fh-database-url)"
FH_ENCRYPTION_KEY="$(secret fh-encryption-key)"
FH_NTFY_TOPIC="$(secret fh-ntfy-topic)"
export DATABASE_URL FH_ENCRYPTION_KEY FH_NTFY_TOPIC
export FH_ENV=prod

echo "-> PRODUCTION database" >&2
exec uv run fh "$@"
