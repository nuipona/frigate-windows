# Publish a CI log file as GitHub annotations.
#
# A failing Windows step has nowhere to show its output: job logs and their
# artifacts are not reachable from outside the runner, and the step that fails
# is often a Python process whose stdout only exists in a redirect. Writing the
# log out as annotations is what makes the failure readable in the checks view.
#
# GitHub keeps only a handful of annotations per job and truncates messages at
# roughly 4 KiB. Each view of a log is therefore published as one annotation,
# reduced first so both the beginning and the failure summary at the end remain
# visible through the checks API.
#
# Usage, from a `shell: pwsh` step at the repository root:
#
#     . .\testing-scripts\publish-log-annotations.ps1
#     Publish-Log -Path suite.log -Prefix suite -Keep '^FAIL: |^ERROR: '
#     Publish-Log -Path frigate-run.log -Prefix frigate-head -Head 40 -Tail 20

function Publish-Log {
    param(
        # the file to publish
        [Parameter(Mandatory = $true)][string]$Path,
        # the annotation title; each call should use its own
        [Parameter(Mandatory = $true)][string]$Prefix,
        # keep only lines matching this regex (empty keeps every line)
        [string]$Keep = '',
        # drop lines matching any of these regexes
        [string[]]$Drop = @(),
        # first N and/or last N lines; either option also works on its own
        [int]$Head = 0,
        [int]$Tail = 0,
        [int]$Budget = 3800
    )

    if (-not (Test-Path $Path)) {
        Write-Output ("::error title={0}::there is no log at {1}" -f $Prefix, $Path)
        return
    }

    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($line in (Get-Content -Path $Path)) {
        if ($Keep -ne '' -and $line -notmatch $Keep) { continue }

        $skip = $false
        foreach ($pattern in $Drop) {
            if ($line -match $pattern) { $skip = $true; break }
        }
        if (-not $skip) { $kept.Add($line) }
    }

    if ($Head -gt 0 -and $Tail -gt 0 -and $kept.Count -gt ($Head + $Tail)) {
        $omitted = $kept.Count - $Head - $Tail
        $trimmed = New-Object System.Collections.Generic.List[string]
        foreach ($line in $kept[0..($Head - 1)]) { $trimmed.Add($line) }
        $trimmed.Add("... $omitted lines omitted ...")
        foreach ($line in $kept[($kept.Count - $Tail)..($kept.Count - 1)]) {
            $trimmed.Add($line)
        }
        $kept = $trimmed
    }
    elseif ($Head -gt 0 -and $kept.Count -gt $Head) {
        $omitted = $kept.Count - $Head
        $trimmed = New-Object System.Collections.Generic.List[string]
        foreach ($line in $kept[0..($Head - 1)]) { $trimmed.Add($line) }
        $trimmed.Add("... $omitted lines omitted ...")
        $kept = $trimmed
    }
    elseif ($Tail -gt 0 -and $kept.Count -gt $Tail) {
        $omitted = $kept.Count - $Tail
        $trimmed = New-Object System.Collections.Generic.List[string]
        $trimmed.Add("... $omitted lines omitted ...")
        foreach ($line in $kept[($kept.Count - $Tail)..($kept.Count - 1)]) {
            $trimmed.Add($line)
        }
        $kept = $trimmed
    }

    $text = ($kept -join ' // ') -replace "`r", ''
    if ($text.Length -gt $Budget) {
        $marker = ' // ... content truncated ... // '

        if ($Budget -le $marker.Length) {
            $text = $text.Substring(0, $Budget)
        }
        else {
            [int]$left = [Math]::Floor(($Budget - $marker.Length) / 2)
            [int]$right = $Budget - $marker.Length - $left
            $text = $text.Substring(0, $left) + $marker +
                $text.Substring($text.Length - $right, $right)
        }
    }
    Write-Output ("::error title={0}::{1}" -f $Prefix, $text)
}
