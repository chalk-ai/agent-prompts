# Durable Agent Workspace

Use this mode for coding agents or other interactive work that should run for hours or days and be reattached from another terminal.

## 1. Confirm the control-plane context

Use one CLI binary and one authentication profile throughout the run. Confirm that the identity has access to the intended environment and the permissions required for sandboxes and any secrets.

```bash
chalk whoami
chalk sandbox list
```

When switching among production, staging, customer, or local control planes, pass the same explicit global API host, auth-config, scope, and environment arguments to every command. Do not infer the active environment from a browser tab.

## 2. Prepare a host pool deliberately

Host-compute sandboxes run on host pools. Configure them in the dashboard:

- Cluster scope: **Clusters → selected cluster → Settings → Compute Host Pools**.
- Environment scope: **Environment → Settings → Compute → Host Pools**.

Cluster pools take precedence: if the cluster has any configured pool, environment-scoped pools are ignored. Prefer a dedicated pool when an experiment needs isolation from unrelated maintenance or workloads.

Choose on-demand capacity (`minimum hosts = 0`) for intermittent dogfooding, or fixed capacity (`minimum hosts = maximum hosts`) when cold-start latency matters. The idle timeout begins only after the last sandbox leaves the pool; it is not a sandbox lifetime.

Each sandbox must fit on one host. Leave room for host overhead: a `32Gi` sandbox may not fit a `32Gi` host. Pool maximum hosts limits concurrent capacity, and a full pool reports that all registered capacity is allocated.

Maintenance is currently disruptive to active sandboxes. A dedicated pool limits the blast radius but does not make maintenance non-disruptive. Before a long run:

1. Check the pool's maintenance schedule and recent maintenance.
2. Avoid the experiment window or arrange a safe window.
3. Never use **Run maintenance now** without accepting that active work on that pool may terminate.

Target the exact pool when supported:

```bash
chalk sandbox create \
  --name "$SANDBOX_NAME" \
  --image node:22-bookworm \
  --compute-class host \
  --host-pool "$HOST_POOL_ID" \
  --cpu 4 \
  --memory 24Gi \
  --lifetime 24h \
  --chalk-identity \
  --tags "owner=$OWNER,purpose=agent-dev"
```

If `--host-pool` is unavailable, update the CLI or use a supported SDK rather than claiming scheduler selection proves placement. After creation, inspect the sandbox and dashboard placement before scaling the test.

## 3. Size and authorize the workspace

Start with the smallest size that has realistic headroom, then use observed peaks to adjust. `2Gi` can be too small for a coding agent plus repository tooling; exit code `137` is consistent with a SIGKILL and commonly indicates an OOM kill, but verify platform evidence before asserting the cause.

Use workload identity for Chalk API access. Add only the external credentials and network hosts the task needs. Prefer an image that already contains Git, tmux, and the agent CLI; otherwise bootstrap once and record how long it takes.

Do not assume `--restart` preserves the workspace. A restarted sandbox can return from its image with an empty ephemeral filesystem. Use it only when bootstrap is idempotent and all required state is durable.

## 4. Start durable work in tmux

Clone or restore the repository, create a task branch, and push an early checkpoint. Then start the agent in a named tmux session:

```bash
chalk sandbox exec --name "$SANDBOX_NAME" -- \
  tmux new-session -d -s claude -c /workspace/repo claude

chalk sandbox attach "$SANDBOX_NAME" --tmux --session claude
```

Detach with `Ctrl-B`, then `D`. Reattach with the same `chalk sandbox attach` command. Omitting the sandbox opens the interactive sandbox picker; omitting `--session` discovers existing tmux sessions.

If a deployed Chalk function provisions the workspace, the higher-level flow is:

```bash
chalk sandbox start PROVISIONING_FUNCTION --tmux -- claude
```

`sandbox start` provisions through the function, while `sandbox attach` only reconnects; attach never starts a missing agent.

Distinguish three concepts:

- **Tmux reattachment** reconnects to a live tmux server and child process in a running sandbox.
- **Agent conversation resume** is the agent CLI's own saved-session mechanism.
- **Sandbox snapshot/resume** is infrastructure restoration and must not be assumed to preserve tmux or child processes unless an end-to-end test proves it for the deployed version.

## 5. Persist and observe

- Push commits or branches regularly. A sandbox branch that exists only on its ephemeral disk is not durable.
- Write logs, checkpoints, and datasets that must survive termination to a mounted Chalk Volume or external store.
- Inspect CPU and memory utilization and their peaks. High memory relative to the limit is more actionable than raw usage alone.
- Generate a lightweight heartbeat and an outbound request when testing liveness or access logs, but keep the interval modest.
- Treat live terminal logs as diagnostic, not durable storage. Capture important evidence before termination.

Before leaving the task unattended, prove that:

1. The sandbox is running on the intended pool.
2. The repository and remote branch are correct.
3. The agent is inside tmux.
4. Detach and reattach preserve the screen and process.
5. Required network calls and Chalk tool calls succeed.
6. The lifetime covers the planned run with cleanup margin.

## 6. Scale only after a canary

For fleet testing, run one canary through the entire create → work → detach → reattach → terminate path. Then run a small batch before the full fleet.

- Give every sandbox deterministic tags such as owner, experiment, and replica.
- Confirm per-host fit and total pool capacity before launch.
- Alert on terminal status and stalled heartbeat, not merely on local client disconnect.
- Keep the monitor independent of the sandbox when possible. A monitor on a laptop is useful for dogfooding but is not reliable week-long coverage if the laptop sleeps.
- A large fleet or week-long lifetime is a cost-bearing operation. Require an explicit count, lifetime, environment, pool, and cleanup plan.
