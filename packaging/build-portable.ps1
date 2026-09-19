param([switch]$SkipTests)

# Builds dist/Spellbook.exe with Nuitka, which compiles the Python code to C
# (MSVC). Nuitka binaries trip antivirus heuristics far less often than
# PyInstaller's shared, widely abused bootloader.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Dist = Join-Path $ProjectRoot "dist"
$Work = Join-Path $ProjectRoot "build\nuitka"
$Exe = Join-Path $Dist "Spellbook.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Missing .venv. Create it and install the dev dependencies first.'
}
if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; build stopped." }
}

# MSVC prints localized (e.g. Chinese) messages that Nuitka's SCons backend
# cannot decode; ask it for English output.
$env:VSLANG = "1033"
$Version = (& $Python -c "import spellbook; print(spellbook.__version__)").Trim()
$NuitkaArgs = @(
    "-m", "nuitka",
    "--onefile",
    "--msvc=latest",
    "--assume-yes-for-downloads",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$ProjectRoot\packaging\spellbook.ico",
    "--product-name=Spellbook",
    "--file-description=Spellbook offline spell compendium",
    "--product-version=$Version",
    "--file-version=$Version",
    "--copyright=Personal offline spellbook",
    # Unpack once per version to a stable folder instead of a new temp folder
    # on every start: faster later starts and no churn in %TEMP%.
    "--onefile-tempdir-spec={CACHE_DIR}/Spellbook/app/{VERSION}",
    # uvicorn picks these by name at runtime, so imports alone do not reveal them.
    "--include-module=uvicorn.logging",
    "--include-module=uvicorn.loops.auto",
    "--include-module=uvicorn.loops.asyncio",
    "--include-module=uvicorn.protocols.http.auto",
    "--include-module=uvicorn.protocols.http.h11_impl",
    "--include-module=uvicorn.protocols.websockets.auto",
    "--include-module=uvicorn.lifespan.on",
    "--nofollow-import-to=pytest,_pytest,nuitka,openpyxl,PIL,pypdf,scripts,tests",
    "--include-data-dir=$ProjectRoot\spellbook\web=spellbook/web",
    "--include-data-files=$ProjectRoot\spellbook\taxonomy.json=spellbook/taxonomy.json",
    "--include-data-files=$ProjectRoot\spellbook\seed_fixes.json=spellbook/seed_fixes.json",
    "--include-data-files=$ProjectRoot\data\spellbook.sqlite=data/spellbook.sqlite",
    "--output-dir=$Work",
    "--output-filename=Spellbook.exe",
    "$ProjectRoot\spellbook\launcher.py"
)
# Nuitka reports progress on stderr; Windows PowerShell would treat that as
# an error when output is redirected, so judge success by the exit code.
$ErrorActionPreference = "Continue"
& $Python @NuitkaArgs
$NuitkaExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($NuitkaExit -ne 0) { throw "Nuitka build failed." }

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
Copy-Item -LiteralPath (Join-Path $Work "Spellbook.exe") -Destination $Exe -Force
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash.ToLowerInvariant()
"$Hash  Spellbook.exe" | Set-Content -LiteralPath "$Exe.sha256" -Encoding ascii
$SizeMb = [math]::Round((Get-Item -LiteralPath $Exe).Length / 1MB, 1)
Write-Host "Built: $Exe ($SizeMb MB, version $Version)"
Write-Host "SHA-256: $Hash"
