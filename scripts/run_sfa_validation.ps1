param(
    [string]$Warehouse = "data/sharadar/warehouse.duckdb",
    [int]$StepDays = 30,
    [int]$HoldDays = 30
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Warehouse)) {
    throw "Private SFA warehouse not found: $Warehouse"
}

python scripts/build_sfa_features.py --warehouse $Warehouse
if ($LASTEXITCODE -ne 0) { throw "SFA feature build failed" }

python scripts/sfa_backtest.py --warehouse $Warehouse --step $StepDays --hold $HoldDays --tune
if ($LASTEXITCODE -ne 0) { throw "SFA replay failed" }

python scripts/audit_sfa_integrity.py data/sharadar/reports/backtest-sfa-full.json --public web/data/backtest-sfa.json
if ($LASTEXITCODE -ne 0) { throw "SFA ticker/dilution integrity audit failed" }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Validation tests failed" }

Write-Output "SFA validation completed. Review web/data/backtest-sfa.json and the private report before any manual weight promotion."
