param(
    [string]$Version = "",
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = (Get-Content -LiteralPath (Join-Path $projectRoot "metadata\VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must use MAJOR.MINOR.PATCH format: $Version"
}
$outputRoot = Join-Path $projectRoot $OutputDirectory
$packageName = "MAA-Agent-v$Version-Windows"
$stageRoot = Join-Path $outputRoot $packageName
$archivePath = Join-Path $outputRoot "$packageName.zip"
$checksumPath = Join-Path $outputRoot "$packageName.sha256"

$excludedFiles = @(".gitignore", "docs/internal/AGENTS.local.md", "MANIFEST.sha256")
$excludedPrefixes = @("scripts/", "docs/CHANGES_", "docs/ROADMAP", "docs/TEST_REPORT")

function Test-ReleasePath([string]$RelativePath) {
    $normalized = $RelativePath.Replace("\", "/")
    if ($excludedFiles -contains $normalized) { return $false }
    foreach ($prefix in $excludedPrefixes) {
        if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
if (Test-Path -LiteralPath $checksumPath) { Remove-Item -LiteralPath $checksumPath -Force }
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

$exePath = & (Join-Path $PSScriptRoot "build_exe.ps1")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Executable build failed."
}

$trackedFiles = & git -C $projectRoot ls-files
if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }
$untrackedPublicFiles = @("docs/RELEASE_NOTES_v$Version.md")
$releaseFiles = @($trackedFiles) + $untrackedPublicFiles | Sort-Object -Unique

foreach ($relativePath in $releaseFiles) {
    if (-not (Test-ReleasePath $relativePath)) { continue }
    $sourcePath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { continue }
    $targetPath = Join-Path $stageRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $targetPath) -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath
}
Copy-Item -LiteralPath $exePath -Destination (Join-Path $stageRoot "MAA-Agent.exe")

$forbiddenNames = @("config.json", "config.json.bak", "agent.log", "decision.log")
$forbiddenDirectories = @(".git", ".venv", "state", "logs", "backups", "disabled_alerts", "__pycache__", "scripts")
foreach ($item in Get-ChildItem -LiteralPath $stageRoot -Recurse -Force) {
    if ($forbiddenNames -contains $item.Name) { throw "Forbidden release file: $($item.FullName)" }
    if ($item.PSIsContainer -and $forbiddenDirectories -contains $item.Name) { throw "Forbidden release directory: $($item.FullName)" }
}

$secretPatterns = @('sk-[A-Za-z0-9_-]{16,}', 'github_pat_[A-Za-z0-9_]{20,}', 'ghp_[A-Za-z0-9]{20,}')
$textExtensions = @(".py", ".json", ".md", ".txt", ".bat", ".vbs", ".ps1")
foreach ($file in Get-ChildItem -LiteralPath $stageRoot -Recurse -File) {
    if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
    $content = Get-Content -LiteralPath $file.FullName -Raw
    foreach ($pattern in $secretPatterns) {
        if ($content -match $pattern) { throw "Potential secret in release file: $($file.FullName)" }
    }
}

Compress-Archive -LiteralPath $stageRoot -DestinationPath $archivePath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksumPath -Value "$hash  $packageName.zip" -Encoding ascii
Write-Output $archivePath
Write-Output $checksumPath
