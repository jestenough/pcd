# Roadmap

This file tracks planned features and improvements for pcd.

## CLI and output

- [ ] Improve `pcd list` output with aligned columns and clearer status information.
- [ ] Add `pcd list --manual`.
- [ ] Add `pcd list --discovered`.
- [ ] Add `pcd list --missing`.
- [ ] Add `pcd list --json`.
- [ ] Improve `pcd roots` output with project counts and root status.
- [ ] Keep `pcd` with no arguments equivalent to showing help.

## Project information

- [ ] Add `pcd info <project>`.
- [ ] Show project name, path, source, root, aliases, group membership, and status.
- [ ] Handle duplicate project names explicitly instead of choosing silently.

## Aliases

Aliases are global alternative names for projects.

Example:

    pcd alias add ilvo-parser --as parser
    pcd parser

Planned commands:

    pcd alias add <project> --as <alias>
    pcd alias remove <alias>
    pcd alias list

Requirements:

- [ ] Aliases must reference a concrete project path internally.
- [ ] Aliases must survive `pcd refresh`.
- [ ] Detect conflicts with commands, project names, groups, and other aliases.
- [ ] Show aliases in `pcd list` and `pcd info`.
- [ ] Validate stale aliases in `pcd doctor`.

## Groups

Groups provide a namespace for related projects, regardless of where those projects are stored or how their directories are named.

Example projects:

    ~/GitHub/work/ilvo-parser
    ~/Projects/ilvo.Library
    ~/GitHub/personal/ilvo

They can be grouped as:

    ilvo/parser
    ilvo/library
    ilvo/core

Planned commands:

    pcd group create <group>
    pcd group add <group> <project> [--as <member-name>]
    pcd group remove <group> <member-name>
    pcd group delete <group>
    pcd group list
    pcd group show <group>

`<project>` may be:

- a known project name;
- an absolute path;
- a relative path;
- `.` for the current directory.

Examples:

    pcd group add ilvo ilvo-parser --as parser
    pcd group add ilvo /home/user/projects/ilvo.Library --as library
    pcd group add ilvo ../../legacy/ilvo --as core
    pcd group add ilvo . --as backend

Navigation:

    pcd ilvo
        Open a picker containing only projects from the `ilvo` group.

    pcd ilvo/parser
        Jump directly to the project registered as `parser` inside `ilvo`.

Requirements:

- [ ] Group member names are local to the group.
- [ ] A group member name is not a global alias.
- [ ] Groups reference concrete project paths internally.
- [ ] Groups must not depend on common parent directories.
- [ ] Groups must survive `pcd refresh`.
- [ ] The same local name may exist in different groups.
- [ ] Group and project name conflicts must have deterministic resolution.
- [ ] `--project` remains available for explicitly resolving a project whose name conflicts with another CLI entity.

## Config

Replace the standalone `config-path` command with a `config` command group.

Planned commands:

    pcd config path
    pcd config show
    pcd config edit
    pcd config validate

Requirements:

- [x] `config path` prints the active user configuration path.
- [x] `config show` prints the effective user configuration.
- [x] `config edit` uses the configured editor, then `$VISUAL`, then `$EDITOR`.
- [x] Allow `config edit` to use a per-user editor configured in `config.toml`.
- [x] Do not silently choose an editor if neither variable is configured.
- [x] `config validate` reports precise validation errors.
- [x] Keep routine operations available as dedicated commands rather than forcing users to edit the config manually.

## Shell integration

Officially supported shells:

- Bash
- Zsh
- Fish

Current automatic setup:

    pcd shell install

Manual setup must remain supported.

Bash:

    eval "$(command pcd shell print bash)"

Zsh:

    eval "$(command pcd shell print zsh)"

Fish:

    command pcd shell print fish | source

Planned improvements:

- [ ] Improve `pcd shell status` formatting.
- [ ] Clearly distinguish installed/configured integration from integration active in the current shell.
- [ ] Give a useful reload hint after installation.
- [ ] Ensure `shell uninstall` removes only the block managed by pcd.
- [ ] Consider renaming `pcd shell print <shell>` to `pcd shell init <shell>`.
- [ ] Keep automatic installation optional; manual shell configuration remains a first-class setup method.
- [ ] Test Bash, Zsh, and Fish independently.

Possible future shell:

- [ ] Nushell support.

## Completion

- [ ] Complete project names for `pcd <project>`.
- [ ] Complete group names.
- [ ] Complete group members after `pcd <group>/`.
- [ ] Complete aliases.
- [ ] Complete appropriate project names for `remove`, `info`, and group commands.

## Frecency

- [ ] Track project usage count and last-used time.
- [ ] Prefer recently and frequently used projects in the picker.
- [ ] Keep history lightweight and bounded.
- [ ] Do not let history affect deterministic exact-name resolution.

## Doctor

Add:

    pcd doctor

Planned checks:

- [ ] Configuration is readable and valid.
- [ ] Cache is writable.
- [ ] Scan roots exist.
- [ ] Projects with missing paths are reported.
- [ ] Aliases with missing targets are reported.
- [ ] Group members with missing targets are reported.
- [ ] `pcd` executable is available in `PATH`.
- [ ] Shell integration is installed.
- [ ] Shell integration is active in the current shell when detectable.
- [ ] Duplicate and conflicting names are reported.

## Refresh and stale data

- [ ] `pcd refresh` rebuilds discovered projects without deleting user configuration.
- [ ] Manual projects survive refresh.
- [ ] Aliases survive refresh.
- [ ] Groups survive refresh.
- [ ] Missing discovered projects are removed from the rebuilt cache.
- [ ] Stale user-defined references are reported rather than silently deleted.

## Later

Ideas intentionally deferred until the core navigation experience is complete:

- [ ] Nushell integration.
- [ ] Optional helpers for creating groups from matching project names.
- [ ] Additional list sorting modes.
- [ ] More machine-readable output where useful.
- [ ] Release automation and PyPI publishing.

The primary goal remains simple:

    find a project -> select it -> jump to it
