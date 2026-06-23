$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
Write-Host "==============================================="
Write-Host "  OrchardBridge - portable onefile EXE builder"
Write-Host "==============================================="
Write-Host ""

function Find-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py -and $py.Source) { return [pscustomobject]@{ Exe = $py.Source; Args = @("-3.10") } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return [pscustomobject]@{ Exe = $cmd.Source; Args = @() } }
    $candidates = @(
        "C:\ProgramData\anaconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) { return [pscustomobject]@{ Exe = $p; Args = @() } }
    }
    return $null
}

$python = Find-PythonCommand
if (-not $python) { throw "Python was not found. Install Python 3.10/3.11 or Anaconda first." }
Write-Host "[INFO] Bootstrap Python: $($python.Exe) $($python.Args -join ' ')"
& $python.Exe @($python.Args) --version

$venvDir = Join-Path $PSScriptRoot ".build_venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$releaseDir = Join-Path $PSScriptRoot "release"
$releaseExe = Join-Path $releaseDir "OrchardBridge.exe"
$tempExe = Join-Path $PSScriptRoot "dist\OrchardBridge.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/7] Creating clean temporary build virtual environment..."
    & $python.Exe @($python.Args) -m venv $venvDir
} else {
    Write-Host "[1/7] Reusing temporary build virtual environment..."
}

Write-Host "[2/7] Upgrading pip/setuptools/wheel..."
& $venvPython -m pip install --upgrade pip setuptools wheel

Write-Host "[3/7] Installing runtime/build requirements into build venv..."
& $venvPython -m pip install --prefer-binary -r requirements.txt

Write-Host "[4/7] Cleaning old temporary build outputs..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
Remove-Item -Force $releaseExe -ErrorAction SilentlyContinue

Write-Host "[5/7] Building onefile portable EXE with OrchardBridge.spec..."
& $venvPython -m PyInstaller --clean --noconfirm OrchardBridge.spec

if (-not (Test-Path -LiteralPath $tempExe)) {
    throw "PyInstaller finished, but the expected EXE was not found: $tempExe"
}

Write-Host "[6/7] Moving final portable EXE to release folder..."
Copy-Item -LiteralPath $tempExe -Destination $releaseExe -Force

Write-Host "[7/7] Removing temporary build folders (.build_venv, build, dist)..."
Remove-Item -Recurse -Force build, dist, .build_venv -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done. Portable EXE:"
Write-Host "  $releaseExe"
Write-Host ""
Write-Host "Upload release\OrchardBridge.exe to GitHub Releases, or commit it only if you intentionally want the binary inside the repository."
Read-Host "Press Enter to exit"
