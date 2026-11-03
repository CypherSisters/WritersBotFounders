<#
    PuffPAI BuildBot installer (Windows, PowerShell 5.1 or newer).
    Harvested from PuffPAIBuildBot@63c8a037:install.ps1 (rewritten-from): the log file, the pause
    when it stops, plain output, and the Python finder.

    From a fresh clone, in PowerShell:
        powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1

    It:
      1. finds Python 3.11 or newer, Node.js and Ollama. For what is missing it asks once, then downloads plain
         copies into a tools folder of their own: no installer, no administrator, nothing else changed (D145),
      2. makes .venv next to this file and installs BuildBot into it,
      3. makes your bot's own folder, %LOCALAPPDATA%\PuffPAI\<bot name> (a folder that is
         already there is never used or deleted: the new bot gets <bot name>-2 and it says so),
      4. writes "Start BuildBot.cmd", "Stop BuildBot.cmd", "Chat with BuildBot.cmd" and
         "Sign in a browser.cmd" next to this file,
      5. runs "puffpai setup": the system parts (the Desktop, setup, the cloud avatar), each with
         its own Yes card for you, then the setup steps. The apps that ship with BuildBot are offered
         in the setup step "Apps", and are off until you pick them.

    Nothing is installed on Windows itself. -DryRun shows every step and every file it would write, and changes nothing.
    Test hook (not for owners): PUFFPAI_INSTALL_PRETEND_MISSING=python,node,ollama makes those look missing, so the
    missing path can be run on a computer that has them.
    Everything this window shows is also kept in install-log.txt next to this file.
    ASCII only in what it prints: the old Windows console garbles curly quotes and dashes.
#>

[CmdletBinding()]
param(
    [string]$Bot = "buildbot",     # the bot's name: its folder is %LOCALAPPDATA%\PuffPAI\<name>
    [string]$Python = "",          # use this python.exe (3.11 or newer) instead of looking for one
    [string]$From = "",            # a bot moved from another computer (puffpai export): setup asks only what can't travel
    [switch]$NoSetup,              # set everything up, but do not start "puffpai setup" yet
    [string]$WindowFor = "",       # D139: only the window, for a bot on another computer at this address; no bot here
    [switch]$DryRun,               # show every step and every file it would write, and change nothing
    [switch]$Yes,                  # download what is missing without asking
    [switch]$NoTools,              # never download anything; say what is missing instead
    [switch]$Help,
    [string]$WindowName = "PuffPAI window"   # WritersBot: the desktop shortcut's name with -WindowFor
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PyMinMajor = 3
$PyMinMinor = 11                   # pyproject.toml: requires-python = ">=3.11"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RepoDir ".venv"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$Puffpai = Join-Path $VenvDir "Scripts\puffpai.exe"
$InstallLog = Join-Path $RepoDir "install-log.txt"
if (-not $DryRun) { try { Start-Transcript -Path $InstallLog -Force | Out-Null } catch { } }

# Started by "Run with PowerShell", the window would close at once: wait for Enter first.
function Pause-IfAlone {
    if ($env:PUFFPAI_NO_PAUSE -or $DryRun) { return }
    try { if ([Console]::IsInputRedirected) { return } } catch { return }
    Write-Host ("The steps above are also in " + $InstallLog + ".")
    try { [void](Read-Host "Press Enter to close this window") } catch { }
}

$Pretend = @(([string]$env:PUFFPAI_INSTALL_PRETEND_MISSING).ToLower().Split(",") | ForEach-Object { $_.Trim() })
function Pretends ($name) { return ($Pretend -contains $name) }
function Say ($m) { Write-Host $m }
function Step ($n, $m) { Write-Host ""; Write-Host ("Step " + $n + " of 5: " + $m) }
function Fail ($m) {
    Write-Host ""
    Write-Host ("Setup stopped: " + $m)
    try { Stop-Transcript | Out-Null } catch { }
    Pause-IfAlone
    exit 1
}

trap {
    Write-Host ""
    Write-Host ("Setup stopped: " + $_.Exception.Message)
    try { Stop-Transcript | Out-Null } catch { }
    Pause-IfAlone
    exit 1
}

# Run a program; stop with its own words if it fails.
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail ($What + " did not work (code " + $LASTEXITCODE + "). The lines above say why.") }
}

if ($Help) {
    @"
BuildBot setup for Windows.

    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [-Bot NAME] [-NoSetup] [-DryRun]

  -Bot NAME   your bot's name (default buildbot). Its folder is
              %LOCALAPPDATA%\PuffPAI\NAME. A folder already there is kept as it is;
              the new bot gets NAME-2 (or -3, ...).
  -Python EXE use this python.exe (3.11 or newer)
  -NoSetup    do everything but "puffpai setup" (run it yourself later)
  -DryRun     show every step and every file it would write, and change nothing
  -Yes        download what is missing without asking
  -NoTools    never download anything; say what is missing instead
  -From DIR   a bot moved from another computer (puffpai export). Setup asks only what can't travel.
  -WindowFor  only the PuffPAI window, for a bot on another computer: the address "puffpai pair" prints there
  -Help       this text
"@ | Write-Host
    exit 0
}

if ($Bot -notmatch '^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$' -or $Bot.Contains("..")) {
    Fail ("'" + $Bot + "' can't be a bot name. Use letters, numbers, spaces, dots, dashes or underscores.")
}

Say ""
Say "BuildBot setup"
Say "This gets BuildBot ready on this computer. Nothing leaves your computer without your consent."
if ($DryRun) { Say "(Practice run: it shows each step and changes nothing.)" }
# D108: this folder may be one the cloud syncs. It works there, so it never stops the install. Her bot's own data is not
# in it: step 3 puts that under LOCALAPPDATA, which none of these sync.
$SyncedBy = ""
$RepoLow = $RepoDir.ToLower().TrimEnd("\") + "\"
foreach ($Synced in @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial)) {
    if ($Synced -and $RepoLow.StartsWith($Synced.ToLower().TrimEnd("\") + "\")) { $SyncedBy = "OneDrive" }
}
foreach ($Synced in @(@("\onedrive\", "OneDrive"), @("\dropbox\", "Dropbox"), @("\google drive\", "Google Drive"), @("\iclouddrive\", "iCloud Drive"))) {
    if (-not $SyncedBy -and $RepoLow.Contains($Synced[0])) { $SyncedBy = $Synced[1] }
}
if ($SyncedBy) {
    Say ("This folder is synced to the cloud (" + $SyncedBy + "). BuildBot works here, but its programs would sync too (your bot's own data stays in AppData, which does not sync). A folder like C:\PuffPAI is better.")
}

# --- 1. Python ---------------------------------------------------------------
Step 1 "Python, Node.js and Ollama"

# Where Python is (lane I7): the -Python file, or else every python.exe found in these places:
#   the Windows registry (PEP 514: python.org installers and the Python install manager both write it),
#   the install manager's own folders, %LOCALAPPDATA%\Python\pythoncore-3.*-64,
#   python.org's per-user folders, %LOCALAPPDATA%\Programs\Python\Python3*,
#   and a real python.exe on the PATH.
# Never run: the "py" launcher, pymanager, and the Store and install-manager stubs (WindowsApps, Python\bin).
# Those can download and install a Python without asking, so they are not run even to list what is there.
# Each file looked at gets one line saying if it can be used or why not, so install-log.txt explains a miss.
# Read only: nothing here writes the registry or any file.
function Test-Python {
    param([string]$Exe, [int]$MinMinor = 11)
    # -I: ignore PYTHON* settings and the current folder. -B: write no .pyc files into that Python's folder.
    $probe = "import sys; print('%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.executable))"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $code = $null
    try { $out = @(& $Exe -I -B -c $probe 2>$null); $code = $LASTEXITCODE } catch { $out = @() }
    finally { $ErrorActionPreference = $prev }
    if ($null -eq $code) { return [pscustomobject]@{ Ok = $false; Why = "it could not be started" } }
    if ($code -ne 0) { return [pscustomobject]@{ Ok = $false; Why = ("it did not run (code " + $code + ")") } }
    $line = $out | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '^\d+\.\d+\|' } | Select-Object -Last 1
    if (-not $line) { return [pscustomobject]@{ Ok = $false; Why = "it ran but did not say its version" } }
    $parts = $line.Split([char[]]"|", 2)
    $ver = $parts[0]; $nums = $ver.Split([char[]]".")
    if ([int]$nums[0] -ne 3 -or [int]$nums[1] -lt $MinMinor) {
        return [pscustomobject]@{ Ok = $false; Why = ("it is Python " + $ver + ", and BuildBot needs 3." + $MinMinor + " or newer") }
    }
    return [pscustomobject]@{ Ok = $true; Minor = [int]$nums[1]; Version = $ver; Path = $parts[1]; Why = "" }
}

# One candidate, the way Windows will read it: the full path ("/" and ".\" and ".." folded, trailing dots
# and spaces gone, a \\?\ or \\.\ prefix dropped, every part by its long name, every junction or folder link
# above the file followed to where it points). Returns Path (that one real path, or empty
# when there is no such file) and Why (empty when it may be run). Only that Path is ever checked or run, so
# another spelling of the same file can't get past the checks. Never run, by any spelling or copy:
#   py, pyw, pymanager (by name, or by the program's own version info, which a renamed copy keeps),
#   the Microsoft Store stubs (an app link: a reparse point; or anything in WindowsApps),
#   the install manager's shortcuts (Python\bin, or a file whose version info says "Python launcher").
function Remove-PathPrefix ([string]$p) {
    # \\?\C:\x, \\.\C:\x and \??\C:\x are C:\x; \\?\UNC\server\x is \\server\x.
    if ($p -match '^\\\\\?\\UNC\\') { return "\\" + $p.Substring(8) }
    if ($p -match '^(\\\\[?.]\\|\\\?\?\\)') { return $p.Substring(4) }
    return $p
}

# The real path of *Path*: full, each part by its long name, and every junction or folder link on the way
# followed to where it points. Throws when a part is missing or the path can't be read.
function Get-RealPath {
    param([string]$Path, [int]$Depth = 0)
    if ($Depth -gt 16) { throw "too many links" }
    $full = [System.IO.Path]::GetFullPath((Remove-PathPrefix $Path).Replace("/", "\"))
    $root = [System.IO.Path]::GetPathRoot($full)
    $parts = @($full.Substring($root.Length).Split([char[]]"\", [System.StringSplitOptions]::RemoveEmptyEntries))
    $cur = $root.ToUpper()
    for ($i = 0; $i -lt $parts.Count; $i++) {
        $item = Get-Item -LiteralPath (Join-Path $cur $parts[$i]) -Force -ErrorAction Stop
        $next = Join-Path $cur $item.Name                 # the long name, even when given as 8.3
        $isLink = [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        if ($isLink -and $item.PSIsContainer) {
            $to = @($item.Target) | Select-Object -First 1
            if (-not $to) { throw "a folder link with no target" }
            $to = Remove-PathPrefix ([string]$to)
            if (-not [System.IO.Path]::IsPathRooted($to)) { $to = Join-Path $cur $to }
            if ($i + 1 -lt $parts.Count) { $to = Join-Path $to ($parts[($i + 1)..($parts.Count - 1)] -join "\") }
            return Get-RealPath $to ($Depth + 1)
        }
        $cur = $next
    }
    return $cur
}

function Get-PythonFile {
    param([string]$Given)
    $none = [pscustomobject]@{ Path = ""; Why = "the file is not there" }
    $p = ([string]$Given).Trim().Trim('"')
    if (-not $p) { return $none }
    $p = Remove-PathPrefix $p
    try {
        if (-not [System.IO.Path]::IsPathRooted($p)) {
            $p = Join-Path (Get-Location -PSProvider FileSystem).ProviderPath $p
        }
        $full = [System.IO.Path]::GetFullPath($p)
    } catch { return [pscustomobject]@{ Path = ""; Why = "Windows can't read that path as a file" } }
    try { $real = Get-RealPath $full } catch { return $none }
    try { $item = Get-Item -LiteralPath $real -Force -ErrorAction Stop } catch { return $none }
    if ($item.PSIsContainer) { return [pscustomobject]@{ Path = ""; Why = "it is a folder, not a python.exe" } }
    $path = $real
    $leaf = $item.Name
    $why = ""
    $ver = $null
    try { $ver = $item.VersionInfo } catch { }
    $orig = ""
    if ($ver -and $ver.OriginalFilename) { $orig = ([string]$ver.OriginalFilename).ToLower() -replace '\.mui$', '' }
    $desc = ""
    if ($ver -and $ver.FileDescription) { $desc = [string]$ver.FileDescription }
    # (Version info "py.exe" alone is not refused: a venv's own python.exe says that too.)
    if ($leaf -match '^(py|pyw|pymanager|pywmanager)\.exe$' -or $orig -match '^(pymanager|pywmanager)\.exe$') {
        $why = "the py launcher or pymanager is never run (it can install a Python by itself)"
    } elseif (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $path -match '\\WindowsApps\\') {
        $why = "the Microsoft Store stub (an app link) is never run (it can install a Python by itself)"
    } elseif ($path -match '\\Python\\bin\\' -or $orig -match '^launcher' -or $desc -match 'launcher') {
        $why = "the install manager's shortcuts are never run (they can install a Python by themselves)"
    } elseif ($leaf -notmatch '^python(3(\.\d+)?)?\.exe$') {
        $why = "it is not a python.exe"
    }
    return [pscustomobject]@{ Path = $path; Why = $why }
}

# The python.exe each PEP 514 key names, read only. *Roots* are registry paths. A root that ends in ".json" is a
# stand-in for the registry, for the tests only, so they never read or write the real one.
function Get-RegistryPythons {
    param([string[]]$Roots)
    foreach ($root in @($Roots)) {
        if (-not $root) { continue }
        $short = ($root -replace '^(HK[A-Z]+):.*?(\\WOW6432Node)?\\Python\\PythonCore$', '$1$2')
        if ($root -like "*.json") {
            $view = $null
            try { $view = Get-Content -LiteralPath $root -Raw | ConvertFrom-Json } catch { }
            if (-not $view) { continue }
            foreach ($tag in @($view.PSObject.Properties)) {
                $ip = $tag.Value.InstallPath
                $exe = $null; $dir = $null
                if ($ip) { $exe = $ip.ExecutablePath; $dir = $ip.'(default)' }
                [pscustomobject]@{ From = ("registry " + $short + " " + $tag.Name); Exe = $exe; Dir = $dir }
            }
            continue
        }
        $keys = @()
        try { if (Test-Path -LiteralPath $root) { $keys = @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue) } } catch { }
        foreach ($key in $keys) {
            $exe = $null; $dir = $null
            try {
                $ip = $key.OpenSubKey("InstallPath")        # opened for reading only
                if ($ip) { $exe = $ip.GetValue("ExecutablePath"); $dir = $ip.GetValue(""); $ip.Close() }
            } catch { }
            [pscustomobject]@{ From = ("registry " + $short + " " + $key.PSChildName); Exe = $exe; Dir = $dir }
        }
    }
}

function Find-Python {
    param(
        [string]$Wanted = "",
        [string[]]$RegistryRoots = @("HKCU:\Software\Python\PythonCore", "HKLM:\Software\Python\PythonCore",
                                     "HKLM:\Software\WOW6432Node\Python\PythonCore"),
        [string]$LocalAppData = $env:LOCALAPPDATA,
        [int]$MinMinor = 11
    )
    $cands = New-Object System.Collections.ArrayList
    if ($Wanted) {
        [void]$cands.Add(@("-Python", $Wanted))
    } else {
        foreach ($r in @(Get-RegistryPythons $RegistryRoots)) {
            $exe = [string]$r.Exe
            if (-not $exe -and $r.Dir) { try { $exe = Join-Path ([string]$r.Dir) "python.exe" } catch { $exe = "" } }
            if (-not $exe) { Say ("  Python: " + $r.From + " names no python.exe, skipped."); continue }
            [void]$cands.Add(@($r.From, $exe))
        }
        if ($LocalAppData) {
            foreach ($where in @(@("Python", "pythoncore-3.*-64"), @("Programs\Python", "Python3*"))) {
                $base = Join-Path $LocalAppData $where[0]
                if (-not (Test-Path -LiteralPath $base -PathType Container)) { continue }
                foreach ($d in @(Get-ChildItem -LiteralPath $base -Directory -Filter $where[1] -ErrorAction SilentlyContinue | Sort-Object Name)) {
                    [void]$cands.Add(@(("folder " + $where[0] + "\" + $d.Name), (Join-Path $d.FullName "python.exe")))
                }
            }
        }
        foreach ($name in @("python", "python3")) {
            foreach ($c in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
                if ($c.Source) { [void]$cands.Add(@("PATH", $c.Source)) }
            }
        }
    }
    $seen = @{}
    $best = $null
    foreach ($c in $cands) {
        $from = $c[0]; $given = [string]$c[1]
        $tell = "  Python: " + $given + " (" + $from + "): "
        if ($from -eq "-Python" -and $given -notmatch '[\\/]') {
            Say ($tell + "not used. Give the full path to python.exe, for example -Python C:\Users\you\AppData\Local\Python\pythoncore-3.11-64\python.exe.")
            continue
        }
        $f = Get-PythonFile $given
        if (-not $f.Path) { Say ($tell + "not used, " + $f.Why + "."); continue }
        $exe = $f.Path                                # the one real path: checked below, and the only one run
        if ($exe -ne $given) { $tell = "  Python: " + $exe + " (" + $from + ", given as " + $given + "): " }
        if ($seen.ContainsKey($exe)) { Say ($tell + "same file as above."); continue }
        $seen[$exe] = $true
        if ($f.Why) { Say ($tell + "not used, " + $f.Why + "."); continue }
        $t = Test-Python $exe $MinMinor
        if (-not $t.Ok) { Say ($tell + "not used, " + $t.Why + "."); continue }
        Say ($tell + "Python " + $t.Version + ", can be used.")
        # 3.11 first (what BuildBot is tested on), then the lowest newer one; the first found wins a tie.
        if (-not $best -or $t.Minor -lt $best.Minor) { $best = $t }
    }
    if (-not $best) { return $null }
    return [pscustomobject]@{ Minor = $best.Minor; Path = $best.Path; Version = $best.Version }
}

$Found = Find-Python -Wanted $Python -MinMinor $PyMinMinor
if ($Found -and -not $Python -and (Pretends "python")) { $Found = $null }

# D145: what is missing can come from its maker's own website, after one yes, as a plain copy in a tools folder of its
# own. Each file has one fixed version and the SHA-256 written here, checked before anything is unpacked. No installer, no
# admin prompt, no registry, nothing on the PATH of this computer: only the launchers below use them. A tool already on this
# computer is used as it is. The same versions as scripts/install_tools.sh. Python: python-build-standalone 20261003
# (SHA256SUMS). Node.js 24.21.0 (SHASUMS256.txt). Ollama 0.40.2 (sha256sum.txt). To move a pin: change them together.
$ToolArch = if (($env:PROCESSOR_ARCHITEW6432, $env:PROCESSOR_ARCHITECTURE) -contains "ARM64") { "arm64" } else { "x64" }
$ToolPy = "3.12.15"; $ToolPyBuild = "20261003"; $ToolNode = "24.21.0"; $ToolOllama = "0.40.2"
$ToolPins = @{
    "python-x64"   = @("x86_64", "4b6f0beebbb695a0f3ea237b8c3eaa5bd424f47a7bc25b2fbe3a43390c770f08")
    "python-arm64" = @("aarch64", "2de96a340fecdabe215094345ed3aea1b473e858bd098360f2253299e18f24d2")
    "node-x64"     = @("win-x64", "158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541")
    "node-arm64"   = @("win-arm64", "8779b1bde1d39f8d420e3b57aa657b39891af434d3de44a919044cec06785921")
    "ollama-x64"   = @("amd64", "e29ad1d5063dd4b54b2492d9b00adab2cff9621bfa654b6c92aa8d6f1fdfe7fc")
    "ollama-arm64" = @("arm64", "82ad9bff6dd5c1c70400ceac00d01aaa0389e9182b98d918d1d164ebf87acdb2")
}
$ToolSizes = @{ "ollama-x64" = "1.4 GB"; "ollama-arm64" = "200 MB" }
$PyPin = $ToolPins["python-" + $ToolArch]; $NodePin = $ToolPins["node-" + $ToolArch]; $OllamaPin = $ToolPins["ollama-" + $ToolArch]
$ToolsDir = $env:PUFFPAI_TOOLS_DIR
if (-not $ToolsDir) {
    $ToolsBase = $env:LOCALAPPDATA
    if (-not $ToolsBase) { $ToolsBase = Join-Path $env:USERPROFILE "AppData\Local" }
    $ToolsDir = Join-Path $ToolsBase "PuffPAI-tools"
}
$OwnPyHome = Join-Path $ToolsDir ("python-" + $ToolPy + "-" + $ToolPyBuild)
$OwnNodeHome = Join-Path $ToolsDir ("node-v" + $ToolNode + "-" + $NodePin[0])
$OwnOllamaHome = Join-Path $ToolsDir ("ollama-" + $ToolOllama)

function Test-OllamaAnswers {
    foreach ($port in @(11434, 11435)) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri ("http://127.0.0.1:" + $port + "/api/version") | Out-Null; return $true } catch { }
    }
    return $false
}

# One download: fetched into a new folder inside the tools folder, checked, unpacked, moved into place. Any failure leaves
# nothing behind, says why in plain words and returns $false, so setup can go on or stop as it should.
function Install-Tool {
    param([string]$Label, [string]$Url, [string]$Sha, [string]$File, [string]$Dest, [string]$Inner)
    if ($DryRun) {
        Say ("  [dry-run] would download " + $Label + " from " + $Url)
        Say ("  [dry-run] check it is exactly " + $Sha + ", and unpack it to " + $Dest)
        return $true
    }
    Say ("  Downloading " + $Label + ".")
    $stage = Join-Path $ToolsDir (".download-" + $PID + "-" + (Get-Random))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    try {
        $out = Join-Path $stage $File
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 7200 -Uri $Url -OutFile $out
        } catch {
            Say "  The download did not work. Check this computer is online, then run setup again."
            return $false
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $out).Hash.ToLower() -ne $Sha) {
            Say "  The download did not match the copy BuildBot expects, so it was thrown away and nothing was run."
            return $false
        }
        $x = Join-Path $stage "x"
        New-Item -ItemType Directory -Force -Path $x | Out-Null
        & (Join-Path $env:SystemRoot "System32\tar.exe") -xf $out -C $x
        if ($LASTEXITCODE -ne 0) { Say ("  " + $Label + " could not be unpacked."); return $false }
        $from = if ($Inner) { Join-Path $x $Inner } else { $x }
        if (-not (Test-Path -LiteralPath $from)) { Say ("  " + $Label + " did not hold what it should."); return $false }
        if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
        Move-Item -LiteralPath $from -Destination $Dest
        return $true
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# What is here. A copy an earlier run of this setup downloaded counts, whatever the test hook says.
$OwnPy = Join-Path $OwnPyHome "python.exe"
if (-not $Found -and -not $Python -and (Test-Path -LiteralPath $OwnPy)) {
    $t = Test-Python $OwnPy $PyMinMinor
    if ($t.Ok) { $Found = [pscustomobject]@{ Minor = $t.Minor; Path = $OwnPy; Version = $t.Version } }
}
$NodeHome = ""
if (Test-Path -LiteralPath (Join-Path $OwnNodeHome "node.exe")) { $NodeHome = $OwnNodeHome }
$NeedNode = (-not $NodeHome) -and ((Pretends "node") -or -not (Get-Command node.exe -ErrorAction SilentlyContinue))
$OllamaExe = ""
if (Test-Path -LiteralPath (Join-Path $OwnOllamaHome "ollama.exe")) { $OllamaExe = Join-Path $OwnOllamaHome "ollama.exe" }
$NeedOllama = $false
if (-not $OllamaExe) {
    if (Pretends "ollama") { $NeedOllama = $true }
    elseif (Test-OllamaAnswers) { Say "  Ollama is already running here. BuildBot uses it as it is." }
    elseif ((Get-Command ollama.exe -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"))) {
        Say "  Found Ollama. Open it once before you start your bot, so it is running."
    } else { $NeedOllama = $true }
}
if ($WindowFor) { $NeedNode = $false; $NeedOllama = $false }      # the window needs only Python
if (-not $NeedNode -and -not $WindowFor) { Say "  Found Node.js." }

$OfferPy = (-not $Found) -and (-not $Python); $OfferNode = $NeedNode; $OfferOllama = $NeedOllama
$ToolsPlace = ([string]$ToolsDir).ToLower().TrimEnd("\") + "\"
if ($NoTools -or $ToolsPlace.StartsWith($RepoDir.ToLower().TrimEnd("\") + "\")) { $OfferPy = $false; $OfferNode = $false; $OfferOllama = $false }
$GoTools = $false
if ($OfferPy -or $OfferNode -or $OfferOllama) {
    Say "  This computer doesn't have:"
    if ($OfferPy) { Say ("    - Python " + $ToolPy + " (about 50 MB), which BuildBot is written in") }
    if ($OfferNode) { Say ("    - Node.js " + $ToolNode + " (about 40 MB), which many apps need") }
    if ($OfferOllama) { Say ("    - Ollama " + $ToolOllama + " (about " + $ToolSizes["ollama-" + $ToolArch] + "), which runs the AI model") }
    Say "  Setup can download them from their makers' own websites, check that each one"
    Say "  is exactly the copy BuildBot expects, and keep them in this folder:"
    Say ("    " + $ToolsDir)
    Say "  It doesn't need an administrator, and it changes nothing else on this computer."
    if ($Yes) { $GoTools = $true }
    elseif ($DryRun) { Say "  [dry-run] would ask: Download them now? (y/n)"; $GoTools = $true }
    else {
        $answer = ""
        try { if (-not [Console]::IsInputRedirected) { $answer = Read-Host "  Download them now? (y/n)" } } catch { }
        if ($answer -match '^[Yy]') { $GoTools = $true } else { Say "  Nothing was downloaded." }
    }
}
if ($GoTools) {
    if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null }
    if ($OfferPy) {
        $tag = "cpython-" + $ToolPy + "+" + $ToolPyBuild + "-" + $PyPin[0] + "-pc-windows-msvc-install_only.tar.gz"
        $url = "https://github.com/astral-sh/python-build-standalone/releases/download/" + $ToolPyBuild + "/" + $tag.Replace("+", "%2B")
        if (Install-Tool ("Python " + $ToolPy) $url $PyPin[1] "python.tar.gz" $OwnPyHome "python") {
            if ($DryRun) { $Found = [pscustomobject]@{ Minor = 12; Path = $OwnPy; Version = $ToolPy } }
            else {
                $t = Test-Python $OwnPy $PyMinMinor
                if ($t.Ok) { $Found = [pscustomobject]@{ Minor = $t.Minor; Path = $OwnPy; Version = $t.Version } }
                else { Say ("  The downloaded Python did not run: " + $t.Why + ".") }
            }
        }
    }
    if ($OfferNode) {
        $url = "https://nodejs.org/dist/v" + $ToolNode + "/node-v" + $ToolNode + "-" + $NodePin[0] + ".zip"
        if (Install-Tool ("Node.js " + $ToolNode) $url $NodePin[1] "node.zip" $OwnNodeHome ("node-v" + $ToolNode + "-" + $NodePin[0])) { $NodeHome = $OwnNodeHome; $NeedNode = $false }
        else { Say "  Node.js could not be set up. BuildBot works without it, but apps that need it won't start." }
    }
    if ($OfferOllama) {
        $url = "https://github.com/ollama/ollama/releases/download/v" + $ToolOllama + "/ollama-windows-" + $OllamaPin[0] + ".zip"
        if (Install-Tool ("Ollama " + $ToolOllama) $url $OllamaPin[1] "ollama.zip" $OwnOllamaHome "") { $OllamaExe = Join-Path $OwnOllamaHome "ollama.exe"; $NeedOllama = $false }
        else { Say "  Ollama could not be set up. Setup goes on, but your bot needs Ollama to answer." }
    }
}
if ($NeedNode) { Say "  Node.js isn't here. BuildBot works without it, but apps that need it won't start. Get it from https://nodejs.org/" }
if ($NeedOllama) { Say "  Ollama isn't here, and your bot needs it to think. Get it from https://ollama.com/download" }
if ($NodeHome) { $env:PATH = $NodeHome + ";" + $env:PATH }
if (-not $Found) {
    if ($Python -and $Python -notmatch '[\\/]') { Fail "Give the full path to python.exe with -Python, not just its name." }
    if ($Python) { Fail ("'" + $Python + "' is not Python $PyMinMajor.$PyMinMinor or newer that setup can use. The line above says why.") }
    Say "  Python $PyMinMajor.$PyMinMinor or newer is not on this computer, and BuildBot needs it."
    Say ""
    Say "  Your turn: run this setup again and let it download Python,"
    Say "  or get it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and open a NEW PowerShell window,"
    Say "  or run this again with -Python <full path to python.exe>."
    Fail "BuildBot needs Python $PyMinMajor.$PyMinMinor or newer."
}
$Python = $Found.Path
Say ("  Found Python " + $Found.Version + " (" + $Python + ").")

# --- 2. .venv and BuildBot's own parts ------------------------------------------
Step 2 "BuildBot's own parts"
if ($DryRun) {
    Say ("  [dry-run] would make " + $VenvDir + " with " + $Python + ", and install BuildBot into it.")
    if ($WindowFor) {
        Say ("  [dry-run] would write the window's one launcher, for " + $WindowFor + ". No bot here, and no setup.")
        Say "  Practice run done. Nothing was changed."
        exit 0
    }
} else {
if (Test-Path -LiteralPath $VenvPy) {
    Say "  Using the .venv folder from last time."
} else {
    Say "  Making .venv (BuildBot's own Python, next to this file)."
    Invoke-Checked $Python @("-m", "venv", $VenvDir) "Making .venv"
}
Say "  Getting BuildBot's parts from the Python package library. This takes a minute or two."
Invoke-Checked $VenvPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "--upgrade", "pip") "Updating pip"
Invoke-Checked $VenvPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", $RepoDir) "Installing BuildBot"
if (-not (Test-Path -LiteralPath $Puffpai)) { Fail "the puffpai command was not made in .venv." }
Say "  Done."
}

# D139: only the window, for a bot on another computer. No bot folder, no setup. It pairs once, on the window's own screen.
if ($WindowFor) {
    if ($WindowFor -notmatch '^[A-Za-z0-9.:/-]+$') { Fail "That is not an address this window can use. Give the one 'puffpai pair' prints on the bot's computer." }
    $WindowOnly = Join-Path $RepoDir "Open BuildBot window on another computer.cmd"
    Set-Content -LiteralPath $WindowOnly -Encoding ASCII -Value @(
        "@echo off",
        "rem Opens the PuffPAI window for the bot at $WindowFor. Written by install.ps1.",
        """%~dp0.venv\Scripts\puffpai.exe"" app --remote $WindowFor",
        "if errorlevel 1 pause"
    )
    # WritersBot: a desktop shortcut too, so the window opens with a double-click and never from a terminal.
    try {
        $Desk = [Environment]::GetFolderPath("Desktop")
        $Lnk = Join-Path $Desk ($WindowName + ".lnk")
        $Ws = New-Object -ComObject WScript.Shell
        $Sc = $Ws.CreateShortcut($Lnk)
        $Sc.TargetPath = $Puffpai                      # the program itself: a shortcut to the .cmd opened its folder on one PC
        $Sc.Arguments = "app --remote " + $WindowFor
        $Sc.WorkingDirectory = $RepoDir
        $Sc.WindowStyle = 7                            # the console it starts from stays minimized
        $Ico = Join-Path $RepoDir "core\puffpai_core\puff.ico"
        if (Test-Path -LiteralPath $Ico) { $Sc.IconLocation = $Ico }
        $Sc.Description = "Opens the PuffPAI window for the bot at " + $WindowFor
        $Sc.Save()
        Say ("  Shortcut on your desktop: " + $WindowName)
    } catch { Say "  (No desktop shortcut was made; the launcher below still works.)" }
    Say ""
    Say ("  Written: " + $WindowOnly)
    Say "  Double-click it to open the window. The first time, type the sign-in code from the bot's computer."
    try { Stop-Transcript | Out-Null } catch { }
    & $Puffpai app --remote $WindowFor
    exit $LASTEXITCODE
}

# --- 3. Your bot's own folder ---------------------------------------------------
Step 3 "Your bot's own folder"
$Base = $env:LOCALAPPDATA
if (-not $Base) { $Base = Join-Path $env:USERPROFILE "AppData\Local" }
$Home_ = Join-Path $Base "PuffPAI"
$Name = $Bot
$n = 2
while (Test-Path -LiteralPath (Join-Path $Home_ $Name)) { $Name = $Bot + "-" + $n; $n++ }
$BotDir = Join-Path $Home_ $Name
if ($Name -ne $Bot) {
    Say ("  A bot named " + $Bot + " is already on this computer. It is kept exactly as it is.")
    Say ("  This new bot is called " + $Name + ".")
}
if ($DryRun) {
    Say ("  [dry-run] would make your bot's folder: " + $BotDir)
} else {
    New-Item -ItemType Directory -Path $BotDir -Force | Out-Null
    Say ("  Your bot's folder: " + $BotDir)
}
$env:PUFFPAI_BOT = $Name
Remove-Item Env:\PUFFPAI_STATE -ErrorAction SilentlyContinue

# --- 4. The Start BuildBot launcher ----------------------------------------------
Step 4 "The Start, Stop, Chat and Sign in launchers"
# D129: one folder of code can hold any number of bots. The first keeps its launchers next to this file; each other bot
# gets the same names in bots\<name>\, so no install overwrites another bot's Start BuildBot.cmd.
function Get-LauncherDir ([string]$Repo, [string]$BotName) {
    $first = Join-Path $Repo "Start BuildBot.cmd"
    if (-not (Test-Path -LiteralPath $first)) { return $Repo }
    $owner = Select-String -LiteralPath $first -Pattern 'set "PUFFPAI_BOT=(.*)"' | Select-Object -First 1
    if (-not $owner -or $owner.Matches[0].Groups[1].Value -eq $BotName) { return $Repo }
    return (Join-Path (Join-Path $Repo "bots") $BotName)
}
# The launchers below are written for the folder $RepoDir names, next to the code. In bots\<name>\ the code is two folders
# up: Set-LauncherLines says so, once they are all written.
function Set-LauncherLines ([string[]]$Lines) {
    return ($Lines | ForEach-Object { $_.Replace('%~dp0.venv', '%~dp0..\..\.venv').Replace('cd /d "%~dp0"', 'cd /d "%~dp0..\..\"') })
}
$LauncherDir = Get-LauncherDir $RepoDir $Name
if ($DryRun) {
    foreach ($written in @("Start BuildBot.cmd", "Stop BuildBot.cmd", "Chat with BuildBot.cmd", "Sign in a browser.cmd", "Open BuildBot window.cmd")) {
        Say ("  [dry-run] would write " + (Join-Path $LauncherDir $written))
    }
    Step 5 "The system parts, then setup"
    Say ("  [dry-run] would run: " + $Puffpai + " setup")
    Say "  Practice run done. Nothing was changed."
    exit 0
}
New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null
# D145: a downloaded Node.js goes first on the PATH of what these start, and a downloaded Ollama, which has no service,
# starts on this computer only when nothing answers there. Stop BuildBot.cmd leaves Ollama running.
$OllamaStart = "`$env:OLLAMA_HOST='127.0.0.1:11434'; Start-Process -WindowStyle Hidden -FilePath '" + $OllamaExe.Replace("'", "''") + "' -ArgumentList 'serve'"
$ToolLines = @()
if ($NodeHome) { $ToolLines += "set ""PATH=" + $NodeHome + ";%PATH%""" }
if ($OllamaExe) { $ToolLines += "curl.exe -fsS --max-time 2 http://127.0.0.1:11434/api/version >nul 2>&1 || powershell -NoProfile -WindowStyle Hidden -Command """ + $OllamaStart + """" }
function Add-ToolLines ([string[]]$Lines) {
    return ($Lines | ForEach-Object { $_; if ($_ -eq 'set "PUFFPAI_STATE="') { $ToolLines } })
}
$CodeDir = $RepoDir
$RepoDir = $LauncherDir
$Launcher = Join-Path $RepoDir "Start BuildBot.cmd"
# Ctrl+C reaches cmd.exe too, and cmd then asks "Terminate batch job (Y/N)?" on its own output. So the
# file runs itself once more with its input from NUL (the question answers itself) and its output to NUL
# (the question is never shown), while BuildBot and the lines below write straight to the window (>con).
# D94: "puffpai start" starts BuildBot once, in its own window (never a second one for this bot), then opens
# the PuffPAI window, the default (or the chat here, or the dashboard, if she picked that; if the window can't
# open on this computer, one plain line and the chat). The chat ends with 0 after a Ctrl+C; any other code
# pauses, so she can read what went wrong. The codes are compared as text, so a crash code below zero
# (0xC0000005 is -1073741819) pauses too; no parenthesized block, since cmd reads %errorlevel% in a block
# before the block runs.
$cmd = @(
    "@echo off",
    "rem Starts BuildBot ($Name). Written by install.ps1.",
    "if ""%~1""==""--run"" goto run",
    "call ""%~f0"" --run <nul >nul",
    "set ""BUILDBOT_EXIT=%errorlevel%""",
    "if not ""%BUILDBOT_EXIT%""==""0"" pause",
    "exit /b %BUILDBOT_EXIT%",
    ":run",
    "set ""PUFFPAI_BOT=$Name""",
    "set ""PUFFPAI_STATE=""",
    "cd /d ""%~dp0""",
    ">con echo BuildBot runs in its own window. To stop it, double-click Stop BuildBot.cmd next to this file.",
    ">con echo To open another chat, double-click Chat with BuildBot.cmd next to this file.",
    ">con echo To sign in another browser later, double-click Sign in a browser.cmd next to this file.",
    """%~dp0.venv\Scripts\puffpai.exe"" start <con >con"
)
Set-Content -LiteralPath $Launcher -Value (Add-ToolLines $cmd) -Encoding ASCII
Say ("  Written: " + $Launcher)
# Stop BuildBot.cmd (lane I7): "puffpai shutdown" for the same bot, from any window. It asks the running Core
# to stop the way Ctrl+C does. Any code but 0 pauses, so she can read why (compared as text, so a crash code
# below zero pauses too). setlocal: run from a command window, it leaves that window's settings alone.
$StopLauncher = Join-Path $RepoDir "Stop BuildBot.cmd"
$stopCmd = @(
    "@echo off",
    "rem Stops BuildBot ($Name). Written by install.ps1.",
    "setlocal",
    "set ""PUFFPAI_BOT=$Name""",
    "set ""PUFFPAI_STATE=""",
    "cd /d ""%~dp0""",
    """%~dp0.venv\Scripts\puffpai.exe"" shutdown",
    "set ""BUILDBOT_EXIT=%errorlevel%""",
    "if not ""%BUILDBOT_EXIT%""==""0"" pause",
    "exit /b %BUILDBOT_EXIT%"
)
Set-Content -LiteralPath $StopLauncher -Value $stopCmd -Encoding ASCII
Say ("  Written: " + $StopLauncher)
# Chat with BuildBot.cmd (D86): "puffpai" with no words, the chat with the running bot, in its own window.
# Like the Start launcher, it runs itself once more with its input from NUL, so cmd's "Terminate batch job
# (Y/N)?" after a Ctrl+C answers itself unseen, while the chat reads her keys from this window (<con) and
# writes to it (>con). It never starts BuildBot; any code but 0 pauses, so she can read why (BuildBot not
# running says how to start it).
$ChatLauncher = Join-Path $RepoDir "Chat with BuildBot.cmd"
$chatCmd = @(
    "@echo off",
    "rem Chat with BuildBot ($Name) in this window. Written by install.ps1.",
    "if ""%~1""==""--run"" goto run",
    "call ""%~f0"" --run <nul >nul",
    "set ""BUILDBOT_EXIT=%errorlevel%""",
    "if not ""%BUILDBOT_EXIT%""==""0"" pause",
    "exit /b %BUILDBOT_EXIT%",
    ":run",
    "set ""PUFFPAI_BOT=$Name""",
    "set ""PUFFPAI_STATE=""",
    "cd /d ""%~dp0""",
    """%~dp0.venv\Scripts\puffpai.exe"" <con >con"
)
Set-Content -LiteralPath $ChatLauncher -Value $chatCmd -Encoding ASCII
Say ("  Written: " + $ChatLauncher)
# Sign in a browser.cmd (D92): "puffpai pair --running" for the same bot, a new one-time sign-in link at any
# time (the one Start BuildBot.cmd shows works for 10 minutes only). It never starts BuildBot: with no bot
# running, it says so and how to start it. It always pauses, so the window stays open to copy the link.
$SignInLauncher = Join-Path $RepoDir "Sign in a browser.cmd"
$signInCmd = @(
    "@echo off",
    "rem Signs a browser in to BuildBot ($Name). Written by install.ps1.",
    "setlocal",
    "set ""PUFFPAI_BOT=$Name""",
    "set ""PUFFPAI_STATE=""",
    "cd /d ""%~dp0""",
    """%~dp0.venv\Scripts\puffpai.exe"" pair --running",
    "set ""BUILDBOT_EXIT=%errorlevel%""",
    "if ""%BUILDBOT_EXIT%""==""0"" echo Open the link above in your browser on this computer. For a new link, double-click this file again.",
    "pause",
    "exit /b %BUILDBOT_EXIT%"
)
Set-Content -LiteralPath $SignInLauncher -Value $signInCmd -Encoding ASCII
Say ("  Written: " + $SignInLauncher)
# Open BuildBot window.cmd (D81): "puffpai app" for the same bot, the PuffPAI window. It starts BuildBot once if it
# is not running (the same start as "puffpai start"), and the window runs on its own, so this ends at once. Any
# code but 0 pauses, so she can read why (compared as text, so a crash code below zero pauses too).
$WindowLauncher = Join-Path $RepoDir "Open BuildBot window.cmd"
$windowCmd = @(
    "@echo off",
    "rem Opens the PuffPAI window for BuildBot ($Name). Written by install.ps1.",
    "setlocal",
    "set ""PUFFPAI_BOT=$Name""",
    "set ""PUFFPAI_STATE=""",
    "cd /d ""%~dp0""",
    """%~dp0.venv\Scripts\puffpai.exe"" app",
    "set ""BUILDBOT_EXIT=%errorlevel%""",
    "if not ""%BUILDBOT_EXIT%""==""0"" pause",
    "exit /b %BUILDBOT_EXIT%"
)
Set-Content -LiteralPath $WindowLauncher -Value (Add-ToolLines $windowCmd) -Encoding ASCII
if ($LauncherDir -ne $CodeDir) {
    foreach ($written in @($Launcher, $StopLauncher, $ChatLauncher, $SignInLauncher, $WindowLauncher)) {
        Set-Content -LiteralPath $written -Value (Set-LauncherLines (Get-Content -LiteralPath $written)) -Encoding ASCII
    }
}
$RepoDir = $CodeDir
Say ("  Written: " + $WindowLauncher)
Say "  Next time, double-click Start BuildBot.cmd: it starts your bot in its own window and opens the PuffPAI window."
Say "  Open BuildBot window.cmd opens that window again; Chat with BuildBot.cmd is the chat in a terminal."
Say "  Stop BuildBot.cmd stops it. Chat with BuildBot.cmd opens another chat while it runs."
Say "  Sign in a browser.cmd makes a new link that signs a browser in to your Desktop."

# --- 5. The built-in packages and the setup steps --------------------------------------
Step 5 "The system parts, then setup"
if ($NoSetup) {
    Say "  Skipped for now (-NoSetup). When you are ready, run:"
    Say ("      `$env:PUFFPAI_BOT = '" + $Name + "'; & '" + $Puffpai + "' setup")
    Say "  Then Start BuildBot.cmd starts your bot, and Stop BuildBot.cmd stops it."
    try { Stop-Transcript | Out-Null } catch { }
    exit 0
}
Say "  Each system part (the Desktop, setup, the cloud avatar) shows its Yes card first. Then setup asks a few questions."
try { Stop-Transcript | Out-Null } catch { }     # setup shows PINs and a recovery code: never in the log
if ($OllamaExe -and -not (Test-OllamaAnswers)) { Invoke-Expression $OllamaStart }
if ($From) { & $Puffpai setup --from $From } else { & $Puffpai setup }
$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) { Write-Host "BuildBot is set up. It is stopped now." } else { Write-Host "Setup is not finished yet. Run Start BuildBot.cmd, then open Setup in the Desktop, or run puffpai setup again." }
Write-Host ("To start BuildBot again, double-click Start BuildBot.cmd in " + $LauncherDir + ".")
Write-Host "To stop it from any window, double-click Stop BuildBot.cmd there."
Write-Host "To chat with it in a terminal while it runs, double-click Chat with BuildBot.cmd there."
Write-Host "To sign in a browser while it runs, double-click Sign in a browser.cmd there."
Pause-IfAlone
exit $code
