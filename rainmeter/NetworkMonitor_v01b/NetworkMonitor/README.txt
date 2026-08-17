NETWORK MONITOR - Rainmeter skin
=================================

WHAT IT DOES
- Lists all local IPv4 addresses (Wi-Fi, Ethernet, VPN adapters, etc.)
- Shows OPEN / CLOSED status for common dev-server ports:
  3000, 4200, 5000, 5001, 5432, 8000, 8080, 8888, 9000
  (edit the $ports list in @Resources\Scripts\GetNetworkStatus.ps1 to change these)
- Each port is labeled with a friendly service name (e.g. "5000 Flask",
  "5432 PostgreSQL") set in the [Variables] section of Network.ini - purely
  cosmetic, edit freely, no rescan needed.
- When a port is OPEN, the script also looks up which process is actually
  listening on it (via Get-NetTCPConnection + Get-Process) and appends it,
  e.g. "OPEN . python" or "OPEN . node" - so you can tell which server.py
  or app is answering on that port, not just that something is.
- Refreshes automatically every 15 seconds (change Variables > UpdateDivider in
  Network.ini), or click "Click to refresh now" at the bottom to force it.

INSTALL
1. Copy the whole "NetworkMonitor" folder into your Rainmeter skins folder:
   Documents\Rainmeter\Skins\NetworkMonitor\
2. In Rainmeter, right-click the tray icon > Refresh all, or open the
   Rainmeter manager and refresh the skins list.
3. Load "NetworkMonitor\Network.ini".

REQUIREMENTS
- Windows with PowerShell (built in).
- Rainmeter's RunCommand and WebParser plugins (both ship with every
  standard Rainmeter install - no extra downloads needed).
- No special permissions needed for the port check itself (it just opens
  a TCP socket to 127.0.0.1), but the script does run with
  -ExecutionPolicy Bypass scoped to just that one process, so it won't
  touch your system-wide PowerShell execution policy.

HOW IT WORKS (for customizing)
- GetNetworkStatus.ps1 gathers IPs + probes each port with a quick
  200ms TCP connect attempt, then writes everything to a flat
  KEY=VALUE file: @Resources\network_status.txt
- Network.ini runs that script on a timer via the RunCommand plugin,
  then parses the output file with WebParser measures (one per IP,
  one per port).
- Each port measure uses IfMatch/IfNotMatchAction to recolor its
  status text and "dot" green (open) or red (closed) - no polling
  loop or Lua needed.

CUSTOMIZING
- Rename a port's label: just edit the PortXXXXName= line in Network.ini's
  [Variables] section - e.g. change "Port5000Name=Flask" to whatever you
  call that app. No script changes needed.
- Add/remove ports: edit $ports in GetNetworkStatus.ps1, add a matching
  PortXXXXName= variable, then copy/paste a MeasurePortXXXX +
  MeasurePortXXXXProc block, plus a MeterDotXXXX/MeterPortXXXXLabel/
  MeterPortXXXXStatus block in Network.ini, following the existing
  pattern (swap the port number everywhere, adjust Y= to stack below
  the last row, and bump the Background rectangle height + Footer
  Y position to fit).
- Turn off process-name detection: if you don't want/need it (e.g. running
  with reduced permissions where Get-NetTCPConnection may fail silently
  anyway), just leave it - it degrades gracefully to blank when it can't
  resolve a process.
- More than 6 network adapters: add MeasureIP6, MeterIP6, etc. the same way.
- Colors, font, size, background: all in the [Variables] section at the
  top of Network.ini.
- Want it to check a REMOTE host instead of localhost? Change
  "127.0.0.1" in the Test-PortOpen calls inside the .ps1 file.

TROUBLESHOOTING
- If nothing shows up, right-click the skin > "Edit skin" is fine, but
  to debug the script itself, open the Rainmeter log (tray icon >
  Show log) - errors from PowerShell will show up there.
- If Windows Defender/AV flags the PowerShell call, you can whitelist
  the script or replace ExecutionPolicy Bypass with your org's approved
  method of running local scripts.
