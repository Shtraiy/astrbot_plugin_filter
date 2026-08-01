# Restore Codex default model to official GPT-5.6-Sol
# IMPORTANT: quit the Codex desktop app completely before running this.
$ErrorActionPreference = 'Stop'

$dir    = 'C:\Users\Resalia\.codex'
$cfg    = Join-Path $dir 'config.toml'
$models = Join-Path $dir 'models.json'
$ts     = Get-Date -Format 'yyyyMMdd-HHmmss'

try {
    # Warn if the Codex app is still running
    $codexProc = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like '*codex*' }
    if ($codexProc) {
        Write-Host '[WARN] Codex app process detected. Quit it completely first, otherwise it may overwrite these changes.' -ForegroundColor Yellow
    }

    # 1. Keep fresh backups so the change is reversible
    Copy-Item -LiteralPath $cfg    -Destination (Join-Path $dir "config.toml.pre-gpt-restore-$ts.bak")
    Copy-Item -LiteralPath $models -Destination (Join-Path $dir "models.json.pre-gpt-restore-$ts.bak")
    Write-Host "[OK] Backups created: config.toml.pre-gpt-restore-$ts.bak / models.json.pre-gpt-restore-$ts.bak"

    # 2. Point Codex back at the official model and drop the DeepSeek login/provider overrides
    $text = [System.IO.File]::ReadAllText($cfg)
    $text = $text -replace '(?m)^model = "deepseek-v4-flash"$', 'model = "gpt-5.6-sol"'
    $text = $text -replace '(?m)^model_provider = "deepseek"$\r?\n', ''
    $text = $text -replace '(?m)^preferred_auth_method = "apikey"$\r?\n', ''
    $text = $text -replace '(?m)^forced_login_method = "api"$\r?\n', ''
    [System.IO.File]::WriteAllText($cfg, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '[OK] config.toml updated.'

    # 3. Restore the official model catalog (removes the injected DeepSeek entry)
    $official = Join-Path $dir 'models.json.pre-deepseek-v4-flash-20260801-013537.bak'
    if (-not (Test-Path -LiteralPath $official)) { throw "Official backup not found: $official" }
    Copy-Item -LiteralPath $official -Destination $models -Force
    Write-Host '[OK] models.json restored to official catalog.'

    # 4. Verify
    $modelLine = (Select-String -LiteralPath $cfg -Pattern '^model =' | ForEach-Object { $_.Line })
    $hasProvider = [bool](Select-String -LiteralPath $cfg -Pattern '^model_provider' -SimpleMatch -Quiet)
    $deepseekCount = (Select-String -LiteralPath $models -Pattern 'deepseek' -SimpleMatch | Measure-Object).Count
    Write-Host "model line: $modelLine"
    Write-Host "model_provider override present: $hasProvider"
    Write-Host "deepseek mentions in models.json: $deepseekCount"
    if ($modelLine -eq 'model = "gpt-5.6-sol"' -and -not $hasProvider -and $deepseekCount -eq 0) {
        Write-Host '[DONE] Restored successfully. Now restart Codex and open a NEW chat.'
    } else {
        Write-Host '[FAILED] Verification did not pass. Check the output above.' -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
