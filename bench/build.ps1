$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    & dotnet build src/dotnet/Bench.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw 'C# build failed' }
    # Compile C++ for this CPU, as the .NET JIT does: AVX-512 when available, else AVX2.
    # (Windows PowerShell 5.1 has no System.Runtime.Intrinsics; it gets AVX2.)
    $arch = 'AVX2'
    try {
        if ([System.Runtime.Intrinsics.X86.Avx512BW]::IsSupported) { $arch = 'AVX512' }
        elseif (-not [System.Runtime.Intrinsics.X86.Avx2]::IsSupported) { $arch = '' }
    } catch { }
    & cmake -S src/cpp -B src/cpp/build -G 'Visual Studio 18 2026' -A x64 "-DBENCH_ARCH=$arch"
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
    & cmake --build src/cpp/build --config Release --parallel
    if ($LASTEXITCODE -ne 0) { throw 'C++ build failed' }
} finally { Pop-Location }
