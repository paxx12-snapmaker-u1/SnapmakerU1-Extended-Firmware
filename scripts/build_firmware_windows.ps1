param(
    [string]$Profile = "extended",
    [string]$OutputFile = "firmware.bin",
    [string]$ImageName = "firmware-dev",
    [string]$TmpVolume = "firmware-tmp-vol",
    [string]$OutputVolume = "firmware-output-vol"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$firmwareDir = Join-Path $repoRoot "firmware"
$destPath = Join-Path $firmwareDir $OutputFile

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not available on PATH. Start Docker Desktop and try again."
}

New-Item -ItemType Directory -Force -Path $firmwareDir | Out-Null

docker image inspect $ImageName *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker image $ImageName was not found. Building it..."
    docker build --cache-from $ImageName -t $ImageName (Join-Path $repoRoot ".github/dev")
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image build failed."
    }
}

Write-Host "Building firmware profile '$Profile'..."
docker run --rm `
    -v "${repoRoot}:/work" `
    -v "${TmpVolume}:/work/tmp" `
    -v "${OutputVolume}:/work/firmware" `
    -w /work `
    --cap-add=SYS_ADMIN `
    $ImageName `
    make build "PROFILE=$Profile"

if ($LASTEXITCODE -ne 0) {
    throw "Firmware build failed."
}

Write-Host "Copying firmware artifact to $destPath..."
docker run --rm `
    -v "${OutputVolume}:/out" `
    -v "${firmwareDir}:/dest" `
    alpine `
    sh -c "cp /out/firmware.bin /dest/$OutputFile && ls -lh /dest/$OutputFile"

if ($LASTEXITCODE -ne 0) {
    throw "Failed to copy firmware artifact."
}

Write-Host "Firmware ready: $destPath"
