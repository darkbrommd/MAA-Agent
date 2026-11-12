$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -c "import openai, PIL, psutil, pystray, requests, win32gui" *> $null
    if ($LASTEXITCODE -eq 0) { exit 0 }

    Write-Host ""
    Write-Host "MAA Agent is repairing its local environment." -ForegroundColor Cyan
    & $venvPython -m pip install -r (Join-Path $projectRoot "requirements\gui.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    Write-Host "Dependencies are ready." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "MAA Agent first-run setup" -ForegroundColor Cyan
Write-Host "Creating a local Python environment and installing dependencies..."

$candidates = @(
    @{ Command = "py"; Arguments = @("-3.11") },
    @{ Command = "py"; Arguments = @("-3") },
    @{ Command = "python"; Arguments = @() }
)
$selected = $null
foreach ($candidate in $candidates) {
    try {
        $candidateCommand = $candidate.Command
        $candidateArguments = @($candidate.Arguments)
        & $candidateCommand @candidateArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            $selected = $candidate
            break
        }
    } catch {
        continue
    }
}

if ($null -eq $selected) {
    Write-Host "Python 3.11 or newer was not found." -ForegroundColor Red
    Write-Host "Install Python from https://www.python.org/downloads/windows/ and enable Add Python to PATH."
    exit 1
}

$selectedCommand = $selected.Command
$selectedArguments = @($selected.Arguments)
& $selectedCommand @selectedArguments -m venv (Join-Path $projectRoot ".venv")
if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv." }
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements\gui.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "Setup is complete. The first-run wizard will open next." -ForegroundColor Green
exit 0
