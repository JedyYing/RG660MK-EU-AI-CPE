# Switch the whole local stack between the two models. They cannot coexist:
#   small  → qwen3-1.7b on :8080 (iGPU, ~3 GB) + dsh web profile thinkpad-web
#   35b    → Qwen3.5-35B-A3B on :8081 (iGPU 40/48 + CPU, ~15 GB) + dsh web profile thinkpad-web-35b
# Usage: switch-model.ps1 -Target small|35b
param([Parameter(Mandatory = $true)][ValidateSet('small', '35b')][string]$Target)
$ErrorActionPreference = 'Continue'

$LogDir = 'C:\AI\logs'
$DshCmd = 'C:\Users\jedy.ying\dsh-runtime\npm-global\dsh.cmd'
New-Item -ItemType Directory -Force $LogDir | Out-Null

# 1) stop everything
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
$conn = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    foreach ($procId in ($conn.OwningProcess | Select-Object -Unique)) {
        Get-Process -Id $procId -ErrorAction SilentlyContinue | Stop-Process -Force
    }
}
Start-Sleep -Seconds 3
Write-Host '[1/3] previous stack stopped'

# 2) start the requested inference server
if ($Target -eq 'small') {
    $port = 8080
    $exe = 'C:\AI\bin\llama-vulkan\llama-server.exe'
    $argList = @('-m', 'C:\AI\models\qwen3-1.7b.gguf', '--alias', 'qwen3-1.7b',
                 '--host', '127.0.0.1', '--port', '8080', '-c', '12288', '-t', '4',
                 '-np', '1', '--jinja', '-ngl', '99',
                 '--reasoning', 'off', '--api-key', 'sk-local')
    $log = 'llama.out.log'
    $profile = 'thinkpad-web'
} else {
    $port = 8081
    $exe = 'C:\AI\bin\llama-vulkan\llama-server.exe'
    $argList = @('-m', 'C:\AI\models\Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf', '--alias', 'qwen3.5-35b-a3b',
                 '--host', '127.0.0.1', '--port', '8081', '-c', '8192', '-t', '6',
                 '-np', '1', '--jinja', '-ngl', '40',
                 '--reasoning', 'off', '--api-key', 'sk-local')
    $log = 'llama-35b-40.out.log'
    $profile = 'thinkpad-web-35b'
}

Start-Process -FilePath $exe -ArgumentList $argList `
    -WorkingDirectory (Split-Path $exe) `
    -RedirectStandardOutput (Join-Path $LogDir $log) `
    -RedirectStandardError  (Join-Path $LogDir "$log.err") | Out-Null

$ready = $false
for ($i = 0; $i -lt 240; $i++) {
    try {
        $m = Invoke-RestMethod "http://127.0.0.1:$port/v1/models" -TimeoutSec 3 -Headers @{ Authorization = 'Bearer sk-local' }
        if ($m.data) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { Write-Host "[2/3] model failed to load — see $LogDir\$log" -ForegroundColor Red; exit 1 }
Write-Host "[2/3] $($m.data[0].id) ready on :$port (n_ctx $($m.data[0].meta.n_ctx))"

# 3) start the matching web UI profile
$env:DSH_HOME = 'C:\AI\dsh-home'
$env:LOCAL_LLM_KEY = 'sk-local'
Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', "`"$DshCmd`"", '--profile', $profile, '--host', '127.0.0.1', '--port', '3080', '--no-open' `
    -WorkingDirectory 'C:\AI\dsh' `
    -RedirectStandardOutput (Join-Path $LogDir 'dsh-web.out.log') `
    -RedirectStandardError  (Join-Path $LogDir 'dsh-web.err.log') | Out-Null
Start-Sleep -Seconds 5

$token = $null
for ($i = 0; $i -lt 20 -and -not $token; $i++) {
    $logPath = Join-Path $LogDir 'dsh-web.out.log'
    if (Test-Path $logPath) {
        $m = Select-String -Path $logPath -Pattern 'token=([A-Za-z0-9_-]+)' | Select-Object -Last 1
        if ($m) { $token = $m.Matches.Groups[1].Value }
    }
    if (-not $token) { Start-Sleep -Seconds 1 }
}
Write-Host "[3/3] dsh web profile: $profile"
if ($token) { Write-Host "Web UI: http://127.0.0.1:3080/?token=$token" }
