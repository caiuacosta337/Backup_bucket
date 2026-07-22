# Image name to run; keep this aligned with your docker build tag.
$ImageName = "backup-service"
# Host folder that contains the logs or files to back up.
$SourceFolder = "C:\Temp\LOGS"
# Local folders used to persist runtime state and select the mounted config.
$StateFolder = Join-Path $PSScriptRoot "state"
$ConfigFile = Join-Path $PSScriptRoot "config.windows-docker.yml"

# Fail fast if the source or config path is missing.
if (-not (Test-Path $SourceFolder)) {
    throw "Source folder not found: $SourceFolder"
}

if (-not (Test-Path $ConfigFile)) {
    throw "Config file not found: $ConfigFile"
}

if (-not (Test-Path $StateFolder)) {
    New-Item -ItemType Directory -Path $StateFolder | Out-Null
}

# Require standard AWS environment variables instead of storing secrets in the script.
if (-not $env:AWS_ACCESS_KEY_ID) {
    throw "AWS_ACCESS_KEY_ID is not set in the current PowerShell session"
}

if (-not $env:AWS_SECRET_ACCESS_KEY) {
    throw "AWS_SECRET_ACCESS_KEY is not set in the current PowerShell session"
}

if (-not $env:AWS_DEFAULT_REGION) {
    throw "AWS_DEFAULT_REGION is not set in the current PowerShell session"
}

# Prefer the production image tag, but fall back to the validation tag if that is what was built locally.
docker image inspect $ImageName *> $null
if ($LASTEXITCODE -ne 0) {
    $FallbackImageName = "backup-service-test"
    docker image inspect $FallbackImageName *> $null
    if ($LASTEXITCODE -eq 0) {
        $ImageName = $FallbackImageName
    } else {
        throw "Docker image '$ImageName' was not found. Build it with: docker build -t backup-service ."
    }
}

# Run the container with the source folder, persistent state, config file, and AWS credentials.
docker run --rm `
  -v "${SourceFolder}:/data/source" `
  -v "${StateFolder}:/app/state" `
  -v "${ConfigFile}:/app/config.yml" `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY `
  -e AWS_DEFAULT_REGION=$env:AWS_DEFAULT_REGION `
  $ImageName python backup.py --config /app/config.yml