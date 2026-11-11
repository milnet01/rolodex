# One update cycle's keyboard work, run in the signed-in session by runner.ps1 (ROLO-0042
# section 4.7). Settings come from job.json beside this file:
#   exe          the Rolodex .exe under test
#   launch       true: open it as a double-click does (Explorer ShellExecute); false: drive the
#                instance already running (cycle 2: the one the update helper relaunched)
#   password     the throwaway test vault's password (alphanumeric, so SendKeys needs no escape)
#   offer_wait_s seconds from unlock to the update offer (startup check: 3 s timer + fetch)
#   offer_keys   SendKeys string that picks Update Now
# GTK 4 has no UI Automation backend on Windows, so this sends keys with SendKeys (SendInput).
$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$job = Get-Content -Raw -LiteralPath (Join-Path $base 'job.json') | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class RoloWin {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
'@

# Write-Host, not output: a function's output is its return value, so a logged line inside
# Focus made its result an always-true array. runner.ps1's *>&1 still captures the host stream.
function Say($msg) { Write-Host ('{0:HH:mm:ss.fff} {1}' -f (Get-Date), $msg) }

function Window {
    # The GUI is the one-file build's child process; only it has a main window.
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $job.exe -and $_.MainWindowHandle -ne [IntPtr]::Zero } |
        Select-Object -First 1
}

function WaitWindow($notHandle, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        $p = Window
        if ($p -and $p.MainWindowHandle -ne $notHandle) { return $p }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Focus($handle) {
    # An ALT tap first: Windows lets a process take the foreground only after input.
    [RoloWin]::ShowWindow($handle, 9) | Out-Null
    [RoloWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
    [RoloWin]::SetForegroundWindow($handle) | Out-Null
    [RoloWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 400
    $ok = [RoloWin]::GetForegroundWindow() -eq $handle
    Say "focus $handle -> foreground=$ok"
    return $ok
}

if ($job.launch) {
    $folder = Split-Path -Parent $job.exe
    Start-Process explorer.exe -ArgumentList "`"$folder`""
    $shellWin = $null
    for ($i = 0; $i -lt 40 -and -not $shellWin; $i++) {
        Start-Sleep -Milliseconds 250
        $shellWin = (New-Object -ComObject Shell.Application).Windows() |
            Where-Object { try { $_.Document.Folder.Self.Path -eq $folder } catch { $false } } |
            Select-Object -First 1
    }
    if (-not $shellWin) { Say 'FAIL: no Explorer window for the folder'; return }
    $shellWin.Document.Application.ShellExecute($job.exe)
    Say 'launched through Explorer ShellExecute'
    $shellWin.Quit()
}

$unlock = WaitWindow ([IntPtr]::Zero) 90
if (-not $unlock) { Say 'FAIL: no Rolodex window'; return }
Say "unlock window $($unlock.MainWindowHandle) pid $($unlock.Id)"
Start-Sleep -Seconds 2
if (-not (Focus $unlock.MainWindowHandle)) { Say 'FAIL: could not focus the unlock window'; return }
[System.Windows.Forms.SendKeys]::SendWait($job.password + '{ENTER}')
Say 'typed the password and Enter'

$main = WaitWindow $unlock.MainWindowHandle 60
if (-not $main) { Say 'FAIL: no main window after unlock'; return }
Say "main window $($main.MainWindowHandle)"
Start-Sleep -Seconds $job.offer_wait_s
if (-not (Focus $main.MainWindowHandle)) { Say 'FAIL: could not focus the main window'; return }
[System.Windows.Forms.SendKeys]::SendWait($job.offer_keys)
Say "sent offer keys $($job.offer_keys)"
