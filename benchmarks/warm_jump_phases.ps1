$ErrorActionPreference = 'Stop'
pcd shell init pwsh | Out-String | Invoke-Expression

# Time the installed wrapper in this benchmark process only.
$body = (Get-Command pcd -CommandType Function).Definition
$anchors = [ordered]@{
    lookup = '$output = __pcdWorkerRequest $pcdExecutable ''lookup'' $PcdArguments[0]'
    location = 'Set-Location -LiteralPath ([string] $output) -ErrorAction Stop'
    record = '__pcdRecordVisit'
}
foreach ($phase in $anchors.Keys) {
    $line = $anchors[$phase]
    if ([regex]::Matches($body, [regex]::Escape($line)).Count -ne 1) {
        throw "Cannot instrument $phase in installed wrapper"
    }
    $body = $body.Replace($line, (
        '$begin = [Diagnostics.Stopwatch]::GetTimestamp()' + "`n" + $line + "`n" +
        '$script:times.' + $phase + ' = [Diagnostics.Stopwatch]::GetElapsedTime($begin).TotalMilliseconds'
    ))
}
Set-Item Function:pcd ([scriptblock]::Create($body))
$workerId = $null
foreach ($i in 0..47) {
    $name = @('jump-alpha', 'jump-beta')[$i % 2]
    $expected = Join-Path $PSScriptRoot $name
    $times = @{}
    $beginJump = [Diagnostics.Stopwatch]::GetTimestamp()
    pcd $name
    $total = [Diagnostics.Stopwatch]::GetElapsedTime($beginJump).TotalMilliseconds
    if ($LASTEXITCODE -ne 0 -or $PWD.Path -ne $expected -or $times.Count -ne 3) {
        throw "Incomplete jump to $name"
    }
    if (!$global:__pcdWorker -or ($i -gt 0 -and $workerId -ne $global:__pcdWorker.Id)) {
        throw 'Worker unavailable or restarted'
    }
    $workerId = $global:__pcdWorker.Id
    if ($i -ge 6) {
        $times.total = $total
        $times.other = $total - $times.lookup - $times.location - $times.record
        $times | ConvertTo-Json -Compress
    }
}
