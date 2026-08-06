# shield-tray.ps1 - ClaudeShield system tray controller
# Zero dependencies. Uses .NET Framework built into Windows.
#
# Usage:
#   .\shield-tray.ps1
#   .\shield-tray.ps1 -Upstream https://api.lumosel.vip
#
# Right-click the tray icon for: Start/Stop/Stats/Quit

param(
    [string]$Upstream = "https://agentrouter.org",
    [int]$Port = 8787
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ProxyScript = "$env:USERPROFILE\.claude\retry-proxy.py"
$IconPath = "$env:USERPROFILE\.claude\shield-icon.png"
$ProxyProcess = $null

if (-not (Test-Path $ProxyScript)) {
    [System.Windows.Forms.MessageBox]::Show(
        "retry-proxy.py not found at $ProxyScript`n`nRun the installer first:`ncurl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python",
        "ClaudeShield", "OK", "Warning"
    )
    exit 1
}

# --- Build tray icon ---
function Make-Icon {
    param([bool]$Running = $false)
    $bmp = New-Object System.Drawing.Bitmap(64, 64)
    $g = [System.Drawing.Graphics]::FromImage($bmp)

    if (Test-Path $IconPath) {
        try {
            $src = New-Object System.Drawing.Bitmap($IconPath)
            $g.DrawImage($src, 0, 0, 64, 64)
            $src.Dispose()
        } catch {
            $g.Clear([System.Drawing.Color]::Transparent)
        }
    } else {
        $g.Clear([System.Drawing.Color]::Transparent)
    }

    # Status dot: green = running, red = stopped
    $dotColor = if ($Running) { [System.Drawing.Color]::FromArgb(131, 217, 87) }
                 else { [System.Drawing.Color]::FromArgb(239, 98, 88) }
    $ringColor = [System.Drawing.Color]::FromArgb(17, 21, 26)
    $g.FillEllipse([System.Drawing.Brushes]::FromArgb($ringColor), 42, 42, 20, 20)
    $g.FillEllipse((New-Object System.Drawing.SolidBrush($dotColor)), 45, 45, 14, 14)

    $g.Dispose()
    $hIcon = $bmp.GetHicon()
    return [System.Drawing.Icon]::FromHandle($hIcon)
}

# --- Fetch stats ---
function Get-Stats {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/stats" -TimeoutSec 2
        return $r
    } catch {
        return $null
    }
}

function Test-Proxy {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

# --- Start / Stop proxy ---
function Start-Proxy {
    if ($script:ProxyProcess -and -not $script:ProxyProcess.HasExited) { return }
    $script:ProxyProcess = Start-Process -FilePath "python" `
        -ArgumentList "`"$ProxyScript`" --start --upstream $Upstream --port $Port" `
        -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    if (Test-Proxy) {
        $NotifyIcon.ShowBalloonTip(3000, "ClaudeShield Started",
            "Proxy running on port $Port.`nUpstream: $Upstream",
            [System.Windows.Forms.ToolTipIcon]::Info)
    }
    Update-Icon
}

function Stop-Proxy {
    if ($script:ProxyProcess -and -not $script:ProxyProcess.HasExited) {
        $script:ProxyProcess.Kill()
        $script:ProxyProcess = $null
    }
    $NotifyIcon.ShowBalloonTip(3000, "ClaudeShield Stopped",
        "Proxy has been stopped.",
        [System.Windows.Forms.ToolTipIcon]::Warning)
    Update-Icon
}

function Update-Icon {
    $running = Test-Proxy
    $NotifyIcon.Icon = Make-Icon -Running $running
    $stats = Get-Stats
    if ($stats) {
        $NotifyIcon.Text = "ClaudeShield - $($stats.requests) req, $($stats.converted) converted"
    } elseif ($running) {
        $NotifyIcon.Text = "ClaudeShield - Running"
    } else {
        $NotifyIcon.Text = "ClaudeShield - Stopped"
    }
}

# --- Context menu ---
$ContextMenu = New-Object System.Windows.Forms.ContextMenuStrip

$miStart = $ContextMenu.Items.Add("Start proxy")
$miStop = $ContextMenu.Items.Add("Stop proxy")
$miStats = $ContextMenu.Items.Add("Show stats")
$ContextMenu.Items.Add("-") | Out-Null
$miQuit = $ContextMenu.Items.Add("Quit")

$miStart.Add_Click({ Start-Proxy })
$miStop.Add_Click({ Stop-Proxy })
$miStats.Add_Click({
    $stats = Get-Stats
    if ($stats) {
        $msg = "Version: $($stats.version)`nUptime: $($stats.uptime_human)`nRequests: $($stats.requests)`nConverted: $($stats.converted)`nPassed: $($stats.passed)`nErrors: $($stats.errors)`nRetry rate: $([math]::Round($stats.retry_rate * 100, 1))%"
    } else {
        $msg = "Proxy is not running."
    }
    $NotifyIcon.ShowBalloonTip(5000, "ClaudeShield Stats", $msg,
        [System.Windows.Forms.ToolTipIcon]::Info)
})
$miQuit.Add_Click({
    Stop-Proxy
    $NotifyIcon.Visible = $false
    $NotifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
})

# --- Tray icon ---
$NotifyIcon = New-Object System.Windows.Forms.NotifyIcon
$NotifyIcon.Icon = Make-Icon -Running $false
$NotifyIcon.Visible = $true
$NotifyIcon.Text = "ClaudeShield - Starting..."
$NotifyIcon.ContextMenuStrip = $ContextMenu

# Double-click toggles
$NotifyIcon.Add_DoubleClick({
    if (Test-Proxy) { Stop-Proxy } else { Start-Proxy }
})

# --- Monitor timer: update tooltip + alert on new conversions ---
$lastConverted = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({
    $stats = Get-Stats
    if ($stats) {
        $newConv = [int]$stats.converted
        if ($newConv -gt $script:lastConverted -and $script:lastConverted -ge 0) {
            $diff = $newConv - $script:lastConverted
            $NotifyIcon.ShowBalloonTip(3000, "ClaudeShield: Auto-retry triggered",
                "Converted $diff error(s) to 429.`nTotal: $newConv conversions, $($stats.requests) requests.",
                [System.Windows.Forms.ToolTipIcon]::Info)
        }
        $script:lastConverted = $newConv
    }
    Update-Icon
})
$timer.Start()

# --- Launch proxy on startup ---
Start-Proxy

# --- Run message loop (blocks until Quit) ---
[System.Windows.Forms.Application]::Run()
