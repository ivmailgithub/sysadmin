# GetNetworkStatus.ps1
# Called by the Rainmeter skin (via the RunCommand plugin) on a timer.
# Writes a simple KEY=VALUE text file that Rainmeter's WebParser plugin reads.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File GetNetworkStatus.ps1 "<output file path>"

param(
    [string]$OutFile = "$PSScriptRoot\..\network_status.txt"
)

# ---- EDIT THIS LIST to match the ports your server.py / flask / node apps use ----
$ports = @(3000, 4200, 5000, 5001, 5432, 8000, 8080, 8888, 9000)
# -----------------------------------------------------------------------------------

# Grab all "real" IPv4 addresses (skip link-local 169.254.x.x and loopback)
$ips = @()
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object {
            $_.IPAddress -notlike '169.254.*' -and
            $_.IPAddress -ne '127.0.0.1' -and
            $_.PrefixOrigin -ne 'WellKnown'
        } |
        Select-Object -ExpandProperty IPAddress -Unique
} catch {
    # Fallback for older systems without the NetTCPIP module
    $ips = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
        ForEach-Object { $_.IPAddressToString }
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("IPCOUNT=$($ips.Count)")
for ($i = 0; $i -lt $ips.Count; $i++) {
    $lines.Add("IP$($i)=$($ips[$i])")
}

# Fast local TCP probe (avoids the ~1s ping delay of Test-NetConnection)
function Test-PortOpen {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(200, $false)
        $open = $ok -and $client.Connected
        $client.Close()
        return $open
    } catch {
        return $false
    }
}

function Get-ListeningProcessName {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
            return $proc.ProcessName
        }
    } catch {
        # Get-NetTCPConnection can fail on older systems / restricted permissions - fall back silently
    }
    return $null
}

foreach ($p in $ports) {
    $isOpen = Test-PortOpen -Port $p
    $status = if ($isOpen) { 'OPEN' } else { 'CLOSED' }
    $lines.Add("PORT_$p=$status")

    $procText = ''
    if ($isOpen) {
        $procName = Get-ListeningProcessName -Port $p
        if ($procName) { $procText = " . $procName" }
    }
    $lines.Add("PORT_${p}_PROC=$procText")
}

$lines | Out-File -FilePath $OutFile -Encoding UTF8 -Force
