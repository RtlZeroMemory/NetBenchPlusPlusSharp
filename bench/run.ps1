param(
    [string]$Matrix = 'bench/matrices/smoke.json',
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$RunnerArgs
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    & python bench/run.py --matrix $Matrix --output $Output @RunnerArgs
    if ($LASTEXITCODE -ne 0) { throw "Benchmark runner exited $LASTEXITCODE" }
    & python bench/report.py $Output
    if ($LASTEXITCODE -ne 0) { throw "Report generation exited $LASTEXITCODE" }
} finally { Pop-Location }
