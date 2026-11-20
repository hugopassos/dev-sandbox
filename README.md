# dev-sandbox v1

dev-sandbox runs Codex in a Docker container against a filtered copy of a local Git working tree. It reduces accidental exposure of local files and lets developers review proposed changes before applying them to the original repository. For deployment boundaries and data handling, see the [security model](docs/SECURITY.md).

## Quick Start

### 1. Check prerequisites and install the launcher

- A Linux host with Bash and standard utilities including `realpath`, `find` and `sha256sum`.
- Docker Engine with a daemon accessible to the user and the Docker Compose v2 plugin (`docker compose`). Use a Docker context that can bind-mount the local workspace path.
- Git and Python 3.9 or later on the host. The Python scripts use the standard library.
- Visual Studio Code with `code` on the host `PATH` to resolve content conflicts through its merge editor.
- A Codex account or authentication method for interactive use.

From the local checkout of dev-sandbox, link the executable into a directory on your `PATH`. Keep the checkout in place because the launcher finds its Python scripts and Compose files relative to the actual executable.

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/bin/dev-sandbox" "$HOME/.local/bin/dev-sandbox"
export PATH="$HOME/.local/bin:$PATH"
command -v dev-sandbox
```

Add the `PATH` setting to your shell startup file if needed. `command -v` should show the launcher you installed. Run `dev-sandbox` without arguments to verify that it prints the command usage. This invocation exits with status 1. You can also invoke `bin/dev-sandbox` directly from the checkout.

### 2. Prepare a fictional project

Use the root of the original Git repository in each project command. The example creates a disposable repository named `example-app` and adds a Ruby file to the Git index.

```bash
demo_dir="$(mktemp -d)"
repo="$demo_dir/example-app"
git init "$repo"
printf 'puts "Hello, example app!"\n' > "$repo/hello.rb"
git -C "$repo" add hello.rb
dev-sandbox prepare "$repo"
```

Preparation selects paths from the Git index and copies eligible bytes from the working tree, including unstaged edits to tracked files. Untracked files, Git history and repository metadata are omitted. Review the printed omissions and the contents at the reported destination before starting Codex. Use a stable working tree while preparing it.

### 3. Build the runtime and proxy images

From the dev-sandbox checkout, build the Ruby agent image and Squid proxy using the base Compose file. Replace `/path/to/dev-sandbox` with your local checkout path and `prepared` with the absolute **Destination** path printed by `prepare` (labeled `Destino` in the CLI).

```bash
cd /path/to/dev-sandbox
prepared='/absolute/path/to/the/prepared/workspace'
DEV_SANDBOX_STACK=dev-sandbox-build \
DEV_SANDBOX_WORKSPACE="$prepared" \
DEV_SANDBOX_RUNTIME=ruby \
docker compose build codex proxy
```

| Runtime | Additional tools in the image |
| --- | --- |
| `ruby` (default) | Ruby, development headers, Bundler, Minitest and build tools |
| `node` | Node.js 24 from the base image, with Corepack checked during the build |
| `python` | Python 3, pip and venv |
| `php` | PHP CLI, mbstring, XML, cURL and Composer |

All four images include Node.js and Codex. Repeat the build for each required runtime by changing `DEV_SANDBOX_RUNTIME`. The runtime selects the `runtime-<runtime>` build target and the local image tag `dev-sandbox:<runtime>`. The Squid proxy is a separate image shared by the runtime choices. Project dependencies are a separate concern. Ordinary public package registries fall outside the configured runtime proxy policy.

### 4. Validate the setup and run Codex

```bash
dev-sandbox status readonly "$repo" ruby
dev-sandbox check readonly "$repo" ruby
dev-sandbox check dev "$repo" ruby
dev-sandbox dev "$repo" ruby
```

Use `dev-sandbox readonly "$repo" ruby` for an inspection-only session. For this example, use the `dev` session to ask Codex to change the greeting in `hello.rb`. If prompted, authenticate using an approved account. dev-sandbox provides persistent Codex storage and delegates sign-in to Codex.

### 5. Review and apply the proposed change

Exit the Codex session before running these commands individually.

```bash
dev-sandbox review "$repo"
dev-sandbox diff "$repo"
dev-sandbox merge "$repo"
```

`review` and `diff` can return 2 when paths require attention. `merge` returns 2 if it produced a session with content conflicts or cases requiring manual decisions. Set `session` to the absolute directory printed by `merge`.

```bash
session='/absolute/path/to/the/reported/session'
```

If the report lists `CONFLICT`, open the session in VS Code.

```bash
dev-sandbox resolve "$repo" "$session"
```

Use `merge-report.json` and `files/` to review candidate results. Handle `MANUAL` cases yourself and verify `DELETE` entries before removing anything from the original. Check the original for changes since the session was generated. Copy approved candidates to their corresponding paths and apply approved deletions manually. For the greeting example, if the report marks `hello.rb` as `READY` and you approve the candidate, copy it back and inspect the unstaged change.

```bash
cp -- "$session/files/hello.rb" "$repo/hello.rb"
git -C "$repo" diff -- hello.rb
```

Run the project's tests on the original before accepting changes. Consult the [merge behavior](docs/ARCHITECTURE.md#review-diff-merge-and-resolve) for result meanings.

## Commands

| Command | Behavior |
| --- | --- |
| `dev-sandbox prepare <repo>` | Creates a filtered workspace, baseline and manifest. |
| `dev-sandbox readonly <repo> [runtime]` | Starts Codex with a read-only mount at `/workspace`. |
| `dev-sandbox dev <repo> [runtime]` | Starts Codex with a writable mount at `/workspace`, subject to host permissions. |
| `dev-sandbox status <readonly\|dev> <repo> [runtime]` | Audits the prepared workspace and inspects rendered Compose configuration without starting containers. |
| `dev-sandbox check <readonly\|dev> <repo> [runtime]` | Audits the workspace, inspects configuration and runs checks in a one-off container without starting dependencies. |
| `dev-sandbox review <repo>` | Summarizes workspace changes, original-only changes, conflicts and unsafe entries. |
| `dev-sandbox diff <repo>` | Prints bounded unified diffs for eligible changes and reports paths it blocks. |
| `dev-sandbox merge <repo>` | Creates a new local merge session with a report and candidate files. |
| `dev-sandbox resolve <repo> <session>` | Opens content conflicts from a merge session in VS Code, one at a time. |

## Project exclusions

Create `.dev-sandboxignore` at the original repository root. It can be untracked and is excluded from the prepared copy. Use UTF-8 and keep the file at or below 64 KiB.

```gitignore
# Root-level directory and its contents
/fixtures/private/
# Matching files directly under the root-level reports directory
reports/*.json
# Matching names at any depth, including the root
**/internal-notes.md
```

Rules are case-sensitive and root-relative, even without a leading slash. A trailing slash targets descendants of matching directories. `*` matches within one path component and `**`, as a whole component, matches zero or more components. Blank lines and lines starting with `#` after trimming are ignored. `?` and brackets are literal. Negation, backslash escapes and empty, `.` or `..` components are invalid. These rules only add exclusions to the built-in filters.

Set exclusions before preparation. The launcher audits against the current rules and blocks any still-present file newly excluded by them. Review, diff and merge also read the current rules and omit matching paths. Removing a rule leaves the prepared copy as it was. Prepare a fresh copy to recalculate its contents.

## Local files and replacement

Under `~/.local/share/dev-sandbox/`, the project ID is the original root's basename followed by a hyphen and 12 hexadecimal characters derived from the resolved path.

| Path | Contents |
| --- | --- |
| `workspaces/<project-id>/` | Copy mounted at `/workspace`. |
| `baselines/<project-id>/` | Text snapshot from preparation. |
| `manifests/<project-id>.json` | Source and copy paths, hashes and excluded paths. |
| `merges/<project-id>/session-*/` | Independent merge sessions containing a report and a `files/` directory. |

A repeated `prepare` refuses to replace an existing workspace or baseline. A leftover manifest also blocks publishing a new manifest. There is no cleanup command. To replace a copy safely:

1. End agent and editor sessions. Preserve any edits you want to keep and complete review or merge while the old inputs are available.
2. Verify the exact workspace and baseline paths from preparation and the matching manifest name. Move those three entries to a private archive or remove those exact entries once you have verified the backup. Avoid wildcard deletion.
3. Archive any old merge sessions you want to retain. Their reports reference live input paths, which a new preparation at the same location can reuse. Finish using `resolve` on old sessions before replacement.
4. Run `prepare` again and review the new copy. The Docker-managed Codex volume has a separate lifecycle.

## Tests and troubleshooting

Run the automated suite from the dev-sandbox checkout.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The suite uses temporary Git repositories and a Docker stub for launcher tests. Use `status` and `check` on a disposable project for Compose and container validation. See [validation scope](docs/SECURITY.md#validation-and-evidence).

- **Invalid repository or missing workspace.** Pass the original Git root, then run `prepare`. Moving the root changes its identity.
- **Image missing or Compose unavailable.** Verify `docker compose version`, the active Docker context and daemon access. Build the required runtime and proxy from the dev-sandbox checkout.
- **Audit fails after edits or a rule change.** Read the reported path and reason. Preserve desired edits, then remove inadmissible entries from the prepared copy or replace it using the procedure above.
- **Container cannot read or write the copy.** Prepared directories and files use owner-only permissions (`0700` and `0600`). Compare the numeric UID and GID of the host account and prepared paths with the effective IDs of the container's `node` user as mapped onto the host. Account for rootless Docker or user-namespace remapping. A matching group alone grants no access under these modes. If the mapped user differs from the workspace owner, ask the Docker administrator to validate an approved identity mapping or supported host setup that preserves owner-only access. Keep the original repository and storage permissions private.
- **Direct network check fails with a timeout.** The probe requires `ENETUNREACH`. A timeout leaves the route check inconclusive. Investigate the Docker network setup.
- **Sign-in or dependencies fail.** Confirm that the required destinations fit the proxy policy. `check` does not validate sign-in or proxy connectivity.
- **VS Code fails to open a conflict.** Check that `code` is on `PATH` and that the original, workspace, baseline and session files still exist. Regenerate the merge if its inputs have changed.
