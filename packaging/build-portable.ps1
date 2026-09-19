param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Release = Join-Path $ProjectRoot "dist\SpellbookReviewer"

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Missing .venv. Create it and install the dev dependencies first.'
}
if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; portable build stopped." }
}

& $Python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "spellbook.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

foreach ($directory in @("data", "migrations", "schemas", "reports", "scripts")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $directory) -Destination (Join-Path $Release $directory) -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $Release "reviews\changes") | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "spellbook_doc_v1.1.pdf") -Destination $Release -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs\REVIEWER-GUIDE.md") -Destination (Join-Path $Release "REVIEWER-GUIDE.md") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $Release -Force

$Zip = Join-Path $ProjectRoot "dist\SpellbookReviewer-windows-x64.zip"
Compress-Archive -Path (Join-Path $Release "*") -DestinationPath $Zip -Force
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()
"$Hash  SpellbookReviewer-windows-x64.zip" | Set-Content -LiteralPath "$Zip.sha256" -Encoding ascii
Write-Host "Portable build: $Zip"
Write-Host "SHA-256: $Hash"
