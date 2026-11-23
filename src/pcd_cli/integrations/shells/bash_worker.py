"""Bash 3.2-compatible transport for interactive exact-name jumps."""

from textwrap import dedent

from pcd_cli.entrypoint import _COMMANDS


def worker_functions() -> str:
    return dedent(r"""
        _pcd_worker_close() {
            exec 7>&- 8>&-
            unset _pcd_worker_active
        }
        _pcd_worker_start() {
            local dir
            # Leave descriptors already owned by the user's shell alone.
            [[ ! -e /dev/fd/7 && ! -e /dev/fd/8 ]] || return 1
            dir=$(command mktemp -d "${TMPDIR:-/tmp}/pcd.XXXXXXXX") || return 1
            if ! command mkfifo "$dir/reply" || ! exec 8<>"$dir/reply"; then
                command rmdir "$dir" 2>/dev/null
                return 1
            fi
            command rm -f "$dir/reply"
            command rmdir "$dir"
            exec 7> >(exec "$exe" --internal-bash-worker >&8 8>&-)
            _pcd_worker_active=1
            _pcd_worker_context=$context
        }
        _pcd_worker_request() {
            # A dead reader can raise SIGPIPE. Confine that signal to the writer.
            (builtin printf '%s\t%s\t%s\n' "$1" "$2" "${PCD_EXCLUDE_DIRS:-}" >&7) || return 2
            IFS= read -r -t 2 reply <&8 || return 1
        }
        _pcd_fast_jump() {
            [[ $- == *i* && $BASH_SUBSHELL == 0 && ${PCD_BASH_WORKER:-1} != 0 ]] || return 1
            case "$OSTYPE" in linux*|darwin*) ;; *) return 1 ;; esac
            [[ $# == 1 && -n $1 && $1 != -* ]] || return 1
            case "$1" in @RESERVED@) return 1 ;; esac
            local exe context reply target op status
            exe=$(type -P pcd) || return 1
            [[ $exe == /* ]] || return 1
            # Quoted declarations distinguish unset, empty and embedded separators.
            context=$(declare -p HOME PATH XDG_CONFIG_HOME XDG_STATE_HOME XDG_CACHE_HOME PYTHONPATH PYTHONHOME VIRTUAL_ENV 2>/dev/null; printf '%s' "$exe")
            case "$1${PCD_EXCLUDE_DIRS:-}" in *$'\t'*|*$'\n'*|*$'\r'*) return 1 ;; esac
            local exclusions=${PCD_EXCLUDE_DIRS:-}
            (( ${#1} + ${#exclusions} < 512 )) || return 1
            if [[ ${_pcd_worker_active:-} ]]; then
                [[ -p /dev/fd/7 && -p /dev/fd/8 ]] || return 1
            fi
            if [[ ${_pcd_worker_active:-} && $_pcd_worker_context != "$context" ]]; then
                _pcd_worker_close
            fi
            [[ ${_pcd_worker_active:-} ]] || _pcd_worker_start || return 1
            if ! _pcd_worker_request lookup "$1"; then
                _pcd_worker_close
                return 1
            fi
            [[ $reply == /* ]] || return 1
            target=$reply
            if [[ $PWD == "$target" ]]; then builtin cd -- "$target"; return $?; fi
            _pcd_last_directory=$PWD
            builtin cd -- "$target" || return 1
            if (( ${#PWD} + ${#exclusions} >= 512 )); then
                _pcd_record_visit
                return 0
            fi
            if [[ $_pcd_track_cd == 1 ]]; then op=track; else op=project; fi
            status=0
            _pcd_worker_request "$op" "$PWD" || status=$?
            if [[ $status == 2 ]]; then
                _pcd_worker_close
                _pcd_record_visit
                return 0
            fi
            _pcd_last_directory=$PWD
            if [[ $status != 0 || $reply != ok ]]; then
                _pcd_worker_close
                printf '%s\n' 'pcd: visit acknowledgement lost; check history' >&2
            fi
            return 0
        }
        """).replace("@RESERVED@", "|".join(sorted(_COMMANDS)))
