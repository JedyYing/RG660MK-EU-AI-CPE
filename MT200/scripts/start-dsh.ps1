# Start the local DeepSeek Harness stack on this ThinkPad:
#   1) llama-server (OpenAI-compatible, Intel iGPU via Vulkan, 127.0.0.1:8080)
#   2) dsh web UI (127.0.0.1:3080, profile thinkpad-web)
# Idempotent: already-running processes are left alone. Logs go to C:\AI\logs.
# The iGPU build measured 2.25x prefill / 1.58x decode over the CPU build for 1.7B;
# set $BinDir to C:\AI\bin\llama-cpu and -ngl to 0 to fall back to CPU only.
$ErrorActionPreference = 'Continue'

$LogDir   = 'C:\AI\logs'
$BinDir   = 'C:\AI\bin\llama-vulkan'
$Model    = 'C:\AI\models\qwen3-1.7b.gguf'
$DshHome  = 'C:\AI\dsh-home'
$WorkDir  = 'C:\AI\dsh'
$DshCmd   = 'C:\Users\jedy.ying\dsh-runtime\npm-global\dsh.cmd'

New-Item -ItemType Directory -Force $LogDir | Out-Null

# 1) inference service -------------------------------------------------------
$llama = Get-Process llama-server -ErrorAction SilentlyContinue
if ($llama) {
    Write-Host "[1/3] llama-server already running (pid $($llama.Id -join ','))"
} else {
    $llamaArgs = @(
        '-m', $Model, '--alias', 'qwen3-1.7b',
        '--host', '127.0.0.1', '--port', '8080',
        '-c', '12288', '-t', '4', '-np', '1', '--jinja', '-ngl', '99',
        '--reasoning', 'off', '--api-key', 'sk-local'
    )
    Start-Process -FilePath (Join-Path $BinDir 'llama-server.exe') -ArgumentList $llamaArgs `
        -WorkingDirectory $BinDir `
        -RedirectStandardOutput (Join-Path $LogDir 'llama.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'llama.err.log') | Out-Null
    Write-Host "[1/3] llama-server starting…"
}

# 2) wait for the OpenAI-compatible API --------------------------------------
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $m = Invoke-RestMethod 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2 -Headers @{ Authorization = 'Bearer sk-local' }
        if ($m.data) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) {
    Write-Host '[2/3] llama-server did NOT come up — see C:\AI\logs\llama.err.log' -ForegroundColor Red
    exit 1
}
Write-Host "[2/3] model ready: $($m.data[0].id) (n_ctx $($m.data[0].meta.n_ctx))"

# 3) harness web UI ----------------------------------------------------------
$env:DSH_HOME = $DshHome
$env:LOCAL_LLM_KEY = 'sk-local'
$listening = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[3/3] dsh web already listening on 127.0.0.1:3080"
} else {
    if (-not (Test-Path $DshCmd)) { throw "dsh launcher not found: $DshCmd" }
    Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', "`"$DshCmd`"", '--profile', 'thinkpad-web', '--host', '127.0.0.1', '--port', '3080', '--no-open' `
        -WorkingDirectory $WorkDir `
        -RedirectStandardOutput (Join-Path $LogDir 'dsh-web.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'dsh-web.err.log') | Out-Null
    Start-Sleep -Seconds 4
    Write-Host "[3/3] dsh web starting…"
}

$token = (Select-String -Path (Join-Path $LogDir 'dsh-web.out.log') -Pattern 'token=([A-Za-z0-9_-]+)' |
          Select-Object -Last 1).Matches.Groups[1].Value
Write-Host ''
if ($token) {
    Write-Host "Web UI: http://127.0.0.1:3080/?token=$token"
} else {
    Write-Host "Web UI: http://127.0.0.1:3080 (token in $LogDir\dsh-web.out.log)"
}
