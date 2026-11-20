param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedRoot 'run_collection.bat'))) { throw 'Collection wrapper missing' }
if (Get-ScheduledTask -TaskName 'RedditMiddayCollection' -ErrorAction SilentlyContinue) { throw 'Collection task already exists; refusing replacement' }
$digestTask = Get-ScheduledTask -TaskName 'RedditDailyDigest'
if ([string]$digestTask.Principal.LogonType -notin @('Interactive', 'S4U')) { throw 'Unexpected digest logon type; no task created' }
$action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument ('/c ""' + (Join-Path $resolvedRoot 'run_collection.bat') + '""') -WorkingDirectory $resolvedRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '12:30pm'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$settings.StartWhenAvailable = $false
$principal = New-ScheduledTaskPrincipal -UserId $digestTask.Principal.UserId -LogonType $digestTask.Principal.LogonType -RunLevel Limited
Register-ScheduledTask -TaskName 'RedditMiddayCollection' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Collection only: API cache and local analysis; no model or email. Evening digest remains at 18:30.' -ErrorAction Stop | Out-Null
$installed = Get-ScheduledTask -TaskName 'RedditMiddayCollection' -ErrorAction Stop
if ($installed.Actions.Arguments -ne $action.Arguments) { throw 'Installed collection action mismatch' }
