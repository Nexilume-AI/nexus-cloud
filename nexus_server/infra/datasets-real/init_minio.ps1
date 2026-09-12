$ErrorActionPreference = "Stop"

$compose = Join-Path $PSScriptRoot "docker-compose.yml"
docker compose -f $compose up -d minio minio-init

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9000/minio/health/ready" -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            docker compose -f $compose run --rm minio-init
            Write-Host "MinIO is ready and bucket nexus-datasets exists."
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

throw "Timed out waiting for MinIO readiness."
