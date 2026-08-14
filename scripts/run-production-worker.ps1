param(
    [switch]$SkipToolCheck,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$workerEnv = Join-Path $backendDir ".env"

if (-not (Test-Path -LiteralPath $workerEnv)) {
    throw "Missing backend/.env"
}

$preflight = @'
from urllib.parse import urlsplit

from app.core.config import get_settings

settings = get_settings()
local_hosts = {
    "localhost",
    "127.0.0.1",
    "host.docker.internal",
    "mongodb",
    "redis",
    "postgres",
}
urls = {
    "POSTGRES_URL": settings.postgres_url,
    "MONGODB_URL": settings.mongodb_url,
    "REDIS_URL": settings.redis_url,
    "CELERY_BROKER_URL": settings.celery_broker_url,
    "CELERY_RESULT_BACKEND": settings.celery_result_backend,
}
errors = []
for name, value in urls.items():
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        errors.append(f"{name} is missing or invalid")
    elif parsed.hostname in local_hosts:
        errors.append(f"{name} still targets a local service")

if settings.celery_broker_url != settings.redis_url:
    errors.append("CELERY_BROKER_URL must match REDIS_URL for the deployed API")

if errors:
    raise SystemExit("Worker preflight failed:\n- " + "\n- ".join(errors))

print("Worker production connection preflight passed")
'@

Push-Location $backendDir
try {
    $preflight | python -
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (-not $SkipToolCheck) {
        $requiredTools = @("git", "node", "npm", "ruff", "bandit", "eslint")
        $missingTools = @(
            $requiredTools | Where-Object {
                $null -eq (Get-Command $_ -ErrorAction SilentlyContinue)
            }
        )
        if ($missingTools.Count -gt 0) {
            throw "Missing worker tools: $($missingTools -join ', ')"
        }
    }

    if ($PreflightOnly) {
        Write-Output "Worker toolchain preflight passed"
        return
    }

    python -m celery -A app.worker worker --loglevel=info --pool=solo
}
finally {
    Pop-Location
}
