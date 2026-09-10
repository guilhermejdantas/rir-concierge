<#
.SYNOPSIS
  Provision a small always-on GCE VM and run the full concierge stack
  (app + redis + evolution + postgres) on it, so no laptop is needed during
  the festival.

.DESCRIPTION
  1. Creates an e2-small VM in southamerica-east1 with Docker preinstalled
     (via startup script).
  2. Uploads the project (tracked files) + .env + secrets/ via scp.
  3. Runs `docker compose --profile evolution up -d` on the VM.
  4. Opens tcp:8080 to YOUR current public IP only (for the manager UI).
  5. Prints the manager URL so you can re-pair the Galaxy S26 (a fresh VM =
     a fresh Evolution instance = a new QR; remove the old linked device
     "rir-concierge" from the laptop afterwards).

  Pure ASCII. Windows PowerShell 5.1 / PowerShell 7. Checks $LASTEXITCODE.

.EXAMPLE
  .\deploy-vm.ps1 -ProjectId n8n-automation-501418
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $ProjectId,
    [string] $Zone     = "southamerica-east1-a",
    [string] $VmName   = "rir-vm",
    [string] $Machine  = "e2-small"
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function RunOrDie {
    param([string[]] $GArgs, [switch] $AllowFail)
    & gcloud @GArgs
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        throw "gcloud $($GArgs -join ' ') failed (exit $LASTEXITCODE)."
    }
    return $LASTEXITCODE
}

if (-not (Test-Path ".env")) { throw ".env missing." }
if (-not (Test-Path "secrets/oauth_token.json")) { throw "secrets/oauth_token.json missing." }

RunOrDie @("config", "set", "project", $ProjectId) | Out-Null
RunOrDie @("services", "enable", "compute.googleapis.com") | Out-Null

# ---- Startup script: install Docker + compose plugin ---------------- #
$startup = @'
#!/bin/bash
set -e
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
mkdir -p /opt/rir
'@
$startupFile = Join-Path ([System.IO.Path]::GetTempPath()) "rir-startup.sh"
[System.IO.File]::WriteAllText($startupFile, ($startup -replace "`r`n", "`n"))

# ---- Create (or reuse) the VM -------------------------------------- #
$exists = (& gcloud compute instances describe $VmName --zone $Zone --format="value(name)" 2>$null)
if (-not $exists) {
    Write-Host "==> Creating VM $VmName ($Machine) in $Zone"
    RunOrDie @("compute", "instances", "create", $VmName,
        "--zone=$Zone", "--machine-type=$Machine",
        "--image-family=debian-12", "--image-project=debian-cloud",
        "--boot-disk-size=20GB", "--boot-disk-type=pd-balanced",
        "--tags=rir-evolution",
        "--metadata-from-file=startup-script=$startupFile") | Out-Null
    Write-Host "   waiting 60s for boot + Docker install"
    Start-Sleep -Seconds 60
} else {
    Write-Host "==> Reusing existing VM $VmName"
}

# ---- Firewall: manager UI (8080) from your IP only ---------------- #
$myIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 10).Trim()
Write-Host "==> Your public IP: $myIp  (opening tcp:8080 to it)"
RunOrDie @("compute", "firewall-rules", "create", "rir-manager",
    "--allow=tcp:8080", "--source-ranges=$myIp/32",
    "--target-tags=rir-evolution", "--direction=INGRESS") -AllowFail | Out-Null
RunOrDie @("compute", "firewall-rules", "update", "rir-manager",
    "--source-ranges=$myIp/32") -AllowFail | Out-Null

# ---- Upload project + secrets ------------------------------------ #
Write-Host "==> Packaging project (tracked files only)"
$tar = Join-Path ([System.IO.Path]::GetTempPath()) "rir-src.tar.gz"
& git archive --format=tar.gz -o $tar HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed." }

$ssh = @("compute", "ssh", "$VmName", "--zone=$Zone", "--command")
$scp = @("compute", "scp", "--zone=$Zone")

RunOrDie ($ssh + @("sudo mkdir -p /opt/rir && sudo chown \$USER /opt/rir")) | Out-Null
RunOrDie ($scp + @($tar, "$VmName`:/opt/rir/rir-src.tar.gz")) | Out-Null
RunOrDie ($scp + @(".env", "$VmName`:/opt/rir/.env")) | Out-Null
RunOrDie ($ssh + @("mkdir -p /opt/rir/secrets")) | Out-Null
RunOrDie ($scp + @("--recurse", "secrets", "$VmName`:/opt/rir/")) | Out-Null

# ---- Extract + start ------------------------------------------- #
Write-Host "==> Starting the stack on the VM"
$remote = "cd /opt/rir && tar xzf rir-src.tar.gz && rm rir-src.tar.gz && " +
          "sudo docker compose --profile evolution up -d --build && " +
          "sleep 12 && sudo docker compose ps"
RunOrDie ($ssh + @($remote)) | Out-Null

$vmIp = (& gcloud compute instances describe $VmName --zone $Zone `
    --format="value(networkInterfaces[0].accessConfigs[0].natIP)").Trim()

Write-Host ""
Write-Host "DONE. VM $VmName is at $vmIp"
Write-Host ""
Write-Host "NEXT - re-pair WhatsApp (one time):"
Write-Host "  1. Open  http://$vmIp`:8080/manager"
Write-Host "     login with the EVOLUTION_API_KEY from .env"
Write-Host "  2. Instance 'rir-concierge' -> scan the QR on the Galaxy S26"
Write-Host "     (WhatsApp > Aparelhos conectados)"
Write-Host "  3. On the laptop's WhatsApp, remove the OLD 'rir-concierge' device."
Write-Host "  4. Test: send 'status' in the festival group."
Write-Host ""
Write-Host "Manage:  gcloud compute ssh $VmName --zone $Zone --command 'cd /opt/rir && sudo docker compose logs -f app'"
Write-Host "Stop VM: gcloud compute instances stop $VmName --zone $Zone   (after the festival)"
