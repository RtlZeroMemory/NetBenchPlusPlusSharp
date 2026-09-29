$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
Push-Location $workspace
try {
    & dotnet build src/dotnet/Bench.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw 'C# build failed' }
    & cmake -S src/cpp -B src/cpp/build -G 'Visual Studio 18 2026' -A x64
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
    & cmake --build src/cpp/build --config Release --parallel
    if ($LASTEXITCODE -ne 0) { throw 'C++ build failed' }
} finally { Pop-Location }
