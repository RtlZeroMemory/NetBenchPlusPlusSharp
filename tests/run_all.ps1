param([switch]$Asan)
# Builds are expected to exist (bench/build.ps1). Runs every correctness suite; stops on the first failure.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $cpp = 'src/cpp/build/Release/tcpbench.exe'
    $cs = 'src/dotnet/bin/Release/net11.0/Bench.dll'
    $steps = @(
        @('native selftest', $cpp, 'selftest', '--corpus', 'tests/fixtures/golden.bin'),
        @('managed selftest', 'dotnet', $cs, 'selftest', '--corpus', 'tests/fixtures/golden.bin'),
        @('harness', 'python', 'tests/harness.py'),
        @('protocol native', 'python', 'tests/protocol.py', '--server', $cpp),
        @('protocol managed', 'python', 'tests/protocol.py', '--server', $cs),
        @('differential', 'python', 'tests/differential.py', '--servers', $cpp, $cs),
        @('server regression native', 'python', 'tests/server_regression.py', '--server', $cpp),
        @('server regression managed', 'python', 'tests/server_regression.py', '--server', $cs),
        @('measurement native client', 'python', 'tests/measurement.py', '--client', $cpp),
        @('measurement managed client', 'python', 'tests/measurement.py', '--client', $cs),
        @('interop', 'python', 'tests/interop.py'),
        @('large frames', 'python', 'tests/large_frames.py')
    )
    if ($Asan) {
        $sanitized = 'src/cpp/build-asan/RelWithDebInfo/tcpbench.exe'
        $steps = @(
            @('ASAN selftest', $sanitized, 'selftest', '--corpus', 'tests/fixtures/golden.bin'),
            @('ASAN protocol', 'python', 'tests/protocol.py', '--server', $sanitized),
            @('ASAN server regression', 'python', 'tests/server_regression.py', '--server', $sanitized),
            @('ASAN measurement client', 'python', 'tests/measurement.py', '--client', $sanitized),
            @('ASAN interop', 'python', 'tests/interop.py', '--cpp', $sanitized)
        )
    }
    foreach ($step in $steps) {
        Write-Host "== $($step[0])"
        & $step[1] $step[2..($step.Length - 1)]
        if ($LASTEXITCODE -ne 0) { throw "$($step[0]) failed ($LASTEXITCODE)" }
    }
    Write-Host '== all passed'
} finally { Pop-Location }
