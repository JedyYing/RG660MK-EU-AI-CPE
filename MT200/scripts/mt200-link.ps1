# Link the USB Ethernet port to MT200 (10.42.0.81/24) by adding a static IP on the same subnet.
# ASCII only. Run elevated.
$ErrorActionPreference = 'Continue'
$log = 'D:\AI\logs\mt200-link.log'
function W($m) { $m | Tee-Object -FilePath $log -Append }

W "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') mt200 link setup ==="
$ad = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like 'Realtek USB GbE*' -and $_.Status -eq 'Up' } | Select-Object -First 1
if (-not $ad) { W 'ERROR: Realtek USB GbE adapter not Up'; exit 1 }
W ("adapter: {0} idx={1} status={2} speed={3}" -f $ad.Name, $ad.ifIndex, $ad.Status, $ad.LinkSpeed)

$existing = Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like '10.42.0.*' }
if (-not $existing) {
    New-NetIPAddress -InterfaceIndex $ad.ifIndex -IPAddress 10.42.0.2 -PrefixLength 24 -ErrorAction Continue | Out-Null
    W 'added 10.42.0.2/24'
} else {
    W ('already has {0}' -f ($existing.IPAddress -join ','))
}

Start-Sleep -Seconds 2
Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 | ForEach-Object { W ('ip: {0}/{1}' -f $_.IPAddress, $_.PrefixLength) }
W ('ping 10.42.0.81: {0}' -f (Test-Connection -ComputerName 10.42.0.81 -Count 2 -Quiet))
foreach ($p in 22, 3080, 8080) {
    $t = Test-NetConnection -ComputerName 10.42.0.81 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue
    W ('port {0}: {1}' -f $p, $t)
}
W 'done'
