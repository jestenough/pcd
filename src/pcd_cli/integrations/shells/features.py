"""Optional directory tracking rendered in the caller's shell."""

from textwrap import dedent

from pcd_cli.integrations.shells.common import Shell


def recorder(shell: Shell) -> str:
    """Record only changed, successfully entered locations; never let history block cd."""
    if shell in (Shell.BASH, Shell.ZSH):
        return dedent("""\
            _pcd_last_directory=$PWD
            _pcd_track_cd=0
            _pcd_record_visit() {
                local previous_status=$?
                if [ "$_pcd_last_directory" != "$PWD" ]; then
                    _pcd_last_directory=$PWD
                    if [ "$_pcd_track_cd" = 1 ]; then
                        command pcd _visit > /dev/null || :
                    else
                        command pcd _visit --project-only > /dev/null || :
                    fi
                fi
                return "$previous_status"
            }
            """)
    if shell is Shell.FISH:
        return dedent("""\
            set -g _pcd_last_directory "$PWD"
            set -g _pcd_track_cd 0
            function _pcd_record_visit
                set -l previous_status $status
                if test "$_pcd_last_directory" != "$PWD"
                    set -g _pcd_last_directory "$PWD"
                    if test "$_pcd_track_cd" = 1
                        command pcd _visit > /dev/null
                    else
                        command pcd _visit --project-only > /dev/null
                    end
                end
                return $previous_status
            end
            """)
    if shell in (Shell.PWSH, Shell.POWERSHELL):
        return dedent("""\
            $global:__pcdLastDirectory = $PWD.Path
            $global:__pcdTrackCd = $false
            $global:__pcdVisitExecutable = $null
            function global:__pcdExecutablePath {
                if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
                    $app = $ExecutionContext.InvokeCommand.GetCommand('pcd', 'Application')
                    if ($app) { return $app.Source }
                }
                (Get-Command pcd -CommandType Application -TotalCount 1 -ErrorAction Stop).Source
            }
            function global:__pcdWorkerRequest($exe, $op, $value) {
                if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows -or
                    $ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage' -or
                    $exe -notlike '*.exe') { return }
                $key = ConvertTo-Json -Compress -InputObject @(
                    $exe, $env:USERPROFILE, $env:HOME, $env:LOCALAPPDATA, $env:APPDATA,
                    $env:WIN_PD_OVERRIDE_LOCAL_APPDATA, $env:WIN_PD_OVERRIDE_APPDATA,
                    $env:PYTHONPATH, $env:PYTHONHOME
                )
                $proc = $global:__pcdWorker
                try {
                    if ($proc -and ($proc.HasExited -or $global:__pcdWorkerKey -cne $key)) {
                        if (-not $proc.HasExited) { $proc.StandardInput.Close() }
                        $proc.Dispose()
                        $global:__pcdWorker = $proc = $null
                    }
                    if (-not $proc) {
                        if ($op -ne 'lookup') { return }
                        $proc = [Diagnostics.Process]::new()
                        $proc.StartInfo.FileName = $exe
                        $proc.StartInfo.Arguments = '--internal-shell-worker'
                        $proc.StartInfo.WorkingDirectory = $env:SystemRoot
                        $proc.StartInfo.UseShellExecute = $false
                        $proc.StartInfo.CreateNoWindow = $true
                        $proc.StartInfo.RedirectStandardInput = $true
                        $proc.StartInfo.RedirectStandardOutput = $true
                        $proc.StartInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
                        if (-not $proc.Start()) { return }
                        $global:__pcdWorker = $proc
                        $global:__pcdWorkerKey = $key
                    }
                    $request = ConvertTo-Json -Compress -InputObject @($op, $value, $env:PCD_EXCLUDE_DIRS)
                    $proc.StandardInput.WriteLine($request)
                    $proc.StandardInput.Flush()
                    $reply = $proc.StandardOutput.ReadLineAsync()
                    if (-not $reply.Wait(10000) -or $null -eq $reply.Result) { throw 'Worker unavailable' }
                    return (ConvertFrom-Json -InputObject $reply.Result)
                } catch {
                    if ($proc) {
                        try { if (-not $proc.HasExited) { $proc.Kill($true) } } catch {}
                        $proc.Dispose()
                    }
                    $global:__pcdWorker = $null
                    if ($op -ne 'lookup') {
                        Write-Warning 'pcd: visit acknowledgement lost; check history'
                        return 'uncertain'
                    }
                }
            }
            if (-not (Get-Variable __pcdWorker -Scope Global -ErrorAction SilentlyContinue)) {
                $global:__pcdWorker = $null
            }
            function global:__pcdRecordVisit {
                $previousCode = $global:LASTEXITCODE
                if ($PWD.Provider.Name -eq 'FileSystem' -and
                    $global:__pcdLastDirectory -cne $PWD.Path) {
                    $global:__pcdLastDirectory = $PWD.Path
                    try {
                        $exe = $global:__pcdVisitExecutable
                        if (-not $exe) { $exe = __pcdExecutablePath }
                        $op = if ($global:__pcdTrackCd) { 'track' } else { 'project' }
                        if ($global:__pcdWorker -and (__pcdWorkerRequest $exe $op $PWD.ProviderPath)) {
                            $global:LASTEXITCODE = $previousCode
                            return
                        }
                        if ($global:__pcdTrackCd) {
                            & $exe _visit | Out-Null
                        } else {
                            & $exe _visit --project-only | Out-Null
                        }
                    } catch {
                        Write-Warning ('pcd: could not record directory visit: ' + $_.Exception.Message)
                    }
                }
                $global:LASTEXITCODE = $previousCode
            }
            """)
    return ""


def optional_features(shell: Shell, *, track_cd: bool) -> str:
    if shell in (Shell.BASH, Shell.ZSH):
        return _posix_features(shell, track_cd=track_cd)
    if shell is Shell.FISH:
        return _fish_features(track_cd=track_cd)
    if shell in (Shell.PWSH, Shell.POWERSHELL):
        return _powershell_features(track_cd=track_cd)
    return ""


def _posix_features(shell: Shell, *, track_cd: bool) -> str:
    result = ""
    if track_cd:
        result += "_pcd_track_cd=1\n"
        if shell is Shell.ZSH:
            result += dedent("""\
                if (( ! ${chpwd_functions[(Ie)_pcd_record_visit]} )); then
                    chpwd_functions+=(_pcd_record_visit)
                fi
                """)
        else:
            result += dedent("""\
                # Preserve a user's cd function/alias; the prompt still observes its final cwd.
                if ! declare -F cd > /dev/null && ! alias cd > /dev/null 2>&1; then
                    function cd {
                        builtin cd "$@" || return $?
                        _pcd_record_visit
                        return 0
                    }
                fi
                if declare -p PROMPT_COMMAND 2>/dev/null | command grep -q 'declare -a'; then
                    if [[ " ${PROMPT_COMMAND[*]} " != *" _pcd_record_visit "* ]]; then
                        PROMPT_COMMAND+=(_pcd_record_visit)
                    fi
                else
                    case ";${PROMPT_COMMAND:-};" in
                        *";_pcd_record_visit;"*) ;;
                        *) PROMPT_COMMAND="_pcd_record_visit${PROMPT_COMMAND:+; $PROMPT_COMMAND}" ;;
                    esac
                fi
                """)
    return result


def _fish_features(*, track_cd: bool) -> str:
    result = ""
    if track_cd:
        result += dedent("""\
            set -g _pcd_track_cd 1
            function _pcd_directory_changed --on-variable PWD
                _pcd_record_visit
            end
            """)
    return result


def _powershell_features(*, track_cd: bool) -> str:
    result = ""
    if track_cd:
        result += "$global:__pcdTrackCd = $true\n"
        result += dedent("""\
            function global:__pcdDirectoryChanged {
                __pcdRecordVisit
            }
            if ($ExecutionContext.SessionState.InvokeCommand.PSObject.Properties['LocationChangedAction']) {
                if (-not (Get-Variable __pcdLocationHook -Scope Global -ErrorAction SilentlyContinue)) {
                    $global:__pcdOriginalLocationAction = $ExecutionContext.SessionState.InvokeCommand.LocationChangedAction
                    $global:__pcdLocationHook = $true
                    $ExecutionContext.SessionState.InvokeCommand.LocationChangedAction = {
                        param($sender, $eventArgs)
                        try {
                            if ($global:__pcdOriginalLocationAction) {
                                $global:__pcdOriginalLocationAction.Invoke($sender, $eventArgs)
                            }
                        } finally { __pcdDirectoryChanged }
                    }
                }
            } elseif (-not (Get-Command Set-Location -CommandType Function -ErrorAction SilentlyContinue)) {
                # A generated proxy keeps all native parameters, providers, and pipeline behavior.
                $metadata = [System.Management.Automation.CommandMetadata](
                    Get-Command Microsoft.PowerShell.Management\\Set-Location)
                $proxy = [System.Management.Automation.ProxyCommand]::Create($metadata)
                $proxy = $proxy.Replace('$steppablePipeline.Process($_)',
                    '$steppablePipeline.Process($_); __pcdDirectoryChanged')
                Set-Item Function:\\global:Set-Location ([scriptblock]::Create($proxy))
            }
            if (-not (Get-Variable __pcdOriginalPrompt -Scope Global -ErrorAction SilentlyContinue)) {
                $global:__pcdOriginalPrompt = (Get-Command prompt -CommandType Function).ScriptBlock
                function global:prompt {
                    __pcdDirectoryChanged
                    & $global:__pcdOriginalPrompt
                }
            }
            """)
    return result
