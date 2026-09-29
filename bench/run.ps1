param(
    [ValidateSet('smoke','first','pilot','confirm','soak')][string]$Suite = 'smoke',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$RunnerArgs
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
& python (Join-Path $PSScriptRoot 'run.py') --suite $Suite @RunnerArgs
if ($LASTEXITCODE -ne 0) { throw "Benchmark runner exited $LASTEXITCODE" }
