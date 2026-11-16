#!/usr/bin/env bash
# Proves the phone app's API works end to end, from your own Terminal (ADR 0029,
# docs/runbooks/phone-app.md). It signs in through Supabase the way the app will, calls each route
# and prints statuses and counts only: never a token, a code, a report's text or a league's name.
#
#   scripts/api-smoke.sh https://<the api's address>
#
# You are asked for three things: your sign-in email, Supabase's publishable key (the
# sb_publishable_... one; the app ships it, but it stays out of the repo, so it comes from
# Bitwarden at a hidden prompt) and, partway through, the 6-digit code Supabase emails you (hidden
# too). Nothing is written to disk: the token lives in this script's memory, reaches curl through
# a pipe and never appears in a command line.
#
# Exits 1 if anything is wrong. What "right" is: 200s with your token, 401 without one, and
# "sign-ups refused" for an email that isn't yours.
set -euo pipefail

[[ $# -eq 1 && "$1" == https://* ]] || { echo "usage: scripts/api-smoke.sh https://<the api's address>" >&2; exit 2; }
API="${1%/}"
command -v python3 >/dev/null || { echo "python3 is needed to read the answers (it ships with macOS's developer tools)" >&2; exit 1; }
SUPABASE_URL="$(gcloud secrets versions access latest --secret fh-supabase-url)"
SUPABASE_URL="${SUPABASE_URL%/}"

failures=0
CODE=""
BODY=""
# http METHOD URL [curl arguments...]: sets CODE (the status) and BODY (the answer). Never prints.
http() {
  local method="$1" url="$2" out
  shift 2
  out="$(curl -sS --max-time 30 -w $'\n%{http_code}' -X "$method" "$url" "$@" || printf '\n000')"
  CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
}
# expect LABEL WANTED...: one line, ✓ or ✗, against the status the last call returned.
expect() {
  local label="$1" want
  shift
  for want in "$@"; do
    if [[ "$CODE" == "$want" ]]; then
      echo "  ✓ $label -> $CODE"
      return 0
    fi
  done
  echo "  ✗ $label -> $CODE (wanted $*)" >&2
  failures=$((failures + 1))
  return 1
}
# field PYTHON-EXPRESSION: the answer's JSON, one value of it, as text.
field() {
  printf '%s' "$BODY" | python3 -c "import sys, json; d = json.load(sys.stdin); print($1)" 2>/dev/null || echo "?"
}
JSON=(-H 'Content-Type: application/json')

read -rp "Your sign-in email: " email
read -rsp "Supabase's publishable key (sb_publishable_..., from Bitwarden): " key
echo
KEY=(-H "apikey: $key")

echo "The API"
http GET "$API/healthz"
expect "GET /healthz" 200 || true
http GET "$API/v1/me"
expect "GET /v1/me with no token is refused" 401 || true
http GET "$API/v1/me" -H "Authorization: Bearer not-a-token"
expect "GET /v1/me with a made-up token is refused" 401 || true

echo "Sign-ups are off"
stranger="smoke-$RANDOM$RANDOM@example.invalid"
http POST "$SUPABASE_URL/auth/v1/otp" "${KEY[@]}" "${JSON[@]}" \
  -d "{\"email\": \"$stranger\", \"create_user\": false}"
if [[ "$CODE" == 4* ]]; then
  echo "  ✓ an unknown email is refused -> $CODE (sign-ups refused)"
else
  echo "  ✗ an unknown email got $CODE: sign-ups may be ON (Supabase: Authentication, Sign In / Providers)" >&2
  failures=$((failures + 1))
fi

echo "Your sign-in"
http POST "$SUPABASE_URL/auth/v1/otp" "${KEY[@]}" "${JSON[@]}" \
  -d "$(E="$email" python3 -c 'import json, os; print(json.dumps({"email": os.environ["E"], "create_user": False}))')"
if expect "a code is sent to you" 200; then
  read -rsp "The 6-digit code from the email: " code
  echo
  http POST "$SUPABASE_URL/auth/v1/verify" "${KEY[@]}" "${JSON[@]}" --data-binary @<(
    E="$email" T="$code" python3 -c 'import json, os; print(json.dumps({"type": "email", "email": os.environ["E"], "token": os.environ["T"]}))'
  )
  unset code
  expect "the code is accepted" 200 || true
  TOKEN="$(field 'd["access_token"]')"
  if [[ -n "$TOKEN" && "$TOKEN" != "?" ]]; then
    auth() { # auth PATH: a GET with your token, passed through a pipe so no command line holds it
      http GET "$API$1" -H @<(printf 'Authorization: Bearer %s\n' "$TOKEN")
    }
    echo "With your token"
    auth /v1/me
    if expect "GET /v1/me" 200; then echo "      leagues: $(field 'len(d["leagues"])')"; fi
    for kind in daily weekly; do
      auth "/v1/reports/$kind"
      if expect "GET /v1/reports/$kind" 200; then
        echo "      date: $(field 'd["for_date"]'), reports: $(field 'len(d["reports"])')"
      fi
    done
    auth /v1/push/public-key
    expect "GET /v1/push/public-key (503 until push is set up)" 200 503 || true
  else
    echo "  ✗ no token came back, so the signed-in calls were skipped" >&2
    failures=$((failures + 1))
  fi
else
  echo "  ✗ skipped the signed-in calls: no code was sent (is $email set up under Authentication, Users?)" >&2
fi
unset key

echo
if [[ "$failures" -eq 0 ]]; then
  echo "All checks passed."
else
  echo "$failures check(s) failed." >&2
  exit 1
fi
