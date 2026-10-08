---
name: chalk-sandboxes
description: Use when creating or operating Chalk sandboxes for one-off commands or durable agent development, including host-pool placement, lifetimes, tmux reattachment, network access, GitHub PR artifacts, observability, and failure diagnosis.
---

# Chalk Sandboxes

Use public Chalk documentation and the installed CLI as the source of truth. Before relying on a less common flag, inspect `chalk sandbox create --help` and `chalk sandbox attach --help`.

## Choose the operating mode first

1. **One-off execution** — one command or short interactive investigation, with automatic cleanup. Read [references/one-off-execution.md](references/one-off-execution.md).
2. **Durable agent workspace** — work lasting hours or days that must survive local disconnects and support reattachment. Read [references/durable-agent-workspace.md](references/durable-agent-workspace.md).

Read [references/artifacts.md](references/artifacts.md) when GitHub pull requests should appear as sandbox artifacts. Read [references/troubleshooting.md](references/troubleshooting.md) when creation, scheduling, execution, attachment, or detection fails.

Do not blend the two modes accidentally. A one-off sandbox should normally use `--rm`; a retained sandbox should have an explicit `--lifetime`, persistence plan, and cleanup owner.

## Invariants

- Keep API host, authentication config, scope, and environment consistent across login, create, inspect, attach, and terminate commands. Confirm identity and environment before creating resources.
- Pass credentials through `--secret-from-chalk`, `--secret-from-integration`, or `--secret-from-local-env`; do not place secret values in command lines, prompts, tags, or source control.
- Treat the sandbox filesystem as ephemeral. Persist code through commits and pushes, and persist non-code state through a Chalk Volume or another durable store.
- Grant only the network destinations the task requires. Hostname rules are exact; `github.com` and `api.github.com` are separate hosts.
- Do not run host-pool maintenance, change environment-wide flags, or create a large fleet unless the user explicitly asked for that operation and understands its blast radius and cost.
- Record the sandbox name or ID, environment, host-pool ID, requested resources, lifetime, and purpose so another operator can inspect or terminate it.

## Verify the result

After creation, use `chalk sandbox get --name NAME` or `chalk sandbox get --id ID`. For retained workspaces, prove detach and reattach once before leaving important work unattended. When finished, terminate the sandbox explicitly rather than waiting for its lifetime if it is no longer needed.

Public references:

- [Sandbox](https://docs.chalk.ai/docs/compute/sandbox)
- [Host Pools](https://docs.chalk.ai/docs/compute/host-pools)
