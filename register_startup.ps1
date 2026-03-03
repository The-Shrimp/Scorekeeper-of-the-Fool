# register_startup.ps1
# Run this ONCE (as your normal user, not admin) to register the bot as a startup task.
# After running: the bot will launch automatically each time you log in to Windows.
#
# Usage:
#   Right-click register_startup.ps1 -> "Run with PowerShell"
#   OR open PowerShell and run: .\register_startup.ps1

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BotScript  = Join-Path $ProjectDir "bot.py"
$Python     = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$TaskName   = "ScorekeeperBot"

if (-not (Test-Path $Python)) {
    Write-Error "Python not found at: $Python"
    exit 1
}

$Action  = New-ScheduledTaskAction -Execute $Python -Argument "`"$BotScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Starts Scorekeeper of the Fool Discord bot on login" `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. The bot will start automatically at next login."
Write-Host "To start it now, run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove it later, run:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
