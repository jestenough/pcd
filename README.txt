Project cd
==========

pcd (project cd) is a fast way to jump between local projects from your terminal.

Instead of:
    cd ~/development/github/palantir/backend/pigeon-surveillance
Just:
    pcd pigeon-surveillance


REQUIREMENTS AND SUPPORT
------------------------
Python:     >=3.12,<4
Platforms:  Linux, macOS, Windows

Supported shells:
    * Bash / Git Bash
    * Zsh
    * Fish
    * PowerShell 7+
    * Windows PowerShell 5.1
    * CMD


INSTALL
-------
    pipx install git+https://github.com/jestenough/pcd.git
    pcd shell install

Restart your shell or run the reload command printed by the installer.
Active shell integration is required to change the current shell's directory.

Windows: Execution Policy can block your PowerShell profile, while App Control
can block pcd.exe itself. These require different fixes; see docs/windows.rst.


QUICK START
-----------
    cd <your projects folder, e.g. ~/Github/>
    pcd init
    pcd <project name>

pcd discovers Git repositories inside registered scan roots. Use pcd add to
register an ordinary directory, and pcd refresh to rescan existing roots.

Use ordered words to narrow a search. For example, pcd acme api finds an api directory
under acme. To remember ordinary cd visits and use --resume, enable directory
tracking for your shell (docs/shells.rst). Stored visits survive pcd refresh.


COMMON COMMANDS
---------------
    pcd <project>                 Find a project and jump to it
    pcd <project> --resume        Return to its last tracked subdirectory
    pcd import zoxide --dry-run   Preview a zoxide import (omit --dry-run to apply)
    pcd add [path] [--name NAME]  Register a directory or restore an excluded project
    pcd remove <project>          Unregister or exclude a project, keeping its directory
    pcd info <project>            Show paths, source, discovery routes and status
    pcd doctor                    Check configuration, cache, history, paths and shell integration
    pcd list                      List known projects
    pcd roots                     List scan roots
    pcd refresh                   Rescan roots
    pcd shell status [shell]      Inspect shell integration
    pcd --help                    Show all commands


PERFORMANCE
-----------
With 100,000 registered projects, exact lookup took 119-161 ms across Linux,
macOS and Windows (342-619 ms with v0.2.8). A real shell jump between two
directories took 169-382 ms with pcd and 2-28 ms with zoxide. For the tested
revisions, datasets and methodology, see docs/storage-030.rst.


DOCUMENTATION
-------------
    docs/usage.rst             All commands, search, and the project picker
    docs/shells.rst            Automatic/manual shell setup and migration
    docs/windows.rst           Execution Policy and Application Control errors
    docs/troubleshooting.rst   Other shell startup problems on Linux/macOS/Windows
    docs/configuration.rst     Configuration, editor, and scan settings
    docs/storage-030.rst       SQLite migration, recovery, and performance vs. v0.2.8 and zoxide
    docs/development.rst       Development, checks, builds, and benchmarks

Browse online: https://github.com/jestenough/pcd/tree/master/docs

See FEATURES.txt for behavior details and ROADMAP.txt for planned changes.
License: MIT (LICENSE).
