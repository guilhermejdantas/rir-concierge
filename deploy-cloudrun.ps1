<#
.SYNOPSIS
  Build and deploy the RIR Concierge to Google Cloud Run (southamerica-east1).

.DESCRIPTION
  - Enables the required APIs.
  - Pushes non-secret config as env vars and secrets via Secret Manager.
  - Builds the container from the repo Dockerfile with Cloud Build and deploys
    it to Cloud Run with a public HTTPS URL for the Meta / Evolution webhook.
  - Runs a single always-on instance so the in-process APScheduler keeps ticking
    (--min-instances 1 --max-instances 1 --no-cpu-throttling).
  - Second pass: writes the generated URL back as PUBLIC_BASE_URL.

  Reads values from .env in the repo root. Requires: gcloud CLI, an authenticated
  account (guilherme.dantas.sp@gmail.com), and secrets/oauth_token.json present.

.EXAMPLE
  ./deploy-cloudrun.ps1 -ProjectId rir-concierge-2026
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)] [string] $ProjectId,
  [string] $Region  = "southamerica-east1",
  [string] $Service = "rir-concierge"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# ---- Load .env ----------------------------------------------------------- #
$envMap = @{}
Get-Content ".env" | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $k, $v = $_ -split '=', 2
  $envMap[$k.Trim()] = $v.Trim()
}
function Val([string] $key, [string] $default = "") {
  if ($envMap.ContainsKey($key) -and $envMap[$key]) { $envMap[$key] } else { $default }
}

if (-not (Test-Path "secrets/oauth_token.json")) {
  throw "secrets/oauth_token.json missing. Run scripts/google_oauth_bootstrap.py first."
}

Write-Host "==> Project $ProjectId / region $Region / service $Service" -ForegroundColor Cyan
gcloud config set project $ProjectId | Out-Null

# ---- Enable APIs ------------------------------------------------------- #
Write-Host "==> Enabling APIs"
gcloud services enable `
  run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com `
  artifactregistry.googleapis.com calendar-json.googleapis.com `
  distance-matrix-backend.googleapis.com

# ---- Secrets --------------------------------------------------------- #
function Set-Secret([string] $name, [string] $value, [string] $fromFile) {
  $exists = gcloud secrets describe $name --format="value(name)" 2>$null
  if (-not $exists) {
    Write-Host "   creating secret $name"
    gcloud secrets create $name --replication-policy="automatic" | Out-Null
  }
  if ($fromFile) {
    gcloud secrets versions add $name --data-file=$fromFile | Out-Null
  } else {
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $value -NoNewline
    gcloud secrets versions add $name --data-file=$tmp | Out-Null
    Remove-Item $tmp
  }
}

Write-Host "==> Upserting secrets"
Set-Secret "rir-oauth-token"  ""                       "secrets/oauth_token.json"
Set-Secret "rir-verify-token" (Val "WHATSAPP_VERIFY_TOKEN" "change-me")
Set-Secret "rir-meta-token"   (Val "META_ACCESS_TOKEN"    "unset")
Set-Secret "rir-maps-key"     (Val "GOOGLE_MAPS_API_KEY"  "unset")
Set-Secret "rir-gemini-key"   (Val "GEMINI_API_KEY"       "unset")
Set-Secret "rir-evolution-key" (Val "EVOLUTION_API_KEY"   "unset")

# ---- Grant the runtime SA access to secrets -------------------------- #
$projNum = gcloud projects describe $ProjectId --format="value(projectNumber)"
$runtimeSa = "$projNum-compute@developer.gserviceaccount.com"
Write-Host "==> Granting secretAccessor to $runtimeSa"
gcloud projects add-iam-policy-binding $ProjectId `
  --member="serviceAccount:$runtimeSa" `
  --role="roles/secretmanager.secretAccessor" --condition=None | Out-Null

# ---- Env vars (non-secret) ------------------------------------------ #
$envVars = @(
  "APP_ENV=prod",
  "LOG_LEVEL=INFO",
  "REDIS_URL=",                       # empty => in-process state store (single instance)
  "GOOGLE_AUTH_MODE=oauth",
  "GOOGLE_OAUTH_TOKEN_FILE=/secrets/oauth_token.json",
  "TARGET_CALENDAR_ID=$(Val 'TARGET_CALENDAR_ID' 'guilherme.dantas.sp@gmail.com')",
  "OWNER_EMAIL=$(Val 'OWNER_EMAIL' 'guilherme.dantas.sp@gmail.com')",
  "FESTIVAL_TIMEZONE=$(Val 'FESTIVAL_TIMEZONE' 'America/Sao_Paulo')",
  "FESTIVAL_START_DATE=$(Val 'FESTIVAL_START_DATE' '2026-09-11')",
  "FESTIVAL_END_DATE=$(Val 'FESTIVAL_END_DATE' '2026-09-13')",
  "ALERT_LEAD_MINUTES=$(Val 'ALERT_LEAD_MINUTES' '30')",
  "SCHEDULER_POLL_SECONDS=$(Val 'SCHEDULER_POLL_SECONDS' '60')",
  "WHATSAPP_PROVIDER=$(Val 'WHATSAPP_PROVIDER' 'meta')",
  "WHATSAPP_GROUP_ID=$(Val 'WHATSAPP_GROUP_ID' '')",
  "META_GRAPH_VERSION=$(Val 'META_GRAPH_VERSION' 'v20.0')",
  "META_PHONE_NUMBER_ID=$(Val 'META_PHONE_NUMBER_ID' '')",
  "EVOLUTION_BASE_URL=$(Val 'EVOLUTION_BASE_URL' '')",
  "EVOLUTION_INSTANCE=$(Val 'EVOLUTION_INSTANCE' 'rir-concierge')",
  "GEMINI_MODEL=$(Val 'GEMINI_MODEL' 'gemini-3.5-flash')"
) -join "^|^"

$secretMounts = @(
  "/secrets/oauth_token.json=rir-oauth-token:latest"
) -join ","

$secretEnv = @(
  "WHATSAPP_VERIFY_TOKEN=rir-verify-token:latest",
  "META_ACCESS_TOKEN=rir-meta-token:latest",
  "GOOGLE_MAPS_API_KEY=rir-maps-key:latest",
  "GEMINI_API_KEY=rir-gemini-key:latest",
  "EVOLUTION_API_KEY=rir-evolution-key:latest"
) -join ","

# ---- Deploy --------------------------------------------------------- #
Write-Host "==> Building + deploying (Cloud Build from Dockerfile)"
gcloud run deploy $Service `
  --source . `
  --region $Region `
  --allow-unauthenticated `
  --port 8000 `
  --cpu 1 --memory 512Mi `
  --min-instances 1 --max-instances 1 --no-cpu-throttling `
  --timeout 120 `
  --set-env-vars "^|^$envVars" `
  --set-secrets "$secretMounts,$secretEnv"

# ---- Second pass: publish the URL back ------------------------------ #
$url = gcloud run services describe $Service --region $Region --format="value(status.url)"
Write-Host "==> Service URL: $url" -ForegroundColor Green
gcloud run services update $Service --region $Region `
  --update-env-vars "PUBLIC_BASE_URL=$url" | Out-Null

Write-Host ""
Write-Host "DONE." -ForegroundColor Green
Write-Host "  Webhook (Meta / Evolution):  $url/webhook/whatsapp"
Write-Host "  Verify token:                (value of WHATSAPP_VERIFY_TOKEN in .env)"
Write-Host "  Health:                      $url/healthz"
Write-Host "  Web check-in:                $url/checkin?u=<id>"
