# Windows equivalent of linux/start_app.sh.
#
# Run it from anywhere. It steps up to the repo root to find
# RFbenchToolkit.py; the launcher finds apps/icons/, apps/fonts/ and config/ from its
# own file, so nothing else depends on the working directory.
#
#   powershell -ExecutionPolicy Bypass -File .\windows\start_app.ps1

# This script lives in windows\, one below the repo root.
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function Find-Conda {
    # CONDA_EXE is already set inside an activated shell. Otherwise look where
    # the per-user installers put it; a machine-wide install lands in
    # ProgramData. Get-Command last, since it only finds conda if the user let
    # the installer touch PATH, which it does not do by default.
    if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) { return $env:CONDA_EXE }
    $candidates = @(
        "$env:USERPROFILE\miniforge3\Scripts\conda.exe",
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "$env:LOCALAPPDATA\miniforge3\Scripts\conda.exe",
        "C:\ProgramData\miniforge3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$conda = Find-Conda
if (-not $conda) {
    Write-Error "conda not found. Run windows\bootstrap.ps1 first."
    exit 1
}

# The shell hook defines conda-activate for this process only. Going through it
# beats calling the env's python.exe directly: GNU Radio's DLLs live in
# Library\bin, and without activation putting that on PATH the import fails
# with a bare 'DLL load failed' that names nothing useful.
(& $conda "shell.powershell" "hook") | Out-String | Invoke-Expression
conda activate gnu
if ($LASTEXITCODE -ne 0) {
    Write-Error "could not activate the 'gnu' environment. Run windows\bootstrap.ps1 first."
    exit 1
}

python RFbenchToolkit.py
exit $LASTEXITCODE
