[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:DataRoot = Join-Path $script:ProjectRoot 'data'
$script:Targets = @(
    @{ Name = 'RedditDailyDigest'; Hour = 18; Minute = 30; Wrapper = 'run_digest.bat' },
    @{ Name = 'RedditDigestWatchdog'; Hour = 19; Minute = 30; Wrapper = 'run_watchdog.bat' }
)

function Test-RepairAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DefinitionWithoutBoundary([string]$Xml) {
    $document = [xml]::new()
    $document.XmlResolver = $null
    $document.LoadXml($Xml)
    $nodes = @($document.SelectNodes("/*[local-name()='Task']/*[local-name()='Triggers']/*/*[local-name()='StartBoundary']"))
    if ($nodes.Count -ne 1) { throw 'Expected exactly one task StartBoundary.' }
    $nodes[0].InnerText = 'BOUNDARY'
    return $document.OuterXml
}

function Get-RepairSnapshot($Target) {
    $tasks = @(Get-ScheduledTask -TaskPath '\' -TaskName $Target.Name -ErrorAction Stop)
    if ($tasks.Count -ne 1) { throw "Expected exactly one root task: $($Target.Name)." }
    $task = $tasks[0]
    $triggers = @($task.Triggers)
    $actions = @($task.Actions)
    if ($task.State.ToString() -ne 'Ready' -or $triggers.Count -ne 1 -or
        $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger' -or
        $triggers[0].DaysInterval -ne 1 -or $triggers[0].Enabled -eq $false) {
        throw "Unexpected task state or daily trigger: $($Target.Name)."
    }
    $expectedWrapper = Join-Path $script:ProjectRoot $Target.Wrapper
    $expectedArguments = '/c "' + $expectedWrapper + '"'
    $command = if ($actions.Count -eq 1) { $actions[0].Execute } else { '' }
    $validCommand = $command -ieq 'cmd.exe' -or $command -ieq (Join-Path $env:SystemRoot 'System32\cmd.exe')
    if ($actions.Count -ne 1 -or -not $validCommand -or
        $actions[0].Arguments -ine $expectedArguments -or
        $actions[0].WorkingDirectory.TrimEnd('\') -ine $script:ProjectRoot.TrimEnd('\')) {
        throw "Unexpected executable, wrapper, arguments or working directory: $($Target.Name)."
    }
    $xml = Export-ScheduledTask -TaskPath '\' -TaskName $Target.Name -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $Target.Name -ErrorAction Stop
    return [pscustomobject]@{
        Task = $task; Xml = $xml; Definition = (Get-DefinitionWithoutBoundary $xml)
        Boundary = [string]$triggers[0].StartBoundary; NextRun = $info.NextRunTime
    }
}

function Test-DesiredBoundary([string]$Boundary, $Target) {
    if ($Boundary -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00$') { return $false }
    $date = [datetime]::ParseExact($Boundary, "yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
    return $date.Hour -eq $Target.Hour -and $date.Minute -eq $Target.Minute
}

function Write-RepairReport($Result, [string]$Path) {
    if (-not $Path) { return }
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($script:DataRoot).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The report must be saved under this project data directory.'
    }
    $parent = [IO.Path]::GetDirectoryName($resolved)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = $resolved + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Result | ConvertTo-Json -Depth 7), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $resolved -Force
}

function Invoke-DigestScheduleRepair([switch]$Apply, [string]$ReportPath) {
    $result = [ordered]@{
        version = 1; mode = $(if ($Apply) { 'apply' } else { 'plan' })
        status = 'failed'; changed = @(); tasks = @(); rollback = @(); backup_directory = $null; error = $null
    }
    $prepared = @()
    $touched = [Collections.Generic.List[object]]::new()
    try {
        if ((Get-TimeZone).Id -ne 'Eastern Standard Time') {
            throw 'Expected Toronto-compatible Windows timezone; no tasks changed.'
        }
        if ($Apply -and -not (Test-RepairAdministrator)) {
            throw 'Apply requires an elevated administrator PowerShell session; no tasks changed.'
        }
        # Reject an invalid report path before any task mutation.
        if ($ReportPath) {
            $reportFullPath = [IO.Path]::GetFullPath($ReportPath)
            $dataPrefix = [IO.Path]::GetFullPath($script:DataRoot).TrimEnd('\') + '\'
            if (-not $reportFullPath.StartsWith($dataPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'The report must be saved under this project data directory.'
            }
        }
        foreach ($target in $script:Targets) {
            $snapshot = Get-RepairSnapshot $target
            $alreadyCorrect = Test-DesiredBoundary $snapshot.Boundary $target
            $nextStart = (Get-Date).Date.AddHours($target.Hour).AddMinutes($target.Minute)
            if ($nextStart -le (Get-Date).AddMinutes(2)) { $nextStart = $nextStart.AddDays(1) }
            $boundary = if ($alreadyCorrect) { $snapshot.Boundary } else { $nextStart.ToString("yyyy-MM-dd'T'HH:mm:ss") }
            if ($alreadyCorrect -and ($snapshot.NextRun -le (Get-Date) -or
                $snapshot.NextRun.Hour -ne $target.Hour -or $snapshot.NextRun.Minute -ne $target.Minute)) {
                throw "Correct-looking trigger has unexpected next run: $($target.Name)."
            }
            $prepared += [pscustomobject]@{ Target = $target; Before = $snapshot; Boundary = $boundary; Skip = $alreadyCorrect }
            $result.tasks += [ordered]@{
                name = $target.Name; original_boundary = $snapshot.Boundary; desired_boundary = $boundary
                already_correct = $alreadyCorrect; next_run_before = $snapshot.NextRun.ToString('o')
            }
        }
        if ($Apply) {
            $toChange = @($prepared | Where-Object { -not $_.Skip })
            if ($toChange.Count) {
                $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N')
                $backupDir = Join-Path $script:DataRoot ('schedule-repair\' + $stamp)
                [IO.Directory]::CreateDirectory($backupDir) | Out-Null
                $result.backup_directory = $backupDir
                # Export both definitions before either trigger is changed.
                foreach ($item in $prepared) {
                    $path = Join-Path $backupDir ($item.Target.Name + '.before.xml')
                    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
                    try {
                        $preamble = [Text.Encoding]::Unicode.GetPreamble()
                        $stream.Write($preamble, 0, $preamble.Length)
                        $bytes = [Text.Encoding]::Unicode.GetBytes($item.Before.Xml)
                        $stream.Write($bytes, 0, $bytes.Length)
                    } finally { $stream.Dispose() }
                }
            }
            foreach ($item in $prepared) {
                if ($item.Skip) { continue }
                $current = Get-RepairSnapshot $item.Target
                if ($current.Xml -cne $item.Before.Xml) { throw "Task changed during repair preparation: $($item.Target.Name)." }
                if ([datetime]::ParseExact($item.Boundary, "yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture) -le (Get-Date)) {
                    throw 'Planned trigger is no longer in the future; rerun the repair.'
                }
                $touched.Add($item)
                $current.Task.Triggers[0].StartBoundary = $item.Boundary
                Set-ScheduledTask -TaskPath '\' -TaskName $item.Target.Name -Trigger $current.Task.Triggers -ErrorAction Stop | Out-Null
                $after = Get-RepairSnapshot $item.Target
                if ($after.Boundary -cne $item.Boundary -or $after.Definition -cne $item.Before.Definition) {
                    throw "Unexpected task definition after updating $($item.Target.Name)."
                }
                if ($after.NextRun -le (Get-Date) -or $after.NextRun.Hour -ne $item.Target.Hour -or $after.NextRun.Minute -ne $item.Target.Minute) {
                    throw "Unexpected next run after updating $($item.Target.Name)."
                }
                [IO.File]::WriteAllText((Join-Path $backupDir ($item.Target.Name + '.after.xml')), $after.Xml, [Text.Encoding]::Unicode)
                $result.changed += $item.Target.Name
            }
            # Verify both tasks, including an idempotently skipped task.
            foreach ($item in $prepared) {
                $final = Get-RepairSnapshot $item.Target
                if ($final.Definition -cne $item.Before.Definition -or -not (Test-DesiredBoundary $final.Boundary $item.Target) -or
                    $final.NextRun -le (Get-Date) -or $final.NextRun.Hour -ne $item.Target.Hour -or $final.NextRun.Minute -ne $item.Target.Minute) {
                    throw "Final schedule verification failed: $($item.Target.Name)."
                }
                ($result.tasks | Where-Object { $_.name -eq $item.Target.Name }).next_run_after = $final.NextRun.ToString('o')
            }
        }
        $result.status = if ($Apply) { 'verified' } else { 'planned' }
    } catch {
        $result.error = $_.Exception.Message
        for ($index = $touched.Count - 1; $index -ge 0; $index--) {
            $item = $touched[$index]
            try {
                $current = Get-RepairSnapshot $item.Target
                if ($current.Definition -cne $item.Before.Definition -or
                    ($current.Boundary -cne $item.Before.Boundary -and $current.Boundary -cne $item.Boundary)) {
                    throw 'Task changed independently; original XML preserved for manual review.'
                }
                if ($current.Boundary -cne $item.Before.Boundary) {
                    $current.Task.Triggers[0].StartBoundary = $item.Before.Boundary
                    Set-ScheduledTask -TaskPath '\' -TaskName $item.Target.Name -Trigger $current.Task.Triggers -ErrorAction Stop | Out-Null
                }
                $restored = Get-RepairSnapshot $item.Target
                if ($restored.Xml -cne $item.Before.Xml) { throw 'Rollback definition verification failed.' }
                $result.rollback += @{ name = $item.Target.Name; status = 'restored' }
            } catch {
                $result.rollback += @{ name = $item.Target.Name; status = 'requires_review'; error = $_.Exception.Message }
            }
        }
    }
    try { Write-RepairReport $result $ReportPath } catch {
        $result.error = (($result.error, "Could not save report: $($_.Exception.Message)") | Where-Object { $_ }) -join ' '
        if ($result.status -ne 'failed') { $result.status = 'report_failed' }
    }
    return $result
}

# Dot-sourcing exposes functions for offline mocked validation only.
if ($MyInvocation.InvocationName -ne '.') {
    $result = Invoke-DigestScheduleRepair -Apply:$Apply -ReportPath $ReportPath
    $result | ConvertTo-Json -Depth 7
    if ($result.status -notin @('planned', 'verified')) { exit 1 }
}
