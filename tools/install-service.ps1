# Registers the scheduled task "Chess Companion": starts the backend supervisor when Windows starts
# (no login needed, S4U), and restarts the task if it ever stops. Run once in PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\chess-companion\tools\install-service.ps1
# Without admin rights it falls back to "at log on" for the current user.
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = Join-Path (Split-Path (Get-Command python.exe).Source) 'pythonw.exe' }
$action = New-ScheduledTaskAction -Execute $py -Argument '"C:\chess-companion\tools\supervise.py"' -WorkingDirectory 'C:\chess-companion'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$user = "$env:USERDOMAIN\$env:USERNAME"
try {
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
  Register-ScheduledTask -TaskName 'Chess Companion' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
  Write-Output 'Registered: starts at boot (no login needed).'
} catch {
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  Register-ScheduledTask -TaskName 'Chess Companion' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Output 'Registered: starts when you log on (run as Administrator for start-at-boot).'
}
Start-ScheduledTask -TaskName 'Chess Companion'
