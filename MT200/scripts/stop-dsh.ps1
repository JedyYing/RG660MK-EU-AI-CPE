# Stop the local DeepSeek Harness stack started by start-dsh.ps1.
$ErrorActionPreference = 'Continue'

foreach ($name in 'llama-server') {
    $p = Get-Process $name -ErrorAction SilentlyContinue
    if ($p) { $p | Stop-Process -Force; Write-Host "stopped $name (pid $($p.Id -join ','))" }
    else { Write-Host "$name not running" }
}

$conn = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    foreach ($pid in ($conn.OwningProcess | Select-Object -Unique)) {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) { $proc | Stop-Process -Force; Write-Host "stopped dsh web (pid $pid / $($proc.ProcessName))" }
    }
} else {
    Write-Host 'dsh web not listening on 3080'
}
