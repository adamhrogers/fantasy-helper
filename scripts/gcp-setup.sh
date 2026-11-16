#!/usr/bin/env bash
# Google Cloud for the jobs (ADR 0025, 0026, 0027). Safe to re-run: each step checks before it
# creates, and IAM bindings are idempotent. Needs gcloud signed in as the project owner and gh.
#
#   scripts/gcp-setup.sh infra      # APIs, image repository, service accounts, empty secrets,
#                                   # keyless GitHub deploys, the jobs and the API service
#                                   # (placeholder images), public access to the API, and the
#                                   # prod environment's variables
#   scripts/gcp-setup.sh schedule   # Cloud Scheduler entries, after the first deploy
#   scripts/gcp-setup.sh show       # what exists, no changes
#
# Secret values are added separately, by Adam, with scripts/gcp-secrets.sh.
set -euo pipefail

REGION=us-east4                       # the jobs, the image repository
SCHEDULER_LOCATION=us-east4           # Cloud Scheduler; checked against its locations below
REPO=fantasy-helper                   # Artifact Registry repository
GITHUB_REPO=adamhrogers/fantasy-helper
TIME_ZONE=America/New_York
# The jobs, from the table Healthchecks' schedules come from too (scripts/jobs.conf). JOBS has every
# job; SCHEDULED has the ones Cloud Scheduler starts, as "job:cron" (Eastern time; the scheduler
# handles daylight saving itself); CHAINED has the ones another job starts, as "job:upstream"
# (ADR 0028: the morning snapshot starts the daily run, which starts the weekly one).
JOBS=()
SCHEDULED=()
CHAINED=()
while IFS='|' read -r job cron _grace trigger; do
  case "$job" in ''|'#'*) continue ;; esac
  JOBS+=("$job")
  case "$trigger" in
    scheduler) SCHEDULED+=("$job:$cron") ;;
    "after "*) CHAINED+=("$job:${trigger#after }") ;;
    *) echo "jobs.conf: $job has trigger '$trigger'" >&2; exit 1 ;;
  esac
done <"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jobs.conf"
# Jobs nothing schedules: the deploy runs `migrate` itself, before the jobs that need new tables.
ONE_OFF_JOBS=(migrate)
# The phone app's API is a service, not a job (ADR 0029).
API_SERVICE=api
# The secrets the jobs read, mapped to settings in .github/workflows/deploy.yml. The snapshot
# jobs get the first group only: they fetch public data and never see the database or the key
# that decrypts league logins. The report jobs and the migration get both.
SNAPSHOT_SECRETS=(fh-r2-endpoint fh-r2-access-key-id fh-r2-secret-access-key fh-ntfy-topic fh-healthchecks-ping-key)
DATABASE_SECRETS=(fh-database-url fh-encryption-key)
# The API gets its own three and nothing else: its database URL (Supabase's transaction pooler, a
# different string from the jobs'), the Supabase project it trusts sign-ins from, and the app's
# addresses. No encryption key, no R2, no ping key, so it can never decrypt a league login.
API_SECRETS=(fh-api-database-url fh-supabase-url fh-app-origins)
SECRETS=("${SNAPSHOT_SECRETS[@]}" "${DATABASE_SECRETS[@]}" "${API_SECRETS[@]}")
# Public sample images, only until the first deploy replaces them.
PLACEHOLDER=us-docker.pkg.dev/cloudrun/container/job:latest
PLACEHOLDER_SERVICE=us-docker.pkg.dev/cloudrun/container/hello:latest

PROJECT="$(gcloud config get-value project 2>/dev/null)"
[[ -n "$PROJECT" ]] || { echo "no gcloud project set (gcloud config set project ...)" >&2; exit 1; }
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
SA_JOBS="fh-jobs@$PROJECT.iam.gserviceaccount.com"          # the snapshot jobs
SA_REPORTS="fh-reports@$PROJECT.iam.gserviceaccount.com"    # the report jobs and the migration
SA_API="fh-api@$PROJECT.iam.gserviceaccount.com"            # the phone app's API
SA_SCHEDULER="fh-scheduler@$PROJECT.iam.gserviceaccount.com"
SA_DEPLOYER="fh-deployer@$PROJECT.iam.gserviceaccount.com"
POOL=github
PROVIDER=github-actions

step() { printf '\n== %s\n' "$*"; }
quiet() { "$@" >/dev/null; }  # IAM commands print whole policies

# Which account runs a job: the snapshot jobs get the one with no database access.
account_for() {
  case "$1" in
    snapshot-*) echo "$SA_JOBS" ;;
    *) echo "$SA_REPORTS" ;;
  esac
}

infra() {
  step "APIs"
  gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com iamcredentials.googleapis.com iam.googleapis.com \
    sts.googleapis.com cloudscheduler.googleapis.com

  step "Image repository $REPO (immutable tags, keeps the 5 newest images)"
  if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
    gcloud artifacts repositories create "$REPO" --location "$REGION" \
      --repository-format docker --immutable-tags \
      --description "Images for the scheduled jobs (ADR 0025)"
  fi
  policy="$(mktemp)"
  cat >"$policy" <<'JSON'
[
  {"name": "keep-newest-5", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-the-rest", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}
]
JSON
  quiet gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
    --policy "$policy" --no-dry-run
  rm -f "$policy"

  step "Service accounts"
  for sa in fh-jobs:"Runs the snapshot jobs (public data only)" \
            fh-reports:"Runs the report jobs and the migration (database access)" \
            fh-api:"Runs the phone app's API (its own database URL, no encryption key)" \
            fh-scheduler:"Starts the scheduled jobs" fh-deployer:"GitHub Actions deploys"; do
    name="${sa%%:*}"
    gcloud iam service-accounts describe "$name@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
      gcloud iam service-accounts create "$name" --display-name "${sa#*:}"
  done

  step "Secrets (empty until scripts/gcp-secrets.sh): each readable only by the jobs that need it"
  for secret in "${SECRETS[@]}"; do
    gcloud secrets describe "$secret" >/dev/null 2>&1 ||
      gcloud secrets create "$secret" --replication-policy automatic
  done
  for secret in "${SNAPSHOT_SECRETS[@]}" "${DATABASE_SECRETS[@]}"; do
    quiet gcloud secrets add-iam-policy-binding "$secret" \
      --member "serviceAccount:$SA_REPORTS" --role roles/secretmanager.secretAccessor
  done
  for secret in "${SNAPSHOT_SECRETS[@]}"; do
    quiet gcloud secrets add-iam-policy-binding "$secret" \
      --member "serviceAccount:$SA_JOBS" --role roles/secretmanager.secretAccessor
  done
  for secret in "${API_SECRETS[@]}"; do
    quiet gcloud secrets add-iam-policy-binding "$secret" \
      --member "serviceAccount:$SA_API" --role roles/secretmanager.secretAccessor
  done

  step "Keyless deploys: only deploy.yml on $GITHUB_REPO's main branch can act as fh-deployer"
  repo_id="$(gh api "repos/$GITHUB_REPO" --jq .id)"
  gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1 ||
    gcloud iam workload-identity-pools create "$POOL" --location global \
      --display-name "GitHub Actions"
  # The workflow file too, not just the branch: another workflow on main that asked for an
  # id-token must not be able to deploy (or reach the jobs' secrets through a deploy).
  workflow="$GITHUB_REPO/.github/workflows/deploy.yml@refs/heads/main"
  condition="assertion.repository_id == '$repo_id' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == '$workflow'"
  mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref"
  if gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global \
      --workload-identity-pool "$POOL" >/dev/null 2>&1; then
    gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --location global \
      --workload-identity-pool "$POOL" --attribute-mapping "$mapping" \
      --attribute-condition "$condition"
  else
    gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global \
      --workload-identity-pool "$POOL" --display-name "This repo, main only" \
      --issuer-uri https://token.actions.githubusercontent.com \
      --attribute-mapping "$mapping" --attribute-condition "$condition"
  fi
  principals="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository_id/$repo_id"
  quiet gcloud iam service-accounts add-iam-policy-binding "$SA_DEPLOYER" \
    --member "$principals" --role roles/iam.workloadIdentityUser

  step "fh-deployer: push images to $REPO, and run the jobs and the API as fh-jobs, fh-reports and fh-api"
  quiet gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
    --member "serviceAccount:$SA_DEPLOYER" --role roles/artifactregistry.writer
  for account in "$SA_JOBS" "$SA_REPORTS" "$SA_API"; do
    quiet gcloud iam service-accounts add-iam-policy-binding "$account" \
      --member "serviceAccount:$SA_DEPLOYER" --role roles/iam.serviceAccountUser
  done

  # Job-level, so fh-deployer can't touch anything else in the project. If the first deploy
  # fails with PERMISSION_DENIED on a project-level permission, see scheduled-jobs.md.
  step "Jobs (placeholder image until the first deploy); fh-deployer may update only these"
  for job in "${JOBS[@]}" "${ONE_OFF_JOBS[@]}"; do
    gcloud run jobs describe "$job" --region "$REGION" >/dev/null 2>&1 ||
      gcloud run jobs create "$job" --region "$REGION" --image "$PLACEHOLDER" \
        --service-account "$(account_for "$job")" --tasks 1 --max-retries 0
    quiet gcloud run jobs add-iam-policy-binding "$job" --region "$REGION" \
      --member "serviceAccount:$SA_DEPLOYER" --role roles/run.developer
  done

  step "The API service $API_SERVICE (placeholder image); fh-deployer may update only this service"
  gcloud run services describe "$API_SERVICE" --region "$REGION" >/dev/null 2>&1 ||
    gcloud run deploy "$API_SERVICE" --region "$REGION" --image "$PLACEHOLDER_SERVICE" \
      --service-account "$SA_API" --min-instances 0 --max-instances 2 --quiet
  quiet gcloud run services add-iam-policy-binding "$API_SERVICE" --region "$REGION" \
    --member "serviceAccount:$SA_DEPLOYER" --role roles/run.developer
  # Public at the platform, private in the app: the phone can't present Google credentials, so
  # Cloud Run lets anyone reach the service and the app checks every token itself (ADR 0029).
  # Only this owner script can grant it; the deployer can't change IAM policy. If an organization
  # policy blocks `allUsers`, this fails and the ADR's fallback is needed.
  quiet gcloud run services add-iam-policy-binding "$API_SERVICE" --region "$REGION" \
    --member allUsers --role roles/run.invoker

  step "The chain: each job may start the next one, and only that one"
  for entry in "${CHAINED[@]}"; do
    job="${entry%%:*}" upstream="${entry#*:}"
    quiet gcloud run jobs add-iam-policy-binding "$job" --region "$REGION" \
      --member "serviceAccount:$(account_for "$upstream")" --role roles/run.invoker
    echo "$upstream -> $job"
  done

  step "The prod environment's variables (IDs, not secrets)"
  gh variable set GCP_PROJECT --env prod --repo "$GITHUB_REPO" --body "$PROJECT"
  gh variable set GCP_DEPLOYER_SA --env prod --repo "$GITHUB_REPO" --body "$SA_DEPLOYER"
  gh variable set GCP_WIF_PROVIDER --env prod --repo "$GITHUB_REPO" \
    --body "projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"

  echo
  echo "Done. Next: Adam runs scripts/gcp-secrets.sh, then Actions -> Deploy -> Run workflow."
}

schedule() {
  if ! gcloud scheduler locations list --format 'value(locationId)' | grep -qx "$SCHEDULER_LOCATION"; then
    echo "Cloud Scheduler isn't offered in $SCHEDULER_LOCATION: set SCHEDULER_LOCATION to one of:" >&2
    gcloud scheduler locations list --format 'value(locationId)' >&2
    exit 1
  fi
  ours="$REGION-docker.pkg.dev/$PROJECT/$REPO/app:"
  for entry in "${SCHEDULED[@]}"; do
    job="${entry%%:*}" cron="${entry#*:}"
    step "$job at '$cron' $TIME_ZONE"
    # Fail closed: schedule only a job that runs our own image, never the placeholder, and not
    # when the image can't be read at all.
    image="$(gcloud run jobs describe "$job" --region "$REGION" \
      --format 'value(spec.template.spec.template.spec.containers[0].image)')"
    if [[ "$image" != "$ours"* ]]; then
      echo "$job runs '${image:-an unreadable image}', not $ours<commit>: deploy first (Actions -> Deploy)" >&2
      exit 1
    fi
    quiet gcloud run jobs add-iam-policy-binding "$job" --region "$REGION" \
      --member "serviceAccount:$SA_SCHEDULER" --role roles/run.invoker
    args=(--location "$SCHEDULER_LOCATION" --schedule "$cron" --time-zone "$TIME_ZONE"
      --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$job:run"
      --http-method POST --oauth-service-account-email "$SA_SCHEDULER"
      --max-retry-attempts 3 --min-backoff 60s)
    if gcloud scheduler jobs describe "$job" --location "$SCHEDULER_LOCATION" >/dev/null 2>&1; then
      gcloud scheduler jobs update http "$job" "${args[@]}"
    else
      gcloud scheduler jobs create http "$job" "${args[@]}" \
        --description "Starts the Cloud Run job $job (ADR 0025)"
    fi
  done

  # A chained job starts only when the one before it finishes. A schedule of its own would start it
  # whether or not the snapshot was complete, so remove any that exists.
  for entry in "${CHAINED[@]}"; do
    job="${entry%%:*}"
    if gcloud scheduler jobs describe "$job" --location "$SCHEDULER_LOCATION" >/dev/null 2>&1; then
      step "$job is chained: removing its own schedule"
      gcloud scheduler jobs delete "$job" --location "$SCHEDULER_LOCATION" --quiet
    fi
  done
}

show() {
  step "Jobs";      gcloud run jobs list --region "$REGION"
  step "The API";   gcloud run services list --region "$REGION" ||:
  step "The chain"; printf '%s\n' "${CHAINED[@]}" | sed 's/^\(.*\):\(.*\)$/\2 -> \1/'
  step "Schedules"; gcloud scheduler jobs list --location "$SCHEDULER_LOCATION" || echo "(none yet)"
  step "Secrets (versions, never values)"
  for secret in "${SECRETS[@]}"; do
    printf '%-28s %s enabled version(s)\n' "$secret" \
      "$(gcloud secrets versions list "$secret" --filter 'state=ENABLED' --format 'value(name)' 2>/dev/null | wc -l | tr -d ' ')"
  done
}

case "${1:-}" in
  infra) infra ;;
  schedule) schedule ;;
  show) show ;;
  *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
