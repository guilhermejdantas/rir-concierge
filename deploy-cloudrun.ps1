<#
.SYNOPSIS
  Build and deploy the RIR Concierge to Google Cloud Run (southamerica-east1).

.DESCRIPTION
  - Enables the required APIs (optional ones are best-effort).
  - Upserts secrets to Secret Manager from .env / secrets/.
  - Writes env vars to a temp YAML file (avoids all shell-quoting issues) and
    deploys the container built from the repo Dockerfile via Cloud Build.
  - Single always-on instance so the in-process APScheduler keeps ticking.
  - Second pass: writes the generated URL back as PUBLIC_BASE_URL.

  Pure ASCII, compatible with Windows PowerShell 5.1 and PowerShell 7.
  gcloud writes progress to stderr, so this script does NOT use
  'ErrorActionPreference = Stop'; it checks $LASTEXITCODE explicitly instead.

  Requires: gcloud CLI, "gcloud auth login" done, secrets/oauth_token.json present.

.EXAMPLE
  .\deploy-cloudrun.ps1 -ProjectId n8n-automation-501418
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $ProjectId,
    [string] $Region  = "southamerica-east1",
    [string] $Service = "rir-concierge"
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Invoke-GCloud {
    # Run gcloud; throw only on a non-zero exit code. stderr (progress) is shown.
    param([Parameter(Mandatory = $true)][string[]] $GArgs, [switch] $AllowFail)
    & gcloud @GArgs
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        throw "gcloud $($GArgs -join ' ') failed (exit $LASTEXITCODE)."
    }
    return $LASTEXITCODE
}

# ---- Load .env into a hashtable ---------------------------------------- #
$envMap = @{}
foreach ($line in (Get-Content ".env")) {
    if ($line -match '^\s*#') { continue }
    if ($line -notmatch '=') { continue }
    $idx = $line.IndexOf('=')
    $key = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    if ($key) { $envMap[$key] = $value }
}
function Val {
    param([string] $Key, [string] $Default = "")
    if ($envMap.ContainsKey($Key) -and $envMap[$Key] -ne "") { return $envMap[$Key] }
    return $Default
}

if (-not (Test-Path "secrets/oauth_token.json")) {
    throw "secrets/oauth_token.json missing. Run scripts/google_oauth_bootstrap.py first."
}

Write-Host "==> Project $ProjectId / region $Region / service $Service"
Invoke-GCloud @("config", "set", "project", $ProjectId) | Out-Null

# ---- Enable APIs ------------------------------------------------------- #
Write-Host "==> Enabling required APIs (this can take a minute)"
Invoke-GCloud @("services", "enable",
    "run.googleapis.com", "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com", "artifactregistry.googleapis.com") | Out-Null

Write-Host "==> Enabling optional APIs (best-effort)"
Invoke-GCloud @("services", "enable",
    "calendar-json.googleapis.com", "distance-matrix-backend.googleapis.com") -AllowFail | Out-Null

# ---- Secrets --------------------------------------------------------- #
function Set-Secret {
    param([string] $Name, [string] $Value, [string] $FromFile)
    # create is a no-op error if it already exists -> AllowFail
    Invoke-GCloud @("secrets", "create", $Name, "--replication-policy=automatic") -AllowFail | Out-Null
    if ($FromFile) {
        Invoke-GCloud @("secrets", "versions", "add", $Name, "--data-file=$FromFile") | Out-Null
    } else {
        $tmp = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($tmp, $Value)
        try {
            Invoke-GCloud @("secrets", "versions", "add", $Name, "--data-file=$tmp") | Out-Null
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "   secret $Name updated"
}

Write-Host "==> Upserting secrets"
Set-Secret -Name "rir-oauth-token"   -FromFile "secrets/oauth_token.json"
Set-Secret -Name "rir-verify-token"  -Value (Val "WHATSAPP_VERIFY_TOKEN" "change-me")
Set-Secret -Name "rir-meta-token"    -Value (Val "META_ACCESS_TOKEN" "unset")
Set-Secret -Name "rir-maps-key"      -Value (Val "GOOGLE_MAPS_API_KEY" "unset")
Set-Secret -Name "rir-gemini-key"    -Value (Val "GEMINI_API_KEY" "unset")
Set-Secret -Name "rir-claude-key"    -Value (Val "ANTHROPIC_API_KEY" "unset")
Set-Secret -Name "rir-evolution-key" -Value (Val "EVOLUTION_API_KEY" "unset")

# ---- Grant the runtime service account access to the secrets --------- #
$projNum = (& gcloud projects describe $ProjectId --format="value(projectNumber)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $projNum) { throw "Could not read project number for $ProjectId." }
$runtimeSa = "$projNum-compute@developer.gserviceaccount.com"
# The compute SA is used BOTH as the Cloud Run runtime identity (needs to read
# secrets) AND, on newer projects, as the Cloud Build service account (needs to
# read the uploaded source from GCS and push the image to Artifact Registry).
Write-Host "==> Granting IAM roles to $runtimeSa"
foreach ($role in @(
        "roles/secretmanager.secretAccessor",
        "roles/cloudbuild.builds.builder")) {
    Invoke-GCloud @("projects", "add-iam-policy-binding", $ProjectId,
        "--member=serviceAccount:$runtimeSa",
        "--role=$role", "--condition=None") | Out-Null
}
Write-Host "   (IAM changes can take up to a minute to propagate)"
Start-Sleep -Seconds 20

# ---- Env vars -> temp YAML file ------------------------------------- #
$envValues = [ordered]@{
    APP_ENV                 = "prod"
    LOG_LEVEL               = "INFO"
    REDIS_URL               = ""
    GOOGLE_AUTH_MODE        = "oauth"
    GOOGLE_OAUTH_TOKEN_FILE = "/secrets/oauth_token.json"
    TARGET_CALENDAR_ID      = (Val "TARGET_CALENDAR_ID" "guilherme.dantas.sp@gmail.com")
    OWNER_EMAIL             = (Val "OWNER_EMAIL" "guilherme.dantas.sp@gmail.com")
    FESTIVAL_TIMEZONE       = (Val "FESTIVAL_TIMEZONE" "America/Sao_Paulo")
    FESTIVAL_START_DATE     = (Val "FESTIVAL_START_DATE" "2026-09-11")
    FESTIVAL_END_DATE       = (Val "FESTIVAL_END_DATE" "2026-09-13")
    ALERT_LEAD_MINUTES      = (Val "ALERT_LEAD_MINUTES" "30")
    SCHEDULER_POLL_SECONDS  = (Val "SCHEDULER_POLL_SECONDS" "60")
    WHATSAPP_PROVIDER       = (Val "WHATSAPP_PROVIDER" "meta")
    WHATSAPP_GROUP_ID       = (Val "WHATSAPP_GROUP_ID" "")
    META_GRAPH_VERSION      = (Val "META_GRAPH_VERSION" "v20.0")
    META_PHONE_NUMBER_ID    = (Val "META_PHONE_NUMBER_ID" "")
    EVOLUTION_BASE_URL      = (Val "EVOLUTION_BASE_URL" "")
    EVOLUTION_INSTANCE      = (Val "EVOLUTION_INSTANCE" "rir-concierge")
    GEMINI_MODEL            = (Val "GEMINI_MODEL" "gemini-3.5-flash")
    REASONING_PROVIDER      = (Val "REASONING_PROVIDER" "auto")
    CLAUDE_MODEL            = (Val "CLAUDE_MODEL" "claude-opus-5")
}

$envFile = Join-Path ([System.IO.Path]::GetTempPath()) ("rir-env-" + [System.Guid]::NewGuid().ToString("N") + ".yaml")
$sb = New-Object System.Text.StringBuilder
foreach ($k in $envValues.Keys) {
    $v = [string]$envValues[$k]
    $v = $v.Replace('\', '\\').Replace('"', '\"')
    [void]$sb.AppendLine(('{0}: "{1}"' -f $k, $v))
}
[System.IO.File]::WriteAllText($envFile, $sb.ToString())
Write-Host "==> Env vars written to $envFile"

$secretsArg = @(
    "/secrets/oauth_token.json=rir-oauth-token:latest",
    "WHATSAPP_VERIFY_TOKEN=rir-verify-token:latest",
    "META_ACCESS_TOKEN=rir-meta-token:latest",
    "GOOGLE_MAPS_API_KEY=rir-maps-key:latest",
    "GEMINI_API_KEY=rir-gemini-key:latest",
    "ANTHROPIC_API_KEY=rir-claude-key:latest",
    "EVOLUTION_API_KEY=rir-evolution-key:latest"
) -join ","

# ---- Deploy -------------------------------------------------------- #
Write-Host "==> Building + deploying (Cloud Build from Dockerfile) - a few minutes"
try {
    Invoke-GCloud @("run", "deploy", $Service,
        "--source=.", "--region=$Region", "--allow-unauthenticated",
        "--port=8000", "--cpu=1", "--memory=512Mi",
        "--min-instances=1", "--max-instances=1", "--no-cpu-throttling",
        "--timeout=120",
        "--env-vars-file=$envFile", "--set-secrets=$secretsArg") | Out-Null
} finally {
    Remove-Item $envFile -Force -ErrorAction SilentlyContinue
}

# ---- Second pass: publish the URL back --------------------------- #
$url = (& gcloud run services describe $Service --region $Region --format="value(status.url)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $url) { throw "Deploy reported success but no service URL was returned." }
Write-Host "==> Service URL: $url"
Invoke-GCloud @("run", "services", "update", $Service, "--region=$Region",
    "--update-env-vars=PUBLIC_BASE_URL=$url") | Out-Null

Write-Host ""
Write-Host "DONE."
Write-Host "  Webhook (Meta / Evolution):  $url/webhook/whatsapp"
Write-Host "  Verify token:                value of WHATSAPP_VERIFY_TOKEN in .env"
Write-Host "  Health:                      $url/health"
Write-Host "  Web check-in:                $url/checkin?u=ID"
