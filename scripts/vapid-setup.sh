#!/usr/bin/env bash
# Makes the Web Push key pair and saves it in Secret Manager (ADR 0030,
# docs/runbooks/phone-app.md Part 3). Run it yourself, in your own Terminal, once:
#
#   scripts/vapid-setup.sh            # the first time
#   scripts/vapid-setup.sh --rotate   # replace the pair (rarely: see below)
#
# The pair is made inside one Python process and goes straight to Google: the private key to
# fh-vapid-private-key (only the report jobs can read it) and the public key to
# fh-vapid-public-key (the API serves it to the app). The private key is never printed, never in a
# command line (so not in `ps` either), never in a shell variable and never on disk. The only thing
# printed is the public key, which the app hands to every browser anyway.
#
# It refuses to run twice. A new pair breaks every device that has turned notifications on (its
# subscription is tied to the old public key), and each one would have to turn them off and on again;
# --rotate says so and waits for you to type "rotate". Run `scripts/gcp-setup.sh infra` first: it
# creates the two empty secrets and says who may read each.
set -euo pipefail

PRIVATE=fh-vapid-private-key
PUBLIC=fh-vapid-public-key
cd "$(dirname "${BASH_SOURCE[0]}")/.."

rotate=0
case "${1:-}" in
  "") ;;
  --rotate) rotate=1 ;;
  *) echo "usage: scripts/vapid-setup.sh [--rotate]" >&2; exit 2 ;;
esac

command -v gcloud >/dev/null || { echo "gcloud isn't on PATH (setup section 5)" >&2; exit 1; }
command -v uv >/dev/null || { echo "uv isn't on PATH (export PATH=\"\$HOME/.local/bin:\$PATH\")" >&2; exit 1; }

for secret in "$PRIVATE" "$PUBLIC"; do
  gcloud secrets describe "$secret" >/dev/null 2>&1 || {
    echo "✗ $secret doesn't exist yet: run scripts/gcp-setup.sh infra first (from this PR's branch)." >&2
    exit 1
  }
done
enabled() { gcloud secrets versions list "$1" --filter 'state=ENABLED' --format 'value(name.basename())'; }
previous_private="$(enabled "$PRIVATE")"
previous_public="$(enabled "$PUBLIC")"

if [[ -n "$previous_private" && $rotate -eq 0 ]]; then
  echo "✗ $PRIVATE already has a key. Making a new pair would break every device that has turned" >&2
  echo "  notifications on: each would have to turn them off and on again. Nothing was changed." >&2
  echo "  If you really want a new pair: scripts/vapid-setup.sh --rotate" >&2
  exit 1
fi
if [[ -n "$previous_private" ]]; then
  echo "Rotating replaces the pair. Every device that has notifications on stops getting them until"
  echo "its app is opened and notifications are turned off and on again."
  read -rp 'Type "rotate" to go on: ' answer
  [[ "$answer" == rotate ]] || { echo "Nothing was changed." >&2; exit 1; }
fi

# One process makes the pair and hands each half to gcloud on its standard input. The public key
# goes first: if the private one then fails, the run is safe to repeat (nothing signs with the
# public one, and the refusal above looks at the private one).
public_key="$(uv run python -I - "$PRIVATE" "$PUBLIC" <<'PY'
import subprocess
import sys

from fantasy_helper.delivery.push import generate_vapid_keys

private_name, public_name = sys.argv[1:3]
keys = generate_vapid_keys()  # checked: the push library reads this key the way the jobs will


def add(name: str, value: str) -> None:
    done = subprocess.run(  # noqa: S603, S607 (gcloud, fixed arguments; the value is on stdin)
        ["gcloud", "secrets", "versions", "add", name, "--data-file=-"],
        input=value.encode(),
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if done.returncode != 0:
        sys.exit(f"couldn't save {name} (gcloud said so above)")


add(public_name, keys.public_key)
add(private_name, keys.private_key)
print(keys.public_key)
PY
)"

for version in $previous_private; do
  gcloud secrets versions disable "$version" --secret "$PRIVATE" --quiet >/dev/null
done
for version in $previous_public; do
  gcloud secrets versions disable "$version" --secret "$PUBLIC" --quiet >/dev/null
done

echo "  ✓ $PRIVATE and $PUBLIC saved${previous_private:+ (older versions disabled)}"
echo "  public key: $public_key"
echo
echo "Next: merge the PR (or, if it is already merged, Actions -> Deploy -> Run workflow) so the"
echo "report jobs and the API read the new values; they only read a secret when they start."
