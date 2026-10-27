# Route A: run a >100B GGUF on ThinkPad (iGPU + CPU), measure, then restore the 1.7B stack.
# Read the notes at the bottom before running. Requires: the model file already downloaded+verified.
param(
  [string]$Model = "D:\models\GLM-4.5-Air-UD-IQ1_S.gguf",
  [string]$Alias = "glm-4.5-air-iq1s",
  [int]$Port = 8081,
  [int]$Ctx = 4096,
  [int]$Ngl = 99,
  [int]$Threads = 6,
  [int]$NPredict = 128,
  [string]$Label = "glm45air-iq1s",
  [string[]]$Extra = @()
)

$ErrorActionPreference = "Stop"
$log = "D:\AI\logs\100b-$Label.log"
function Say($m) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m; $line | Tee-Object -FilePath $log -Append }

Say "=== route A test start ==="
Say ("model={0} size={1:N0} bytes" -f $Model, (Get-Item $Model).Length)
Say ("git HEAD=" + (& git -C D:\AI rev-parse --short HEAD))

# 1) stop only the llama-server on 8080 (keep dsh web on 3080)
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
  $oldPid = $conn.OwningProcess
  Say "stopping llama-server pid=$oldPid on :8080"
  Stop-Process -Id $oldPid -Force
  Start-Sleep -Seconds 3
} else { Say "no listener on :8080" }

# 2) start the 100B model on a separate port
$exe = "D:\AI\bin\llama-vulkan\llama-server.exe"
$args = @("-m", $Model, "--alias", $Alias, "--host", "127.0.0.1", "--port", "$Port",
          "-c", "$Ctx", "-t", "$Threads", "-np", "1", "--jinja", "-ngl", "$Ngl",
          "--no-warmup", "--metrics") + $Extra
Say ("starting: {0} {1}" -f $exe, ($args -join " "))
$p = Start-Process -FilePath $exe -ArgumentList $args -PassThru `
     -RedirectStandardOutput "D:\AI\logs\100b-server.out.log" `
     -RedirectStandardError "D:\AI\logs\100b-server.err.log"
Say ("server pid=" + $p.Id)

# 3) sample memory while it loads
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 15
  $proc = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
  if (-not $proc) { Say "server exited early - see 100b-server.err.log"; break }
  $os = Get-CimInstance Win32_OperatingSystem
  Say ("load {0}: rss={1:N0} MB, free={2:N0} MB, committed={3:N0} MB" -f `
       ($i * 15), ($proc.WorkingSet64 / 1MB), ($os.FreePhysicalMemory / 1KB),
       (($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1KB))
  try {
    $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
    if ($h.status -eq "ok") { Say "server health ok"; break }
  } catch { }
}

# 4) benchmark
Say "running benchmark"
& python D:\AI\scripts\bench_100b.py $Port $Label $NPredict 2>&1 | Tee-Object -FilePath $log -Append

# 5) restore the 1.7B stack and verify
Say "stopping 100B server"
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
Say "restoring 1.7B stack"
& pwsh -NoProfile -ExecutionPolicy Bypass -File D:\AI\scripts\switch-model.ps1 -Target small 2>&1 |
  Tee-Object -FilePath $log -Append
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8080/v1/models" -UseBasicParsing -TimeoutSec 5
    Say ("restore check: :8080 -> " + $r.StatusCode); break
  } catch { Say "restore check: not ready yet" }
}
Say "=== route A test end ==="

# Notes
# - The model is 35.9 GiB on a 31.5 GB box: expect heavy paging and single-digit tok/s at best.
# - If the server dies with an allocation error, retry with -Ngl 30 or -Ctx 2048.
# - Nothing here touches drivers, CMA, registry or the MT200 box.
