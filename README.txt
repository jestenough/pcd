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


COMMON COMMANDS
---------------
    pcd <project>                Find a project and jump to it
    pcd add [path] [--name NAME]  Register a directory (default: current directory)
    pcd remove <project>         Remove a manual registration, keeping its directory
    pcd list                    List known projects
    pcd roots                   List scan roots
    pcd refresh                 Rescan roots
    pcd shell status [shell]     Inspect shell integration
    pcd --help                  Show all commands


DOCUMENTATION
-------------
    docs/usage.rst             All commands, search, and the project picker
    docs/shells.rst            Automatic/manual shell setup and migration
    docs/windows.rst           Execution Policy and Application Control errors
    docs/troubleshooting.rst   Other shell startup problems on Linux/macOS/Windows
    docs/configuration.rst     Configuration, editor, and scan settings
    docs/development.rst       Development, checks, builds, and benchmarks

Browse online: https://github.com/jestenough/pcd/tree/master/docs

See FEATURES.txt for behavior details and ROADMAP.txt for planned changes.
License: MIT (LICENSE).
