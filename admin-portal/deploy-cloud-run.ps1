<# 
Redeploy admin portal to Cloud Run (same project/region pattern as rag-mcp-backend).

Prereqs: gcloud CLI, Docker, permissions to Cloud Run + Artifact Registry.
Run from admin-portal:  .\deploy-cloud-run.ps1

After deploy, set Cloud Run env vars (Console or gcloud) — at minimum:
  RAG_BACKEND_URL, PUBLIC_BASE_URL, OAUTH_REDIRECT_URI, GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET, SESSION_SECRET, ALLOWED_ADMIN_EMAILS

Update Google OAuth "Authorized origins" + "redirect URIs" to the new https://...run.app URLs.
#>

param(
    [string]$ProjectId = "",
    [string]$Region = "us-central1",
    [string]$Service = "rag-admin-portal",
    [string]$ArRepo = "docker",
    [string]$ImageName = "rag-admin-portal"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectId) {
    $ProjectId = (& gcloud config get-value project 2>$null).Trim()
}
if (-not $ProjectId) {
    throw "Set GCP project: gcloud config set project YOUR_PROJECT_ID"
}

$reg = "$Region-docker.pkg.dev"
$image = "${reg}/${ProjectId}/${ArRepo}/${ImageName}:$(Get-Date -Format 'yyyyMMdd-HHmmss')"

Write-Host "Project: $ProjectId  Region: $Region  Service: $Service" -ForegroundColor Cyan
Write-Host "Building: $image" -ForegroundColor Cyan

& gcloud artifacts repositories describe $ArRepo --location=$Region --project=$ProjectId 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating Artifact Registry repo '$ArRepo'..." -ForegroundColor Yellow
    & gcloud artifacts repositories create $ArRepo --repository-format=docker --location=$Region --description="Container images" --project=$ProjectId
}

$here = $PSScriptRoot
Set-Location $here
docker build -t $image .
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

& gcloud auth configure-docker "${reg}" --quiet
docker push $image
if ($LASTEXITCODE -ne 0) { throw "docker push failed" }

Write-Host "Deploying to Cloud Run..." -ForegroundColor Cyan
& gcloud run deploy $Service `
    --image $image `
    --region $Region `
    --project $ProjectId `
    --allow-unauthenticated `
    --port 8080 `
    --memory 2Gi `
    --timeout 600 `
    --min-instances 0 `
    --max-instances 2

if ($LASTEXITCODE -ne 0) { throw "gcloud run deploy failed" }

$uri = (& gcloud run services describe $Service --region $Region --project $ProjectId --format "value(status.url)").Trim()
Write-Host ""
Write-Host "Deployed: $uri" -ForegroundColor Green
Write-Host "1) Set env vars on this service (secrets for GOOGLE_CLIENT_SECRET, SESSION_SECRET)." -ForegroundColor Yellow
Write-Host "2) Set PUBLIC_BASE_URL and OAUTH_REDIRECT_URI to this URL (callback = .../auth/callback)." -ForegroundColor Yellow
Write-Host "3) Add the same URL to Google OAuth client authorized origins + redirect URIs." -ForegroundColor Yellow
