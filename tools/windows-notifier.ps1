<#
.SYNOPSIS
    Desktop notifications on Windows for myPrivateNewsRoom.

.DESCRIPTION
    Polls the server for items collected since the last check and raises a
    Windows toast for each one. Unlike the browser's notifications this needs
    no open tab and no HTTPS, so it works against a server on the LAN.

    Toasts are shown with the BurntToast module when it is installed, which
    also makes them clickable; otherwise it falls back to a tray balloon that
    needs nothing installed at all.

.PARAMETER Server
    Base URL of the newsroom, e.g. http://192.168.1.50:8080

.PARAMETER IntervalSeconds
    How often to poll. The server itself decides how often sources are read;
    this only controls how quickly you hear about what it found.

.PARAMETER SourceId
    Optional: only notify about one source (its id from the Sources tab).

.PARAMETER Match
    Optional: only notify about titles matching this regular expression.

.PARAMETER MaxPerCheck
    At most this many toasts per poll; the rest are summarised in one.

.PARAMETER Token
    The X-API-Token, if the server sets NEWSROOM_API_TOKEN.

.EXAMPLE
    .\windows-notifier.ps1 -Server http://192.168.1.50:8080

.EXAMPLE
    .\windows-notifier.ps1 -Server http://192.168.1.50:8080 -Match 'AI|Copilot' -IntervalSeconds 120

.NOTES
    Optional, for clickable toasts:  Install-Module -Name BurntToast -Scope CurrentUser
    Run at logon: Task Scheduler -> Create Task -> Trigger "At log on" ->
      Action: powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass
              -File C:\path\to\windows-notifier.ps1 -Server http://192.168.1.50:8080
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [int]$IntervalSeconds = 120,
    [int]$SourceId = 0,
    [string]$Match = '',
    [int]$MaxPerCheck = 3,
    [string]$Token = '',
    [string]$StateFile = "$env:LOCALAPPDATA\myPrivateNewsRoom\last-id.txt"
)

$ErrorActionPreference = 'Stop'
$Server = $Server.TrimEnd('/')
$useBurntToast = $null -ne (Get-Module -ListAvailable -Name BurntToast)
if ($useBurntToast) { Import-Module BurntToast -ErrorAction SilentlyContinue }

function Get-Headers {
    if ($Token) { return @{ 'X-API-Token' = $Token } }
    return @{}
}

function Get-LastId {
    if (Test-Path $StateFile) {
        $value = 0
        if ([int]::TryParse((Get-Content $StateFile -Raw).Trim(), [ref]$value)) { return $value }
    }
    return 0
}

function Set-LastId([int]$id) {
    $folder = Split-Path $StateFile -Parent
    if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
    Set-Content -Path $StateFile -Value $id
}

function Show-Toast([string]$title, [string]$body, [string]$link) {
    if ($useBurntToast) {
        $arguments = @{ Text = @($title, $body); AppLogo = $null }
        if ($link) {
            $button = New-BTButton -Content 'Read' -Arguments $link
            New-BurntToastNotification @arguments -Button $button
        } else {
            New-BurntToastNotification @arguments
        }
        return
    }
    # Fallback: a tray balloon, available on any Windows without extra modules
    Add-Type -AssemblyName System.Windows.Forms
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.BalloonTipTitle = $title
    $icon.BalloonTipText = $body
    $icon.Visible = $true
    $icon.ShowBalloonTip(10000)
    Start-Sleep -Milliseconds 400
    $icon.Dispose()
}

Write-Host "myPrivateNewsRoom notifier -> $Server (every ${IntervalSeconds}s)"
Write-Host $(if ($useBurntToast) { 'Using BurntToast: toasts are clickable.' }
             else { 'BurntToast not installed - using tray balloons. Install-Module BurntToast for clickable toasts.' })

# First run only marks the current position, so starting up does not replay
# everything the server has ever collected.
$lastId = Get-LastId
if ($lastId -eq 0) {
    try {
        $seed = Invoke-RestMethod -Uri "$Server/api/news?limit=1&order=fetched" -Headers (Get-Headers) -TimeoutSec 30
        if ($seed.items.Count -gt 0) { $lastId = ($seed.items | Measure-Object -Property id -Maximum).Maximum }
        Set-LastId $lastId
        Write-Host "Starting from item id $lastId."
    } catch {
        Write-Warning "Could not reach $Server - $($_.Exception.Message)"
    }
}

while ($true) {
    try {
        $url = "$Server/api/news?after_id=$lastId&limit=50&order=fetched"
        if ($SourceId -gt 0) { $url += "&source_id=$SourceId" }
        $page = Invoke-RestMethod -Uri $url -Headers (Get-Headers) -TimeoutSec 60

        $items = @($page.items)
        if ($Match) { $items = @($items | Where-Object { $_.title -match $Match }) }

        if ($items.Count -gt 0) {
            foreach ($item in ($items | Select-Object -First $MaxPerCheck)) {
                $body = $item.source
                if ($item.summary) {
                    $text = $item.summary
                    if ($text.Length -gt 120) { $text = $text.Substring(0, 120) + '...' }
                    $body = "$($item.source) - $text"
                }
                Show-Toast -title $item.title -body $body -link $item.link
            }
            if ($items.Count -gt $MaxPerCheck) {
                Show-Toast -title "$($items.Count - $MaxPerCheck) more new item(s)" `
                           -body 'myPrivateNewsRoom' -link "$Server/"
            }
        }

        # advance past everything seen, including items the filters skipped, so
        # they are not re-examined on every poll
        if ($page.items.Count -gt 0) {
            $highest = ($page.items | Measure-Object -Property id -Maximum).Maximum
            if ($highest -gt $lastId) { $lastId = $highest; Set-LastId $lastId }
        }
    } catch {
        Write-Warning "Poll failed: $($_.Exception.Message)"   # keep going; the server may be restarting
    }
    Start-Sleep -Seconds $IntervalSeconds
}
