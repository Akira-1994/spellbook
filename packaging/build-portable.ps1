param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Exe = Join-Path $ProjectRoot "dist\Spellbook.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Missing .venv. Create it and install the dev dependencies first.'
}
if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; build stopped." }
}

& $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $ProjectRoot "dist") --workpath (Join-Path $ProjectRoot "build") (Join-Path $PSScriptRoot "spellbook.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash.ToLowerInvariant()
"$Hash  Spellbook.exe" | Set-Content -LiteralPath "$Exe.sha256" -Encoding ascii
$SizeMb = [math]::Round((Get-Item -LiteralPath $Exe).Length / 1MB, 1)
Write-Host "Built: $Exe ($SizeMb MB)"
Write-Host "SHA-256: $Hash"
