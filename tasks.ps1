param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("install","lint","check","test","pipeline","etl","step","ci","verify")]
  [string]$Task,
  [Parameter(Mandatory = $false, Position = 1)]
  [string]$STEP
)

$ErrorActionPreference = "Stop"

function Write-Section([string]$text, [string]$color = 'Cyan') {
  Write-Host ("==== $text ====") -ForegroundColor $color
}

function Use-Poetry() { return [bool](Get-Command poetry -ErrorAction SilentlyContinue) }

function PyRun([string[]]$pyArgs) {
  if (Use-Poetry) { & poetry run python @pyArgs } else { & python @pyArgs }
}

function Set-EnvIfMissing([string]$name, [string]$value) {
  $current = [Environment]::GetEnvironmentVariable($name, "Process")
  if (-not $current -or $current -eq "") { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
}

function Set-DbEnv() {
  Set-EnvIfMissing "PYTHONIOENCODING" "utf-8"
}

function Resolve-TargetDate() {
  try {
    $baseUrl = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"
    $resp = Invoke-WebRequest -Uri $baseUrl -UseBasicParsing -TimeoutSec 30
    $dates = @()
    foreach ($l in $resp.Links) {
      $href = $l.href
      if ($href -match '^[0-9]{4}-[0-9]{2}/?$') { $dates += ($href.TrimEnd('/')) }
    }
    if ($dates.Count -gt 0) {
      $sorted = $dates | Sort-Object -Descending
      return $sorted[0]
    }
  } catch {}
  return (Get-Date).ToString('yyyy-MM')
}

function Update-DotEnvTargetDate([string]$Value) {
  $path = Join-Path (Get-Location) ".env"
  if (Test-Path $path) {
    $lines = Get-Content -Path $path -Encoding UTF8
    $found = $false
    for ($i=0; $i -lt $lines.Count; $i++) {
      if ($lines[$i] -match '^\s*target_date\s*=') {
        $lines[$i] = "target_date=$Value"
        $found = $true
        break
      }
    }
    if (-not $found) { $lines += "target_date=$Value" }
    Set-Content -Path $path -Value ($lines -join [Environment]::NewLine) -Encoding UTF8
  } else {
    Set-Content -Path $path -Value ("target_date=$Value") -Encoding UTF8
  }
}

function Set-TargetDate() {
  $td = Resolve-TargetDate
  [Environment]::SetEnvironmentVariable("TARGET_DATE", $td, "Process")
  [Environment]::SetEnvironmentVariable("target_date", $td, "Process")
  Update-DotEnvTargetDate -Value $td
  Write-Host ("[INFO] target_date = {0}" -f $td) -ForegroundColor Cyan
}

function Write-DotEnvIfMissing() {
  $path = Join-Path (Get-Location) ".env"
  if (-not (Test-Path $path)) {
    Set-Content -Path $path -Value "duckdb_path=data/cnpj.duckdb" -Encoding UTF8
  }
}

function Invoke-Step([string]$name, [ScriptBlock]$action, [bool]$AllowFail = $false) {
  Write-Host ("[START] $name") -ForegroundColor Yellow
  $swStep = [System.Diagnostics.Stopwatch]::StartNew()
  & $action
  $exit = $LASTEXITCODE
  $swStep.Stop()
  if (-not $exit) { $exit = 0 }
  if ($exit -ne 0) {
    Write-Host ("[FAIL] $name (codigo $exit) em {0:mm\:ss\.fff}" -f $swStep.Elapsed) -ForegroundColor Red
    if (-not $AllowFail) { exit $exit }
  } else {
    Write-Host ("[OK] $name em {0:mm\:ss\.fff}" -f $swStep.Elapsed) -ForegroundColor Green
  }
}

switch ($Task) {
  "install" {
    if (Use-Poetry) { poetry install --with dev } else {
      python -m pip install --upgrade pip
      pip install -r requirements.txt -r requirements-dev.txt
    }
  }
  "lint" {
    if (Use-Poetry) { poetry run ruff check . } else { ruff check . }
  }
  "check" {
    if (Use-Poetry) { poetry run python -m compileall -q src } else { python -m compileall -q src }
  }
  "test" {
    if (Use-Poetry) { poetry run pytest -q } else { pytest -q }
  }
  "pipeline" {
    if (Use-Poetry) { poetry run python -m src } else { python -m src }
  }
  "etl" {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Set-DbEnv
    Set-TargetDate
    Write-DotEnvIfMissing
    Write-Section "ETL"
    Invoke-Step "download" { if (Use-Poetry) { poetry run python -m src --step download } else { python -m src --step download } } $true
    Invoke-Step "extract" { if (Use-Poetry) { poetry run python -m src --step extract } else { python -m src --step extract } }
    Invoke-Step "load" { if (Use-Poetry) { poetry run python -m src --step load } else { python -m src --step load } }
    Invoke-Step "constraints" { if (Use-Poetry) { poetry run python -m src --step constraints } else { python -m src --step constraints } }
    $sw.Stop()
    Write-Host ("==== ETL concluido em {0:hh\:mm\:ss\.fff} ====" -f $sw.Elapsed) -ForegroundColor Magenta
  }
  "step" {
    if (-not $STEP) { Write-Error "STEP is required"; exit 2 }
    Write-Section ("Executando step: $STEP")
    Invoke-Step $STEP { if (Use-Poetry) { poetry run python -m src --step $STEP } else { python -m src --step $STEP } }
  }
  "ci" {
    if (-not (Test-Path ".venv310\Scripts\python.exe")) { try { py -3.10 -m venv .venv310 } catch { python -m venv .venv310 } }
    if (Test-Path ".venv310\Scripts\Activate.ps1") {
      . .\.venv310\Scripts\Activate.ps1
    }
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt -r requirements-dev.txt
    pytest -q --maxfail=1 --disable-warnings --cov=src --cov-report=term-missing
    ruff check .
    try { mypy src } catch { Write-Host "[WARN] mypy check failed or crashed" -ForegroundColor Yellow }
    
    $skipDockerBuild = ($env:SKIP_DOCKER_BUILD -eq "1" -or $env:SKIP_DOCKER_BUILD -eq "true")
    if ($skipDockerBuild) {
      Write-Host "[INFO] Skipping docker build (SKIP_DOCKER_BUILD=$($env:SKIP_DOCKER_BUILD))" -ForegroundColor Cyan
    } else {
      # Check if docker is available
      if (Get-Command docker -ErrorAction SilentlyContinue) {
        docker build -t etl-cnpj:ci .
        if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] docker build failed, continuing CI without image" -ForegroundColor Yellow }
      } else {
         Write-Host "[INFO] Docker not found, skipping docker build" -ForegroundColor Cyan
      }
    }
    
    # DuckDB is embedded: the load tests run against a temporary file, so there
    # is no service to start and no gate to set.
    Set-DbEnv
    pytest -q --maxfail=1 --disable-warnings
  }
  "verify" {
    Write-Section "Installing Dependencies"
    if (Use-Poetry) { poetry install --with dev } else {
      python -m pip install --upgrade pip
      pip install -r requirements.txt -r requirements-dev.txt
    }
    
    Write-Section "Linting"
    if (Use-Poetry) { poetry run ruff check . } else { ruff check . }
    
    Write-Section "Type/Compile Check"
    if (Use-Poetry) { poetry run python -m compileall -q src } else { python -m compileall -q src }
    
    # Try mypy but don't fail verify if it crashes (known issue in some envs)
    try {
        if (Use-Poetry) { poetry run mypy src } else { mypy src }
        if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] mypy reported issues" -ForegroundColor Yellow }
    } catch {
        Write-Host "[WARN] mypy failed to run" -ForegroundColor Yellow
    }
    
    Write-Section "Unit Tests"
    if (Use-Poetry) { 
        poetry run pytest -q --maxfail=1 --disable-warnings --cov=src --cov-report=term-missing 
    } else { 
        pytest -q --maxfail=1 --disable-warnings --cov=src --cov-report=term-missing 
    }
    
    Write-Section "Verification Complete" "Green"
  }
}

