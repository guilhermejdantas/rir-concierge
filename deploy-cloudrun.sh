#!/usr/bin/env bash
# Build and deploy the RIR Concierge to Google Cloud Run (southamerica-east1).
#
# Mirrors deploy-cloudrun.ps1. Reads config from ./.env. Requires: gcloud CLI,
# an authenticated account (guilherme.dantas.sp@gmail.com), and
# secrets/oauth_token.json present.
#
# Usage:  ./deploy-cloudrun.sh <PROJECT_ID> [REGION] [SERVICE]
set -euo pipefail

PROJECT_ID="${1:?Usage: ./deploy-cloudrun.sh <PROJECT_ID> [REGION] [SERVICE]}"
REGION="${2:-southamerica-east1}"
SERVICE="${3:-rir-concierge}"

cd "$(dirname "$0")"

[ -f secrets/oauth_token.json ] || {
  echo "secrets/oauth_token.json missing. Run scripts/google_oauth_bootstrap.py first." >&2
  exit 1
}

# ---- Load .env -------------------------------------------------------------- #
set -a; # shellcheck disable=SC1091
. <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env || true); set +a
val() { local v="${!1:-}"; printf '%s' "${v:-${2:-}}"; }

echo "==> Project $PROJECT_ID / region $REGION / service $SERVICE"
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com
echo "==> Enabling optional APIs (calendar, distance matrix) - non-fatal"
gcloud services enable calendar-json.googleapis.com distance-matrix-backend.googleapis.com \
  || echo "   (skipped - enable manually if you use a Maps API key)"

# ---- Secrets --------------------------------------------------------------- #
set_secret() { # name  value  [file]
  local name="$1" value="${2:-}" file="${3:-}"
  gcloud secrets describe "$name" >/dev/null 2>&1 || \
    gcloud secrets create "$name" --replication-policy=automatic >/dev/null
  if [ -n "$file" ]; then
    gcloud secrets versions add "$name" --data-file="$file" >/dev/null
  else
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  fi
}

echo "==> Upserting secrets"
set_secret rir-oauth-token   "" secrets/oauth_token.json
set_secret rir-verify-token  "$(val WHATSAPP_VERIFY_TOKEN change-me)"
set_secret rir-meta-token    "$(val META_ACCESS_TOKEN unset)"
set_secret rir-maps-key      "$(val GOOGLE_MAPS_API_KEY unset)"
set_secret rir-gemini-key    "$(val GEMINI_API_KEY unset)"
set_secret rir-claude-key    "$(val ANTHROPIC_API_KEY unset)"
set_secret rir-evolution-key "$(val EVOLUTION_API_KEY unset)"

# ---- Grant runtime SA access to secrets ---------------------------------- #
PROJ_NUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
echo "==> Granting IAM roles to $RUNTIME_SA"
# secretAccessor: Cloud Run runtime reads secrets.
# cloudbuild.builds.builder: on newer projects the compute SA is also the Cloud
#   Build SA and must read the uploaded source + push to Artifact Registry.
for role in roles/secretmanager.secretAccessor roles/cloudbuild.builds.builder; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="$role" --condition=None >/dev/null
done
echo "   (IAM changes can take up to a minute to propagate)"
sleep 20

# ---- Env vars -> temp YAML file (avoids delimiter/empty-value issues) --- #
ENV_FILE="$(mktemp -t rir-env-XXXXXX.yaml)"
trap 'rm -f "$ENV_FILE"' EXIT
yaml_kv() { printf '%s: "%s"\n' "$1" "$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/"/\\"/g')" >> "$ENV_FILE"; }
: > "$ENV_FILE"
yaml_kv APP_ENV                 prod
yaml_kv LOG_LEVEL               INFO
yaml_kv REDIS_URL               ""
yaml_kv GOOGLE_AUTH_MODE        oauth
yaml_kv GOOGLE_OAUTH_TOKEN_FILE /secrets/oauth_token.json
yaml_kv TARGET_CALENDAR_ID      "$(val TARGET_CALENDAR_ID guilherme.dantas.sp@gmail.com)"
yaml_kv OWNER_EMAIL             "$(val OWNER_EMAIL guilherme.dantas.sp@gmail.com)"
yaml_kv FESTIVAL_TIMEZONE       "$(val FESTIVAL_TIMEZONE America/Sao_Paulo)"
yaml_kv FESTIVAL_START_DATE     "$(val FESTIVAL_START_DATE 2026-09-11)"
yaml_kv FESTIVAL_END_DATE       "$(val FESTIVAL_END_DATE 2026-09-13)"
yaml_kv ALERT_LEAD_MINUTES      "$(val ALERT_LEAD_MINUTES 30)"
yaml_kv SCHEDULER_POLL_SECONDS  "$(val SCHEDULER_POLL_SECONDS 60)"
yaml_kv WHATSAPP_PROVIDER       "$(val WHATSAPP_PROVIDER meta)"
yaml_kv WHATSAPP_GROUP_ID       "$(val WHATSAPP_GROUP_ID)"
yaml_kv META_GRAPH_VERSION      "$(val META_GRAPH_VERSION v20.0)"
yaml_kv META_PHONE_NUMBER_ID    "$(val META_PHONE_NUMBER_ID)"
yaml_kv EVOLUTION_BASE_URL      "$(val EVOLUTION_BASE_URL)"
yaml_kv EVOLUTION_INSTANCE      "$(val EVOLUTION_INSTANCE rir-concierge)"
yaml_kv GEMINI_MODEL            "$(val GEMINI_MODEL gemini-3.5-flash)"
yaml_kv REASONING_PROVIDER      "$(val REASONING_PROVIDER auto)"
yaml_kv CLAUDE_MODEL            "$(val CLAUDE_MODEL claude-opus-5)"

SECRETS_ARG="/secrets/oauth_token.json=rir-oauth-token:latest,WHATSAPP_VERIFY_TOKEN=rir-verify-token:latest,META_ACCESS_TOKEN=rir-meta-token:latest,GOOGLE_MAPS_API_KEY=rir-maps-key:latest,GEMINI_API_KEY=rir-gemini-key:latest,ANTHROPIC_API_KEY=rir-claude-key:latest,EVOLUTION_API_KEY=rir-evolution-key:latest"

# ---- Deploy ------------------------------------------------------------ #
echo "==> Building + deploying (Cloud Build from Dockerfile)"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8000 \
  --cpu 1 --memory 512Mi \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --timeout 120 \
  --env-vars-file "$ENV_FILE" \
  --set-secrets "${SECRETS_ARG}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "==> Service URL: $URL"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "PUBLIC_BASE_URL=${URL}" >/dev/null

cat <<EOF

DONE.
  Webhook (Meta / Evolution):  ${URL}/webhook/whatsapp
  Verify token:                (value of WHATSAPP_VERIFY_TOKEN in .env)
  Health:                      ${URL}/health
  Web check-in:                ${URL}/checkin?u=<id>
EOF
