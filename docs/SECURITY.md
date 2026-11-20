# dev-sandbox v1 security model

## Objectives and threat model

dev-sandbox reduces accidental disclosure of local files and accidental writes to the original working tree. Its Docker configuration constrains an agent process, including one that behaves unexpectedly. Treat code and instructions inside the workspace as untrusted, and assume that a process in the Codex container can read any workspace bytes accessible to its user and use any writable mount and reachable service.

| Asset | Threat and surface | Control and residual exposure |
| --- | --- | --- |
| Original working tree, Git history and local credentials | A mistaken agent command, untrusted project instructions or a malicious tool reads or modifies host files. | The launcher mounts a filtered copy. Host compromise, unsafe configuration and later manual application can affect the original. |
| Eligible code and data | A user includes proprietary material or an agent sends it to the AI service. | Preparation reduces the files available to the process. The permitted service connection can still carry code and prompts. |
| Baseline, manifest and merge results | A local process replaces inputs, poisons a candidate or reads retained artifacts. | Local permissions and consistency checks cover selected paths. The same host user can change these files and associated hashes. |
| Codex credentials | Code in the container reads or modifies persisted authentication state. | State lives in a project-scoped volume accessible to the container in both modes. Its contents survive agent exits. |
| Host and network services | A process attempts unauthorized connections, resource exhaustion or container escape. | Networks, proxy rules, process limits and container restrictions reduce exposure. Docker, the kernel, the images and effective host configuration remain trusted. |

The model assumes a trusted launcher checkout, host user, Docker daemon, kernel and image supply chain. It assumes the user invokes the provided launcher and protects the storage directories. A host process running as the same user can access the original and alter the workspace, baseline or manifest. Root, an actor with Docker control, or a compromised daemon or kernel can bypass the project's controls. The repository, tool and model instructions can influence what the agent does within the container. Code review and data classification remain necessary even in `readonly`.

## Privileges and writable state

The Codex service runs as the image's `node` user, with a read-only container root, `cap_drop: [ALL]`, `no-new-privileges:true` and a PID limit of 128. The original tree, host home and Docker socket are absent from the declared mounts. `readonly` makes the workspace bind read-only. Both modes still provide writable `/tmp` and `CODEX_HOME`; `dev` also permits workspace writes subject to filesystem permissions. The configuration sets no explicit CPU or memory limit. The Codex invocation uses `danger-full-access`, with the Docker boundary supplying isolation. The approval setting `on-request` supplies no additional filesystem boundary.

Treat `codex_home` as credential-bearing storage. Processes in the Codex container can potentially read authentication material, change configuration or leave state for later sessions. Docker administrators can access volumes. The project implements no credential broker, token redaction or rotation, and replacement of workspace files does not reset the volume.

## Network policy

The Codex service attaches only to `codex_internal`, an internal Docker network. Squid listens on port 3128 and also attaches to the egress bridge. Compose configures uppercase and lowercase HTTP and HTTPS proxy variables and exempts `localhost` and `127.0.0.1` through `NO_PROXY` and `no_proxy`. These variables guide clients. The Docker network and the proxy ACLs supply enforcement.

Squid denies the configured private destination ranges first, denies methods other than CONNECT, denies ports other than 443, allows matching `.openai.com` and `.chatgpt.com` destination domains, then denies all other requests. The private destination list covers IPv4 RFC 1918 ranges, loopback, link-local and shared address space, plus IPv6 loopback, unique-local and link-local ranges. It is the list in [squid.conf](../proxy/squid.conf), rather than a general classification of every special-use address. The proxy has no published host port. Its logs use tmpfs and the project does not provide durable network audit records.

CONNECT permits a tunnel to a destination. The allowlist governs where Squid accepts tunnels. Data loss prevention (DLP) requires controls over what information can be transferred. Squid performs no TLS interception or inspection of request paths, prompts or payloads, so code excerpts and other workspace data can leave through an allowed tunnel. Authorization for the tenant, account and data transfer must be established separately. Builds use Docker's build network to obtain system and npm dependencies and fall outside the runtime proxy policy. The AI service's retention, processing and data-use terms must be assessed for the actual corporate account and configuration.

## File selection and pre-launch audit

Preparation copies Git-index paths from working-tree bytes. Case-insensitive mandatory name rules exclude common metadata, credential and data filenames. A repository's optional `.dev-sandboxignore` adds exclusions, including for proprietary source and internal documentation that pass the mandatory rules. The rules apply only within the project's file workflow. They do not scan file contents for sensitive business information or prevent a running agent from creating a new matching path in `dev`.

The ignore file must be regular, single-linked, at most 64 KiB, and valid UTF-8 with valid rules. Missing rules are allowed. Preparation skips files matched by either exclusion layer before scanning their contents. Its secret detection uses a fixed set of expressions for private-key headers, selected AWS and service-token patterns, and certain credential assignments. It can miss secrets and flag innocent text. Audit reuses those checks on the current workspace, including eligible files created after preparation. Audit does not require the current copy to have the original hashes.

Before any launcher Docker call, the locator checks the original Git root, the expected workspace and baseline association, and ownership and permissions of selected storage paths and the manifest. Audit checks the workspace entries for unsafe paths and file types, forbidden names, current custom exclusions, UTF-8 and NUL, known secrets and the size limits. Bash then searches for selected sensitive filename patterns as an additional check. The audit is a scan of the prepared tree at a point in time. It is neither a continuous write monitor nor a complete filesystem and mount inventory. For example, ordinary directories are traversed, and the content checks apply to files.

## Links, races and integrity

Descriptor-based reads open components relative to directory descriptors with `O_NOFOLLOW`, then require a regular file and enforce a byte limit. Relative path validation rejects traversal, absolute paths, backslashes, NUL and empty components where applicable. Preparation skips identified symbolic links and rejects multiple hard links in otherwise eligible source files. The ignore-file reader and pre-launch audit also require a single hard link. Review, diff and merge use the shared reader without requiring a single link. `resolve` uses path-based symlink and regular-file checks before passing filenames to VS Code. Checks differ by command.

An inspected file can change during reading or after audit. The locator, audit, Bash scan and Docker mount are separate operations, and Compose receives a path rather than a sealed snapshot. Changes by the host user or another process can invalidate earlier checks. In `dev`, the agent can also modify the mounted copy after startup. SHA-256 detects changes relative to recorded values where checked, but the manifest is unsigned and the host owner can alter both values and files. The tool has no locking or atomic snapshot across preparation, audit, merge and application.

## Original integrity and human review

The launcher mounts the prepared copy. The review and merge commands read the original and save proposed changes separately. Review current diffs and merge artifacts before copying any results back. A clean three-way merge can still contain unsafe logic, introduced secrets or incorrect changes. Inspect new files and proposed deletions, check for working-tree changes since merge, and run project tests before accepting the results. VS Code opens the original, workspace and baseline as merge inputs, with the session artifact as the result. The editor receives live paths, and the tool does not revalidate their hashes before opening them.

## Operational Security Requirements

Before corporate deployment, IT, security and repository owners should agree on the following prerequisites. These are deployment requirements for the organization to establish.

- Approve eligible repositories and data classes, the specific AI service account, and its processing and retention terms. Review the prepared file set before granting access and determine whether separate DLP controls are required.
- Use managed Linux hosts with approved Docker configuration, restricted administrative access and reviewed image sources. Verify bind-mount identity mapping, filesystem protection and limits on the target hosts.
- Validate runtime egress, effective Squid rules and sign-in with synthetic data. Review build-time network access separately and repeat checks when the host, images or network policy change.
- Define access, retention and disposal for workspace copies, baselines, manifests, merge outputs and the Codex volume. Assign ownership for credential revocation, incident reporting and containment.
- Require human review before incorporating candidate files or deletions. Keep the repository's existing testing and approval process in place.

## Validation and evidence

| Procedure | What it establishes | Coverage boundary |
| --- | --- | --- |
| Python suite in [tests](../tests) | Exercises preparation and no-overwrite behavior, manifest creation, working-tree comparison, custom exclusions, unsafe reads and links, blocked diff output for covered patterns, baseline mismatch, merge cases and independent sessions. | Tests use disposable local fixtures. Launcher tests stub Docker and verify the prepared path, mode and failures before Docker calls. |
| `dev-sandbox status <mode> <repo> [runtime]` | Audits the copy and checks selected fields in the actual rendered Compose configuration. | Configuration inspection neither starts a container nor checks live mounts, routes or Squid. |
| `dev-sandbox check <mode> <repo> [runtime]` | Uses a one-off container to check workspace listing, expected temporary file creation and cleanup, a write attempt under `/etc`, and direct TCP reachability to `1.1.1.1:443`. | Only `ENETUNREACH` passes the direct-network probe. The check uses `--no-deps`, covers one destination and performs no proxy or authentication tests. |

For deployment validation, use a synthetic Git repository, test both modes on the intended Docker host, and confirm that the original is unaffected by test edits in the prepared copy. Separately validate live Squid behavior with the proxy running, using harmless requests for allowed and disallowed domains, methods and ports, and controlled private destinations. Verify the approved account's sign-in and service reachability using synthetic code. Record the platform, image and versions tested. These procedures are proposed checks for deployment and have no bearing on container-escape resistance or data-handling commitments.
