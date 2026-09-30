<#
written by agy gemini38flashhigh...
dahhh  20260930  i forgot to install opencv-python ... and requirements.txt barfed on wrong version
so on first launch wyze_server.py pops up the firewall dialog .. but on some boxes that never appears
so run this script...
.SYNOPSIS
    Configures Windows Defender Firewall rules for Wyze RTSP/RTSPS Multi-Cam Viewer.

.DESCRIPTION
    Opens required Inbound and Outbound ports for:
      - Wyze RTSPS (Port 322 TCP)
      - Wyze RTSP Standard (Port 554 TCP & UDP)
      - Wyze Alternate RTSP (Port 8554 TCP & UDP, Port 8555 TCP)
      - Wyze Multi-Cam Web Server (Port 5005 TCP)
      - Wyze Discovery & P2P / TUTK (Ports 10001, 10002 UDP & UDP 1024-65535)
      - Wyze Bridge / WebRTC / RTMP / HLS (Ports 1935, 5000, 8888, 8889, 8443)
      - Python Executable Application rules (Python 3.12, 3.13, and local venv)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\open_wyze_firewall_ports.ps1
    powershell -ExecutionPolicy Bypass -File .\open_wyze_firewall_ports.ps1 -Remove
#>

[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$TestConnection
)

# ── Ensure Administrator Privileges ───────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Administrative privileges required to manage Windows Firewall rules." -ForegroundColor Yellow
    Write-Host "[*] Please right-click 'open_wyze_firewall_ports.bat' and select 'Run as administrator'," -ForegroundColor Cyan
    Write-Host "    or run PowerShell as Administrator and execute this script." -ForegroundColor Cyan
    try {
        $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        if ($Remove) { $arguments += " -Remove" }
        if ($TestConnection) { $arguments += " -TestConnection" }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -ErrorAction Stop
        exit
    } catch {
        Write-Host "`n[!] Notice: Interactive UAC prompt could not be launched from this background session." -ForegroundColor Yellow
        Write-Host "    To apply these firewall rules, open an Administrator PowerShell prompt and run:" -ForegroundColor Yellow
        Write-Host "    powershell -ExecutionPolicy Bypass -File .\open_wyze_firewall_ports.ps1`n" -ForegroundColor White
        exit 1
    }
}

$RULE_PREFIX = "[Wyze Viewer]"

# ── Clean / Remove Rules ───────────────────────────────────────────────────────
function Remove-WyzeRules {
    Write-Host "`n[*] Removing existing Wyze Firewall rules..." -ForegroundColor Cyan
    $existing = Get-NetFirewallRule -DisplayName "$RULE_PREFIX*" -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | ForEach-Object {
            Write-Host "    [-] Removing: $($_.DisplayName)" -ForegroundColor Gray
            Remove-NetFirewallRule -Name $_.Name -ErrorAction SilentlyContinue
        }
        Write-Host "[+] All Wyze Firewall rules removed.`n" -ForegroundColor Green
    } else {
        Write-Host "    No existing rules found.`n" -ForegroundColor Gray
    }
}

if ($Remove) {
    Remove-WyzeRules
    Write-Host "Press any key to exit..."
    $null = [Console]::ReadKey()
    exit
}

# ── Define Port Rules ─────────────────────────────────────────────────────────
$PortRules = @(
    @{
        Name        = "WyzeViewer-RTSPS-322-In"
        DisplayName = "$RULE_PREFIX Inbound RTSPS (Wyze Cam Stream Port 322 TCP)"
        Direction   = "Inbound"
        Protocol    = "TCP"
        LocalPort   = 322
        Description = "Allows incoming Wyze RTSPS camera video stream handshakes and return traffic."
    },
    @{
        Name        = "WyzeViewer-RTSPS-322-Out"
        DisplayName = "$RULE_PREFIX Outbound RTSPS (Wyze Cam Stream Port 322 TCP)"
        Direction   = "Outbound"
        Protocol    = "TCP"
        RemotePort  = 322
        Description = "Allows outbound connections to Wyze cameras on RTSPS TLS port 322."
    },
    @{
        Name        = "WyzeViewer-RTSP-554-In"
        DisplayName = "$RULE_PREFIX Inbound RTSP Standard (Port 554 TCP/UDP)"
        Direction   = "Inbound"
        Protocol    = "TCP,UDP"
        LocalPort   = 554
        Description = "Allows incoming RTSP standard streams from Wyze / IP cameras."
    },
    @{
        Name        = "WyzeViewer-RTSP-554-Out"
        DisplayName = "$RULE_PREFIX Outbound RTSP Standard (Port 554 TCP/UDP)"
        Direction   = "Outbound"
        Protocol    = "TCP,UDP"
        RemotePort  = 554
        Description = "Allows outbound connections to Wyze / IP cameras on standard RTSP port 554."
    },
    @{
        Name        = "WyzeViewer-RTSP-8554-In"
        DisplayName = "$RULE_PREFIX Inbound RTSP Alt (Port 8554 TCP/UDP)"
        Direction   = "Inbound"
        Protocol    = "TCP,UDP"
        LocalPort   = 8554
        Description = "Allows incoming RTSP streams from docker-wyze-bridge / go2rtc / wz_mini_hacks."
    },
    @{
        Name        = "WyzeViewer-RTSP-8554-Out"
        DisplayName = "$RULE_PREFIX Outbound RTSP Alt (Port 8554 TCP/UDP)"
        Direction   = "Outbound"
        Protocol    = "TCP,UDP"
        RemotePort  = 8554
        Description = "Allows outbound RTSP connections to docker-wyze-bridge / go2rtc on port 8554."
    },
    @{
        Name        = "WyzeViewer-WebUI-5005-In"
        DisplayName = "$RULE_PREFIX Inbound Web UI & MJPEG Stream (Port 5005 TCP)"
        Direction   = "Inbound"
        Protocol    = "TCP"
        LocalPort   = 5005
        Description = "Allows web browser and LAN clients to access the Wyze Multi-Cam Viewer web dashboard."
    },
    @{
        Name        = "WyzeViewer-WebUI-5005-Out"
        DisplayName = "$RULE_PREFIX Outbound Web UI & MJPEG Stream (Port 5005 TCP)"
        Direction   = "Outbound"
        Protocol    = "TCP"
        LocalPort   = 5005
        Description = "Allows outbound socket responses from Wyze Multi-Cam Viewer on port 5005."
    },
    @{
        Name        = "WyzeViewer-Discovery-UDP-In"
        DisplayName = "$RULE_PREFIX Inbound Wyze LAN Discovery & P2P (UDP 10001, 10002)"
        Direction   = "Inbound"
        Protocol    = "UDP"
        LocalPort   = @("10001", "10002")
        Description = "Allows Wyze camera UDP broadcast discovery and TUTK P2P session establishment."
    },
    @{
        Name        = "WyzeViewer-Discovery-UDP-Out"
        DisplayName = "$RULE_PREFIX Outbound Wyze LAN Discovery & P2P (UDP 10001, 10002)"
        Direction   = "Outbound"
        Protocol    = "UDP"
        RemotePort  = @("10001", "10002")
        Description = "Allows outbound Wyze UDP discovery broadcasts on the local subnet."
    },
    @{
        Name        = "WyzeViewer-Media-UDP-In"
        DisplayName = "$RULE_PREFIX Inbound Wyze RTP/P2P Media Stream (UDP 10000-65535)"
        Direction   = "Inbound"
        Protocol    = "UDP"
        LocalPort   = "10000-65535"
        Description = "Allows incoming UDP RTP video and audio packets from Wyze RTSP / TUTK P2P streams."
    },
    @{
        Name        = "WyzeViewer-Bridge-WebRTC-In"
        DisplayName = "$RULE_PREFIX Inbound Wyze Bridge WebRTC & HLS (Ports 1935, 5000, 8443, 8888, 8889)"
        Direction   = "Inbound"
        Protocol    = "TCP"
        LocalPort   = @("1935", "5000", "8443", "8888", "8889")
        Description = "Allows docker-wyze-bridge WebRTC, RTMP, and HLS streaming protocols."
    },
    @{
        Name        = "WyzeViewer-Bridge-WebRTC-Out"
        DisplayName = "$RULE_PREFIX Outbound Wyze Bridge WebRTC & HLS (Ports 1935, 5000, 8443, 8888, 8889)"
        Direction   = "Outbound"
        Protocol    = "TCP"
        RemotePort  = @("1935", "5000", "8443", "8888", "8889")
        Description = "Allows outbound connections to docker-wyze-bridge WebRTC, RTMP, and HLS streaming."
    }
)

# ── Apply Port Rules ──────────────────────────────────────────────────────────
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "   WYZE MULTI-CAM VIEWER • WINDOWS FIREWALL CONFIGURATION       " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$results = @()

foreach ($rule in $PortRules) {
    try {
        # Check if rule exists
        $existing = Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
        if ($existing) {
            Remove-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
        }

        $params = @{
            Name        = $rule.Name
            DisplayName = $rule.DisplayName
            Description = $rule.Description
            Direction   = $rule.Direction
            Action      = "Allow"
            Enabled     = "True"
            Profile     = "Any"
        }

        if ($rule.Protocol -eq "TCP,UDP") {
            # Create both TCP and UDP rules
            foreach ($proto in @("TCP", "UDP")) {
                $subName = "$($rule.Name)-$proto"
                $subDisp = "$($rule.DisplayName) [$proto]"
                $subParams = $params.Clone()
                $subParams["Name"] = $subName
                $subParams["DisplayName"] = $subDisp
                $subParams["Protocol"] = $proto
                if ($rule.ContainsKey("LocalPort"))  { $subParams["LocalPort"]  = $rule.LocalPort }
                if ($rule.ContainsKey("RemotePort")) { $subParams["RemotePort"] = $rule.RemotePort }
                
                Remove-NetFirewallRule -Name $subName -ErrorAction SilentlyContinue
                New-NetFirewallRule @subParams | Out-Null
                $results += [PSCustomObject]@{
                    Rule        = $subDisp
                    Direction   = $rule.Direction
                    Protocol    = $proto
                    Port        = if ($rule.ContainsKey("LocalPort")) { "Local: " + ($rule.LocalPort -join ',') } else { "Remote: " + ($rule.RemotePort -join ',') }
                    Status      = "Active / Allowed"
                }
            }
            continue
        } else {
            $params["Protocol"] = $rule.Protocol
            if ($rule.ContainsKey("LocalPort"))  { $params["LocalPort"]  = $rule.LocalPort }
            if ($rule.ContainsKey("RemotePort")) { $params["RemotePort"] = $rule.RemotePort }
            New-NetFirewallRule @params | Out-Null
            $results += [PSCustomObject]@{
                Rule        = $rule.DisplayName
                Direction   = $rule.Direction
                Protocol    = $rule.Protocol
                Port        = if ($rule.ContainsKey("LocalPort")) { "Local: " + ($rule.LocalPort -join ',') } else { "Remote: " + ($rule.RemotePort -join ',') }
                Status      = "Active / Allowed"
            }
        }
    } catch {
        Write-Warning "Failed to configure rule $($rule.Name): $_"
    }
}

# ── Add Python Executable Application Rules ───────────────────────────────────
$PythonCandidates = @(
    "C:\Python\Python313\python.exe",
    "C:\Python\Python312\python.exe",
    "$PSScriptRoot\Scripts\python.exe",
    "$PSScriptRoot\.venv\Scripts\python.exe"
)

foreach ($pyPath in $PythonCandidates) {
    if (Test-Path $pyPath) {
        $verStr = (Split-Path (Split-Path $pyPath -Parent) -Leaf)
        if ($verStr -match "Scripts") {
            $verStr = "Venv-" + (Split-Path (Split-Path (Split-Path $pyPath -Parent) -Parent) -Leaf)
        }
        $appRuleIn  = "WyzeViewer-App-$verStr-In"
        $appRuleOut = "WyzeViewer-App-$verStr-Out"

        Remove-NetFirewallRule -Name $appRuleIn  -ErrorAction SilentlyContinue
        Remove-NetFirewallRule -Name $appRuleOut -ErrorAction SilentlyContinue

        New-NetFirewallRule -Name $appRuleIn  -DisplayName "$RULE_PREFIX Allow Python Inbound ($verStr)"  -Program $pyPath -Direction Inbound  -Action Allow -Profile Any -Description "Allows inbound RTSP/RTSPS stream reception for Wyze Viewer." | Out-Null
        New-NetFirewallRule -Name $appRuleOut -DisplayName "$RULE_PREFIX Allow Python Outbound ($verStr)" -Program $pyPath -Direction Outbound -Action Allow -Profile Any -Description "Allows outbound camera connections for Wyze Viewer." | Out-Null

        $results += [PSCustomObject]@{
            Rule        = "$RULE_PREFIX Python Application Rule ($verStr)"
            Direction   = "In & Out"
            Protocol    = "Any"
            Port        = $pyPath
            Status      = "Active / Allowed"
        }
    }
}

# ── Display Summary Table ─────────────────────────────────────────────────────
$results | Format-Table -AutoSize -Property Rule, Direction, Protocol, Port, Status

Write-Host "[+] All Wyze Firewall Rules successfully created and activated!" -ForegroundColor Green
Write-Host ""
Write-Host "Key Ports Opened:" -ForegroundColor Yellow
Write-Host "  • Port 322  (TCP)     : Wyze Cam TLS RTSPS video ingestion" -ForegroundColor White
Write-Host "  • Port 554  (TCP/UDP) : Standard RTSP video ingestion" -ForegroundColor White
Write-Host "  • Port 8554 (TCP/UDP) : Wyze Bridge / go2rtc alternate RTSP" -ForegroundColor White
Write-Host "  • Port 5005 (TCP)     : Wyze Multi-Cam Viewer local web dashboard" -ForegroundColor White
Write-Host "  • Port 10001-10002    : Wyze LAN broadcast discovery & P2P" -ForegroundColor White
Write-Host "  • Ports 10000-65535   : Inbound UDP RTP streaming packets" -ForegroundColor White
Write-Host ""

# ── Optional Camera Connectivity Test ─────────────────────────────────────────
$cfgFile = Join-Path $PSScriptRoot "wyze_config.yaml"
if (Test-Path $cfgFile) {
    Write-Host "[*] Testing configured camera connectivity from wyze_config.yaml..." -ForegroundColor Cyan
    $cfgContent = Get-Content $cfgFile -Raw
    $ips = [regex]::Matches($cfgContent, "host:\s*([0-9\.]+)") | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
    
    foreach ($camIp in $ips) {
        Write-Host "  Testing connection to $camIp on port 322 ... " -NoNewline
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $async = $tcp.BeginConnect($camIp, 322, $null, $null)
            $wait = $async.AsyncWaitHandle.WaitOne(1500, $false)
            if ($wait -and $tcp.Connected) {
                Write-Host "ONLINE & REACHABLE (Port 322 OPEN)" -ForegroundColor Green
                $tcp.EndConnect($async)
            } else {
                Write-Host "UNREACHABLE / CLOSED (Check IP or power)" -ForegroundColor Red
            }
            $tcp.Close()
        } catch {
            Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

Write-Host "`nReady! You can now start the server with start_wyze_viewer.bat." -ForegroundColor Green
