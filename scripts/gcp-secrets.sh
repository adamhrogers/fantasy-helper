#!/usr/bin/env bash
# Puts the scheduled jobs' secrets into Secret Manager (ADR 0025). Run it yourself, in your own
# Terminal: each value is typed or pasted at a hidden prompt and goes straight to Google. Nothing
# is printed, logged or saved on the laptop. Press Enter at a prompt to leave that secret as is.
#
#   scripts/gcp-secrets.sh            # all five
#   scripts/gcp-secrets.sh fh-ntfy-topic   # just one (to rotate it)
#
# Values come from Bitwarden. The R2 values are the Cloud Run token's, its own token, not the
# laptop's or GitHub's (docs/runbooks/scheduled-jobs.md).
set -euo pipefail

# macOS ships bash 3.2, so no associative arrays here.
NAMES=(fh-r2-endpoint fh-r2-access-key-id fh-r2-secret-access-key fh-ntfy-topic fh-healthchecks-ping-key)
prompt_for() {
  case "$1" in
    fh-r2-endpoint) echo "R2 endpoint (https://<account id>.r2.cloudflarestorage.com, nothing after .com)" ;;
    fh-r2-access-key-id) echo "Cloud Run's R2 token: access key ID" ;;
    fh-r2-secret-access-key) echo "Cloud Run's R2 token: secret access key" ;;
    fh-ntfy-topic) echo "The prod ntfy topic" ;;
    fh-healthchecks-ping-key) echo "The Healthchecks project's ping key" ;;
    *) return 1 ;;
  esac
}

[[ $# -eq 0 ]] || NAMES=("$@")
for name in "${NAMES[@]}"; do
  prompt="$(prompt_for "$name")" || { echo "unknown secret: $name" >&2; exit 2; }
  read -rsp "$prompt: " value
  echo
  if [[ -z "$value" ]]; then
    echo "  - $name left as is"
    continue
  fi
  if [[ "$name" == fh-r2-endpoint && "$value" =~ \.com/.+ ]]; then
    echo "  ✗ $name: remove everything after .com (the bucket name doesn't belong there)" >&2
    unset value
    exit 1
  fi
  printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  unset value
  echo "  ✓ $name: new version saved"
done
echo "Done. Check (versions only, never values): scripts/gcp-setup.sh show"
