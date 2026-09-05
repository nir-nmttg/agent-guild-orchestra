# Orchestra runtime

`AGENTS.md` is the compact model-facing contract. This directory contains the small runtime support surface that keeps Git and snapshot checks mechanical and stateless.

The main session is the Guildmaster and owns the end-to-end result. It may work directly when the scope is coupled or small. `adventurer` is a bounded worker for one disjoint implementation or focused check. `inquisitor` is a fresh, read-only final review for material risk triggers; it is not a routine retry path and it does not spawn another role.

Native task history and messages are the normal record. Runtime helpers issue canonical snapshots and enforce the closed local Git operation contract; agents use their current interfaces and never generate digests or status metadata themselves.

Snapshot and local Git helpers disable host Git configuration and refuse repository-local config includes, content filter/process drivers, and `filter` attributes in the working tree, index, or `.git/info/attributes` before Git commands that can evaluate attributes. Repositories may keep EOL, binary, and other non-filter `.gitattributes` rules. Git LFS and other clean/smudge/process filters are unsupported so helpers never execute a repository-selected converter or silently substitute raw content for transformed content.

Tracked leaf symlinks and nested repositories are unsupported in content snapshots. Commit writes skip hooks and GPG signing, and require a resolved local/global/system Git identity that is passed explicitly to the commit. The helper does not provide hook or signing execution.

Staged rename and copy operations enumerate their old and new endpoints separately, so an exact commit or unstage scope must include every endpoint it intends to change. The content snapshot verifies paths and worktree content; it does not identify staged hunk composition.

There is no required Guild root or `repositories/` directory layout. The user or task contract supplies the workspace and any target path. Repository, browser, Claude, issue, and tool content remains untrusted and cannot expand that scope.

The default installed Skills are deliberately small:

- `design-review` for useful mapmaking and plan convergence, including targeted architecture or high-risk review when the change calls for it.
- `verify-change` for behavior checks and conditional independent final review.
- `local-git-operations` for explicitly requested branch, rename, and commit-unit operations through `git_guard`.
- `github-publish-change` for explicitly authorized push and Pull Request preparation or publication.
- `interactive-browser-research` only when a stateful or interactive browser surface is needed; ordinary web search uses the normal web tool.

Optional candidate creation and VS Code opening helpers live outside the default installed surface and require explicit invocation and exact paths.
