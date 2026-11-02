# The WritersBot window on a Windows computer: "Get the WritersBot window.cmd" runs this. It asks for the bot's address,
# gets what is missing with Windows' own app installer, keeps its own copy of the code so the download can be deleted,
# and leaves a WritersBot shortcut on the desktop. The bot itself stays on the Orin.

param(
    [string]$Address = "",
    [string]$Dest = (Join-Path $env:USERPROFILE "WritersBot window"),
    [string]$Name = "WritersBot"
)

$ErrorActionPreference = "Stop"
$Source = Join-Path $PSScriptRoot "BuildBotv2"

function Say([string]$Text) { Write-Host $Text }
function Stop-Here([string]$Text) { Say ""; Say $Text; exit 1 }

if (-not (Test-Path -LiteralPath (Join-Path $Source "install.ps1"))) {
    Stop-Here "The rest of the folder is not here. Right-click the zip file, choose Extract All, and run this from the new folder."
}

Say ""
Say "WritersBot window"
Say "This puts the window on this computer. WritersBot and your books stay on the Orin."
Say ""

# What she may type: 192.168.1.50, 192.168.1.50:8423 or the whole http://192.168.1.50:8423
if (-not $Address) {
    Say "Ask the person at the Orin for its address. It looks like http://192.168.1.50:8423"
    $Address = Read-Host "Type the address"
}
$Bare = ($Address.Trim() -replace '^https?://', '').TrimEnd('/')
if ($Bare -notmatch ':\d+$') { $Bare = $Bare + ":8423" }
if ($Bare -notmatch '^[A-Za-z0-9.-]+:\d+$') { Stop-Here "That does not look like an address. It looks like http://192.168.1.50:8423" }
$BotHost, $BotPort = $Bare -split ':'
$Url = "http://" + $Bare

$Reached = $false
$Tcp = New-Object System.Net.Sockets.TcpClient
try {
    $Try = $Tcp.BeginConnect($BotHost, [int]$BotPort, $null, $null)
    if ($Try.AsyncWaitHandle.WaitOne(5000)) { $Tcp.EndConnect($Try); $Reached = $true }
} catch { $Reached = $false } finally { $Tcp.Close() }
if ($Reached) {
    Say "  Found the bot at $Url"
} else {
    Say "  This computer cannot reach the bot at $Url right now."
    Say "  It has to be on the same home network as the Orin, and the bot has to be running."
    $Answer = Read-Host "  Set up the window anyway? [y/N]"
    if ($Answer -notmatch '^[Yy]') { Stop-Here "Nothing was changed. Run this again when the bot can be reached." }
}

function Have-Python {
    foreach ($Try in @(@("py", "-3"), @("python"))) {
        if (-not (Get-Command $Try[0] -ErrorAction SilentlyContinue)) { continue }
        try {
            $PyArgs = @($Try | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 11))")
            if ((& $Try[0] @PyArgs 2>$null) -eq "True") { return $true }
        } catch { }
    }
    return $false
}

function Get-App([string]$Id, [string]$Words, [string]$Link) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-Here "This computer needs $Words, and Windows' app installer is not here to get it. Get it from $Link, then run this again."
    }
    Say "  This computer needs $Words. Getting it now, which can take a few minutes."
    & winget install -e --id $Id --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    # 0 is done. Anything else may mean it is there already, so the next step is the real test.
    if ($LASTEXITCODE -ne 0) { Say "  Windows' app installer ended with code $LASTEXITCODE. Going on to see if it is there." }
}

if (Have-Python) { Say "  Python: yes." } else { Get-App "Python.Python.3.11" "Python" "https://www.python.org/downloads/" }

$WebView = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$HasWebView = $false
foreach ($Root in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients", "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients", "HKCU:\Software\Microsoft\EdgeUpdate\Clients")) {
    if (Test-Path -LiteralPath (Join-Path $Root $WebView)) { $HasWebView = $true }
}
if ($HasWebView) { Say "  Microsoft's WebView2: yes." } else { Get-App "Microsoft.EdgeWebView2Runtime" "Microsoft's WebView2" "https://developer.microsoft.com/microsoft-edge/webview2/" }

Say "  Copying the window's code to: $Dest"
$Target = Join-Path $Dest "BuildBotv2"
& robocopy $Source $Target /E /XD .venv .git tests __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Stop-Here "The code could not be copied to $Dest." }

Say ""
Say "The window opens next and asks for a sign-in code. To get one, the person at the Orin runs:"
Say "    ./install-writersbot.sh pair"
Say "You type the code once. After that, open the window from the $Name shortcut on your desktop."
Say ""

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Target "install.ps1") -WindowFor $Url -WindowName $Name
exit $LASTEXITCODE
