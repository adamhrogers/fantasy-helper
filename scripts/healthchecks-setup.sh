#!/usr/bin/env bash
# Sets every scheduled job's Healthchecks check: its cron line, time zone and grace (ADR 0025).
# The table is scripts/jobs.conf, the same one Cloud Scheduler's entries come from.
#
# Why a script and not the website: on 2026-11-14 the schedules were typed into the site and saved
# in UTC, its default, so the evening run looked six hours early and raised a false "down" alert.
# This sets the time zone every time, and is safe to run again after a schedule changes.
#
# For a chained job (daily, weekly) the cron time is when the CHAIN starts and the grace runs to the
# alert deadline: see the header of scripts/jobs.conf for why.
#
#   scripts/healthchecks-setup.sh
#
# It asks for the project's API key at a hidden prompt (Healthchecks -> your project -> Settings ->
# API Access -> create a read-write key; save it in Bitwarden). The key goes to curl on stdin, so it
# never appears in a command line or `ps`, and it isn't stored.
set -euo pipefail

TIME_ZONE=America/New_York
API="${FH_HEALTHCHECKS_API:-https://healthchecks.io/api/v3/checks/}"
CONF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.conf"

read -rsp "Healthchecks API key (read-write): " key
echo
[[ -n "$key" ]] || { echo "no key given" >&2; exit 1; }

out="$(mktemp)"
trap 'rm -f "$out"' EXIT
status=0
while IFS='|' read -r job cron grace _trigger; do
  case "$job" in ''|'#'*) continue ;; esac
  # `unique: slug` matches the check the job's first ping created (the ping URL's slug), updates
  # it in place, and creates it if no ping has come yet. `channels: *` keeps every integration
  # attached, so a check created here can alert too.
  body="$(printf '{"name":"%s","slug":"%s","schedule":"%s","tz":"%s","grace":%s,"channels":"*","unique":["slug"]}' \
    "$job" "$job" "$cron" "$TIME_ZONE" "$grace")"
  code="$(printf 'header = "X-Api-Key: %s"\n' "$key" |
    curl -sS -K - -o "$out" -w '%{http_code}' --data "$body" "$API")" || code=000
  case "$code" in
    200|201)
      next="$(grep -o '"next_ping": *"[^"]*"' "$out" | cut -d'"' -f4 || true)"
      verb=updated; [[ "$code" == 201 ]] && verb=created
      echo "  ✓ $job: '$cron' $TIME_ZONE, grace ${grace}s ($verb${next:+; next ping due $next})"
      ;;
    *)
      echo "  ✗ $job: Healthchecks answered HTTP $code" >&2
      cat "$out" >&2
      echo >&2
      status=1
      ;;
  esac
done <"$CONF"
unset key
exit "$status"
