# Start the 35B MoE model (Qwen3.5-35B-A3B) on the Intel iGPU via Vulkan, port 8081.
#   -ngl   how many layers to offload (the iGPU reports a ~16 GB shared heap, so a
#          16.6 GB Q3 model cannot be fully resident: use 99 and let llama.cpp keep
#          what fits, or lower it until the allocation succeeds)
# Usage: start-35b.ps1 [-Ngl 99] [-Ctx 16384] [-Threads 6]
param(
    [int]$Ngl = 99,
    [int]$Ctx = 16384,
    [int]$Threads = 6
)
$ErrorActionPreference = 'Continue'

$LogDir = 'D:\AI\logs'
$BinDir = 'D:\AI\bin\llama-vulkan'
$Model  = 'D:\AI\models\Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf'

if (-not (Test-Path $Model)) { Write-Host "model missing: $Model" -ForegroundColor Red; exit 1 }

$running = Get-Process llama-server -ErrorAction SilentlyContinue |
           Where-Object { $_.Path -like '*llama-vulkan*' }
if ($running) { Write-Host "vulkan llama-server already running (pid $($running.Id -join ','))" }

$llamaArgs = @(
    '-m', $Model, '--alias', 'qwen3.5-35b-a3b',
    '--host', '127.0.0.1', '--port', '8081',
    '-c', "$Ctx", '-t', "$Threads", '-np', '1', '--jinja',
    '-ngl', "$Ngl", '--reasoning', 'off', '--api-key', 'sk-local'
)
Start-Process -FilePath (Join-Path $BinDir 'llama-server.exe') -ArgumentList $llamaArgs `
    -WorkingDirectory $BinDir `
    -RedirectStandardOutput (Join-Path $LogDir 'llama-35b.out.log') `
    -RedirectStandardError  (Join-Path $LogDir 'llama-35b.err.log') | Out-Null

for ($i = 0; $i -lt 600; $i++) {
    try {
        $m = Invoke-RestMethod 'http://127.0.0.1:8081/v1/models' -TimeoutSec 3 -Headers @{ Authorization = 'Bearer sk-local' }
        if ($m.data) {
            Write-Host "ready: $($m.data[0].id)  n_ctx=$($m.data[0].meta.n_ctx)  size=$([math]::Round($m.data[0].meta.size/1GB,1)) GB"
            Write-Host "provider baseURL: http://127.0.0.1:8081/v1   (dsh profile: thinkpad-35b)"
            exit 0
        }
    } catch { Start-Sleep -Seconds 2 }
}
Write-Host 'model did not come up — see D:\AI\logs\llama-35b.err.log' -ForegroundColor Red
exit 1
