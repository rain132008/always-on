$ErrorActionPreference = 'Stop'
$env:ALWAYS_ON_SDD_RUNNER = 'claude'
if (-not $env:ALWAYS_ON_CLAUDE_MODEL) {
    $env:ALWAYS_ON_CLAUDE_MODEL = 'moonshotai/Kimi-K2.7-Code'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    uv run --locked uvicorn backend.api:app --host 127.0.0.1 --port 8000 --no-access-log
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
