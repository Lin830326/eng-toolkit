<#
.SYNOPSIS
  One-shot install of eng-toolkit on a clean Windows 11 machine.

.DESCRIPTION
  1. Installs uv (Astral's Python package manager) if it is not on PATH.
  2. Lets uv fetch Python 3.12 and create .venv with the locked dependencies.
  3. Runs the toolkit doctor (dependency / license / workspace check).
  4. Prints the Claude Code command that registers the MCP server.

  Run from the repository root:
      powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

Write-Host "== eng-toolkit installer ==" -ForegroundColor Cyan
Write-Host "repo: $repo"

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found; installing from https://astral.sh/uv ..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

# 2. Python 3.12 + locked dependencies (uv downloads a managed Python if none is present)
uv python install 3.12
uv sync --all-groups --python 3.12
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

# 3. doctor
Write-Host "`n== toolkit_doctor ==" -ForegroundColor Cyan
uv run eng-toolkit doctor --no-scan | Select-Object -First 40
$doctorExit = $LASTEXITCODE
uv run eng-toolkit doctor | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "doctor reported failures (see full report: uv run eng-toolkit doctor)" -ForegroundColor Red
} else {
    Write-Host "doctor: OK" -ForegroundColor Green
}

# 4. register with Claude Code
Write-Host "`n== register with Claude Code ==" -ForegroundColor Cyan
Write-Host "Run this once (user scope, available in every project):"
Write-Host ""
Write-Host "  claude mcp add --scope user eng-toolkit -- uv run --directory `"$repo`" eng-toolkit" -ForegroundColor White
Write-Host ""
Write-Host "Or open this repo in Claude Code: .mcp.json is already configured."
