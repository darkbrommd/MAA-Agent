param(
    [string]$Python = "",
    [string]$OutputDirectory = "dist\exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Build Python was not found: $Python"
}

$version = (Get-Content -LiteralPath (Join-Path $projectRoot "metadata\VERSION") -Raw).Trim()
if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "VERSION must use MAJOR.MINOR.PATCH format: $version"
}
$versionTuple = "$($Matches[1]), $($Matches[2]), $($Matches[3]), 0"
$outputRoot = Join-Path $projectRoot $OutputDirectory
$workRoot = Join-Path $projectRoot "dist\pyinstaller-work"
$versionFile = Join-Path $workRoot "version_info.txt"

if (Test-Path -LiteralPath $outputRoot) { Remove-Item -LiteralPath $outputRoot -Recurse -Force }
if (Test-Path -LiteralPath $workRoot) { Remove-Item -LiteralPath $workRoot -Recurse -Force }
New-Item -ItemType Directory -Path $outputRoot, $workRoot -Force | Out-Null

@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($versionTuple), prodvers=($versionTuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404b0', [
    StringStruct('CompanyName', 'Taylor Yu'),
    StringStruct('FileDescription', 'MAA Agent Manager and Monitor'),
    StringStruct('FileVersion', '$version.0'),
    StringStruct('InternalName', 'MAA-Agent'),
    StringStruct('LegalCopyright', 'Copyright (c) Taylor Yu'),
    StringStruct('OriginalFilename', 'MAA-Agent.exe'),
    StringStruct('ProductName', 'MAA Agent'),
    StringStruct('ProductVersion', '$version.0')
  ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@ | Set-Content -LiteralPath $versionFile -Encoding utf8

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "MAA-Agent" `
    --icon (Join-Path $projectRoot "assets\app_icon.ico") `
    --version-file $versionFile `
    --paths (Join-Path $projectRoot "app") `
    --distpath $outputRoot `
    --workpath (Join-Path $workRoot "build") `
    --specpath (Join-Path $workRoot "spec") `
    (Join-Path $projectRoot "app\launcher.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$exePath = Join-Path $outputRoot "MAA-Agent.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "PyInstaller did not create $exePath"
}
Write-Output $exePath
