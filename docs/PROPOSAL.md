# Proposal for corporate adoption of dev-sandbox v1

## Executive Summary

dev-sandbox gives development teams a repeatable way to try a coding agent without giving it the original working tree. Developers can review proposed edits before those edits enter existing testing and approval workflows. IT and security can use the pilot to assess operational effort and determine which repositories, accounts and data are suitable for this workflow. Approved code may still reach the AI service, so the account and permitted data must be reviewed before use.

## Decision requested

Approve a time-boxed pilot on synthetic or explicitly approved low-sensitivity repositories. Confirm the [operational security requirements](SECURITY.md#operational-security-requirements) before use and continue to use the organization's existing code review and release process. Expansion to more sensitive repositories requires a separate decision by IT, security and the data owner.

## Business context

Coding agents can inspect project files, run commands and propose changes. Running one directly in a sensitive working tree exposes the files and tools that its process can access. A mistaken instruction, untrusted project content or compromised dependency can lead to unauthorized edits, disclosure of local credentials or transfer of proprietary code. Teams also need a way to inspect changes before they enter the normal delivery pipeline.

dev-sandbox gives Codex a filtered working copy on a Linux host and generates local merge candidates for human review. Developers can start in read-only mode, switch to a writable copy when needed, then inspect and manually incorporate accepted changes. The approach reduces the set of local files available to the agent and keeps candidate edits out of the original during the launcher workflow. See the [security model](SECURITY.md) for the conditions that make those protections effective.

The pilot should account for outbound transfer to the AI service, persistent Codex credentials, and the limits of name and content filters. The copy can omit inputs required for tests, omit Git metadata and prevent dependency downloads under the runtime network policy. Manual integration adds work and can surface conflicts when the original changes during a session.

## Infrastructure and operations

- **Workstations.** Provide managed Linux hosts with an approved Docker configuration, Compose, Git and Python. Provide VS Code where developers will resolve content conflicts.
- **Images.** Assign ownership for rebuilding and validating the runtime and Squid images, reviewing base and dependency changes, and distributing approved image versions or build inputs. Validate container UID mapping and writable paths on supported hosts.
- **Network and identity.** Approve destinations needed during builds separately from runtime egress. Validate runtime proxy behavior and sign-in with the intended Codex account. Establish approved identities, credential handling and access revocation.
- **Data and support.** Define retention and disposal for copies, baselines, manifests, merge outputs and Codex volumes. Establish an incident reporting path and a support owner. Keep operational records in approved systems because the local Squid logs are ephemeral.

## Pilot plan and measures

1. Select a small group of volunteers, representative tasks and eligible repositories. Record baseline effort for comparable tasks and establish data classifications, approved account settings and stopping criteria.
2. Run the local suite and validate both Compose modes, live proxy behavior and authentication on the managed host configuration. Use controlled fixtures for exclusion tests and confirm that the original stays unchanged during agent work.
3. Start with inspection tasks in `readonly`. Progress to contained edits in `dev` after reviewing the prepared files. Require developers to review candidate changes, apply them manually and use existing Git, testing and peer-review practices.
4. Review outcomes with IT, security and the participating teams. Pause on suspected unauthorized disclosure or an isolation failure and route it through the existing incident process.

| Measure | Evidence for the decision |
| --- | --- |
| Protection of the original and data handling | Logs of controlled tests, observed writes limited to expected locations, documented proxy checks and review of incidents or exceptions. |
| Developer effort | Time to first working session, time from preparation through review, and effort spent resolving conflicts or missing dependencies. |
| Quality of candidate changes | Test and peer-review outcomes, acceptance rates and rework compared with the baseline tasks. |
| Operational fit | Build and login reliability, compatibility across supported hosts, support effort and ability to remove retained data. |

Agree on thresholds before the pilot. Any expansion should require satisfactory controlled checks, explicit approval of service data handling, and a documented support and incident response arrangement.

## Responsibilities

| Owner | Responsibilities |
| --- | --- |
| IT and platform engineering | Manage supported hosts, image provenance and updates, Docker configuration, access, retention tooling and support. |
| Security, privacy and data owners | Define eligible data, approve provider terms and configurations, review network and credential controls, evaluate exceptions and lead incident response. |
| Developers and repository maintainers | Review exclusions and the prepared copy, use approved accounts, inspect changes, test and obtain normal repository approvals, and report suspected exposure or unexpected behavior. |

## Alternatives to evaluate

- **Docker Sandboxes.** Docker describes its local Sandboxes as running agents in microVMs with separate kernels. Assess this boundary alongside host prerequisites, workspace exposure, credential handling, network policy and integration with existing review workflows. Verify the exact deployment mode and approved configuration in a pilot. [Docker Sandboxes isolation documentation](https://docs.docker.com/ai/sandboxes/security/isolation)
- **Managed corporate agent environments.** Evaluate approved virtual desktops, hosted development environments or managed agent services against the same controls. Confirm each candidate's workspace access, data residency, retention, tenant controls, network policy, auditability, identity integration and cost. Product-specific capabilities and contractual terms are pending research for shortlisted providers.
- **Existing development process without agent execution.** Retain this option for repositories outside the approved data classification or wherever the required controls cannot be demonstrated.

## Proposed enhancements

A later version could support short-lived workspaces based on an explicit file selection. A developer would review the proposed file list before copying any contents. Mandatory exclusions and content checks would still apply, and the design would need a way to preserve reviewed results before expiry. Evaluate this alongside workspace locking or snapshotting, scoped credential delivery, and managed retention and audit controls. These capabilities are proposed future work.
