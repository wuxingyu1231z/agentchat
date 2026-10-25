param(
    [ValidatePattern('^[a-zA-Z0-9._/@:\-]+$')]
    [string]$BaseImage = 'python:3.12-slim-bookworm'
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerPath = if ($dockerCommand) { $dockerCommand.Source } else { Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe' }
if (-not (Test-Path -LiteralPath $dockerPath)) {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
}
if (-not (Test-Path -LiteralPath $dockerPath)) { throw 'Install Docker Desktop with the WSL2 backend first.' }
$stdoutPath = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString() + '.docker-info.txt')
$stderrPath = $stdoutPath + '.err'
$probeProcess = Start-Process -FilePath $dockerPath -ArgumentList @('info','--format','{{.OSType}}') -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
if (-not $probeProcess.WaitForExit(20000)) {
    $probeProcess.Kill()
    throw 'Docker engine did not become ready. Restart Windows if WSL/VirtualMachinePlatform was just enabled, then start Docker Desktop.'
}
if ($probeProcess.ExitCode -ne 0 -or (Get-Content -LiteralPath $stdoutPath -Raw).Trim() -ne 'linux') {
    throw 'A running Linux Docker engine is required. No host execution fallback is enabled.'
}
& $dockerPath build --build-arg "BASE_IMAGE=$BaseImage" --tag agentchat-sandbox:v1 (Join-Path $repoPath 'docker\sandbox')
if ($LASTEXITCODE -ne 0) { throw 'Sandbox image build failed.' }
Write-Output 'Sandbox image ready. Run the opt-in Docker tests before using untrusted documents.'
