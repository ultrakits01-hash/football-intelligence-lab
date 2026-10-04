$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "FIL deployment preflight" -ForegroundColor Cyan

if (Test-Path '.env') {
  Write-Host "OK: .env exists locally and will NOT be included by git when .gitignore is respected." -ForegroundColor Green
}

$bad = @('.venv','__pycache__','.pytest_cache') | Where-Object { Test-Path $_ }
if ($bad.Count -gt 0) {
  Write-Host "Local-only folders present (do not commit): $($bad -join ', ')" -ForegroundColor Yellow
}

Write-Host "Checking Python source..." -ForegroundColor Cyan
py -m compileall -q apps scripts

Write-Host "Checking release QA..." -ForegroundColor Cyan
$env:PYTHONPATH='.'
py scripts\release_qa.py

Write-Host "`nReady for GitHub/Render. Before publishing, include only data you are allowed to redistribute." -ForegroundColor Green
