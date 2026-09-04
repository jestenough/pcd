Project cd
==========

pcd (project cd) - Is a fast way to jump between projects from your terminal

Instead of:
    cd ~/development/github/palantir/backend/pigeon-surveillance
Just:
    pcd pigeon-surveillance

Supported shells:
* Bash
* Zsh
* Fish


INSTALL
-------
Requires Python 3.12 or newer.

    pipx install git+https://github.com/jestenough/pcd.git
    pcd shell install
Restart your shell after installing shell integration.


QUICK START
----------
    cd <your root folder, e.g. ~/Github/>
    pcd init
    pcd <project name from your root folder>
pcd discovers projects inside registered scan roots.


COMMANDS
--------

    pcd <project>                Jump to a project
    pcd                          Show help

    pcd add <path>               Add a project manually
    pcd remove <project>         Remove a manual project

    pcd init                     Add the current directory as a scan root
    pcd uninit                   Remove the current directory from scan roots
    pcd roots                    List scan roots
    pcd refresh                  Rescan roots and rebuild the project cache

    pcd list                     List all known projects
    pcd config path              Print the configuration file path
    pcd config show              Print the effective configuration
    pcd config edit              Edit the user configuration
    pcd config validate          Validate the user configuration

    pcd shell install            Install shell integration
    pcd shell status             Show shell integration status
    pcd shell uninstall          Remove shell integration

    pcd --project <project>      Jump to a project whose name matches a command
    pcd --version                Show version
    pcd --help                   Show help

* Projects inside registered roots are discovered automatically.
* You can register multiple scan roots.
* Set `editor = "nvim"` in the configuration to choose an editor for `pcd config edit`.


DEVELOPMENT
-----------
    make install
    make check
    make build
See ./dist folder
You can install *.whl with your python package manager


SEE ALSO
--------
    Features:
        FEATURES.txt

    Planned changes:
        ROADMAP.md

    License:
        MIT
