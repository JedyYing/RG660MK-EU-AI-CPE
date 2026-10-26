# Route A, attempt 3: experts on CPU, non-expert weights on GPU, KV cache in system RAM.
param(
  [string]$Model = "D:\models\GLM-4.5-Air-UD-IQ1_S.gguf",
  [int]$Port = 8081,
  [int]$NPredict = 32,
  [string]$Label = "glm45air-v3"
)

$ErrorActionPreference = "Continue"
$log = "C:\AI\logs\100b-$Label.log"
function Say($m) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m; $line | Tee-Object -FilePath $log -Append }

Say "=== attempt 3 start ==="
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Say ("stopping :8080 pid=" + $conn.OwningProcess); Stop-Process -Id $conn.OwningProcess -Force; Start-Sleep 4 }

$exe = "C:\AI\bin\llama-vulkan\llama-server.exe"
$args = @("-m", $Model, "--alias", "glm-4.5-air-iq1s", "--host", "127.0.0.1", "--port", "$Port",
          "-c", "2048", "-t", "6", "-np", "1", "--jinja",
          "-ngl", "46", "-ncmoe", "46", "-nkvo",
          "-ctk", "q8_0", "-ctv", "q8_0",
          "-b", "128", "-ub", "64",
          "--no-warmup", "--metrics")
Say ("cmd: " + ($args -join " "))
$p = Start-Process -FilePath $exe -ArgumentList $args -PassThru `
     -RedirectStandardOutput "C:\AI\logs\100b-server3.out.log" `
     -RedirectStandardError "C:\AI\logs\100b-server3.err.log"
Say ("pid=" + $p.Id)

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 10
  $proc = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
  if (-not $proc) { Say "server exited"; break }
  $os = Get-CimInstance Win32_OperatingSystem
  if ($i % 2 -eq 0) {
    Say ("t={0}s rss={1:N0} MB free={2:N0} MB committed={3:N0} MB" -f ($i*10),
         ($proc.WorkingSet64/1MB), ($os.FreePhysicalMemory/1KB),
         (($os.TotalVirtualMemorySize - $os.FreeVirtualMemory)/1KB))
  }
  try { $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($h.status -eq "ok") { Say "health ok at t=$($i*10)s"; $ready = $true; break } } catch { }
}

if ($ready) {
  Say "benchmark start ($NPredict tokens per prompt)"
  & python C:\AI\scripts\bench_100b.py $Port $Label $NPredict 2>&1 | Tee-Object -FilePath $log -Append
} else {
  Say "not ready"; Get-Content "C:\AI\logs\100b-server3.err.log" -Tail 12 | Tee-Object -FilePath $log -Append
}

Say "stopping"; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Start-Sleep 5
Say "restoring 1.7B + dsh web"
& pwsh -NoProfile -ExecutionPolicy Bypass -File C:\AI\scripts\switch-model.ps1 -Target small 2>&1 | Tee-Object -FilePath $log -Append
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep 3
  try { $r = Invoke-WebRequest "http://127.0.0.1:8080/v1/models" -UseBasicParsing -TimeoutSec 5
        Say ("restore: :8080 -> " + $r.StatusCode); break } catch { }
}
Say "=== attempt 3 end ==="
